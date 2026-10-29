"""Encrypted, atomic AI overrides. Identity checks live in Discord adapter, never AI."""

import asyncio
import ipaddress
import json
import os
import socket
import tempfile
from dataclasses import replace
from pathlib import Path
from urllib.parse import urlsplit

from cryptography.fernet import Fernet, InvalidToken
from .core import DomainError

FIELDS = ("model", "api_base", "api_key", "context_chars", "ai_mode")


def validate_base(value):
    value = value.strip().rstrip("/")
    parts = urlsplit(value)
    if (
        parts.scheme != "https"
        or not parts.hostname
        or parts.username
        or parts.password
        or parts.query
        or parts.fragment
        or parts.port not in (None, 443)
    ):
        raise DomainError("Base URL harus HTTPS publik, tanpa kredensial, query, atau port khusus.")
    host = parts.hostname.lower()
    if host == "localhost" or host.endswith((".localhost", ".local", ".internal")):
        raise DomainError("Base URL harus mengarah ke penyedia API publik.")
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        if not ip.is_global:
            raise DomainError("Alamat jaringan privat tidak boleh digunakan untuk API.")
    return value


async def validate_public_dns(base):
    host = urlsplit(base).hostname
    try:
        addresses = await asyncio.wait_for(
            asyncio.get_running_loop().getaddrinfo(host, 443, type=socket.SOCK_STREAM), 10
        )
    except (OSError, asyncio.TimeoutError):
        raise DomainError("Alamat penyedia API belum dapat ditemukan. Periksa base URL.") from None
    if not addresses or any(not ipaddress.ip_address(x[4][0]).is_global for x in addresses):
        raise DomainError("Base URL harus mengarah ke alamat IP publik.")


class RuntimeConfig:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.path = self.directory / "ai-settings.enc"
        self.key_path = self.directory / "ai-settings.key"
        self.lock = asyncio.Lock()
        self.version = 0

    def _cipher(self):
        # Never replace a lost key for an existing encrypted config.
        if not self.key_path.exists():
            if self.path.exists():
                raise DomainError("Kunci konfigurasi AI hilang. Pulihkan ai-settings.key dari backup.")
            self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            fd = os.open(self.key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as f:
                f.write(Fernet.generate_key())
                f.flush()
                os.fsync(f.fileno())
        return Fernet(self.key_path.read_bytes())

    def load(self, settings):
        if not self.path.exists():
            return settings
        try:
            values = json.loads(self._cipher().decrypt(self.path.read_bytes()))
            if set(values) != set(FIELDS):
                raise ValueError("Unexpected keys")
            return self.candidate(settings, **values)
        except (InvalidToken, ValueError, TypeError, OSError):
            raise DomainError(
                "Konfigurasi AI tersimpan tidak dapat dibaca. Pulihkan konfigurasi dari backup."
            ) from None

    @staticmethod
    def candidate(settings, model, api_base, api_key, context_chars, ai_mode):
        base = validate_base(api_base)
        model = model.strip()
        if not model or len(model) > 200 or any(ord(c) < 32 for c in model):
            raise DomainError("Model harus berisi ID model provider, maksimal 200 karakter.")
        if not api_key.strip():
            if base != settings.api_base.rstrip("/"):
                raise DomainError("Ganti base URL harus disertai API key baru untuk penyedia tersebut.")
            api_key = settings.api_key
        if not api_key or len(api_key) > 2000 or any(ord(c) < 32 for c in api_key):
            raise DomainError("API key kosong atau tidak valid.")
        if not 4000 <= int(context_chars) <= 60000 or ai_mode not in {"json", "tools"}:
            raise DomainError("Context 4000–60000 karakter; mode harus json atau tools.")
        return replace(
            settings,
            model=model,
            api_base=base,
            api_key=api_key,
            context_chars=int(context_chars),
            ai_mode=ai_mode,
        )

    def save(self, settings):
        encrypted = self._cipher().encrypt(json.dumps({k: getattr(settings, k) for k in FIELDS}).encode())
        fd, name = tempfile.mkstemp(prefix=".ai-", dir=self.directory)
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(encrypted)
                f.flush()
                os.fsync(f.fileno())
            os.replace(name, self.path)
        finally:
            if os.path.exists(name):
                os.unlink(name)
        self.version += 1
