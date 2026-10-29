import os
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    root: Path
    database: Path
    token: str
    channel: str
    api_key: str
    api_base: str
    model: str
    ai_mode: str
    timezone: str
    chat_mode: str
    history_limit: int
    context_chars: int
    cooldown: int
    ai_concurrency: int
    alarm_interval: int
    alarm_max: int
    scheduler_seconds: int

    @classmethod
    def load(cls, require_secrets=True):
        root = Path(os.getenv("ELAINE_ROOT", Path.cwd())).resolve()
        load_dotenv(root / ".env")

        def required(name):
            val = os.getenv(name, "").strip()
            if require_secrets and (not val or val.startswith("ISI_")):
                raise ValueError(f"Isi {name} di .env terlebih dahulu.")
            return val

        def number(name, default, low, high):
            return max(low, min(high, int(os.getenv(name, str(default)))))

        zone = os.getenv("DEFAULT_TIMEZONE", "Asia/Jakarta")
        ZoneInfo(zone)
        channel = required("CHANNEL_ID")
        if require_secrets and not channel.isdigit():
            raise ValueError("CHANNEL_ID harus berupa angka ID Discord.")
        base = os.getenv("AI_BASE_URL", "https://api.naelle-card.my.id/v1").rstrip("/")
        if not base.startswith("https://"):
            raise ValueError("AI_BASE_URL harus HTTPS.")
        mode = os.getenv("AI_MODE", "json")
        chat = os.getenv("CHAT_MODE", "auto")
        if mode not in {"json", "tools"} or chat not in {"auto", "mention_only"}:
            raise ValueError("AI_MODE atau CHAT_MODE tidak valid.")
        path = Path(os.getenv("DB_PATH", "data/elaine_v2.sqlite3"))
        return cls(
            root,
            root / path,
            required("DISCORD_TOKEN"),
            channel,
            required("AI_API_KEY"),
            base,
            required("AI_MODEL"),
            mode,
            zone,
            chat,
            number("HISTORY_SYNC_LIMIT", 100, 0, 1000),
            number("CONTEXT_CHARS", 18000, 4000, 60000),
            number("COOLDOWN_SECONDS", 5, 0, 120),
            number("AI_CONCURRENCY", 2, 1, 8),
            number("ALARM_INTERVAL_SECONDS", 300, 60, 3600),
            number("ALARM_MAX_SENDS", 3, 1, 10),
            number("SCHEDULER_SECONDS", 5, 1, 60),
        )
