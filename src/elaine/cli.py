import argparse
import asyncio
import logging
import os
import sqlite3
from pathlib import Path

from alembic import command
from alembic.config import Config

from .config import Settings


def migrate(root, database):
    database.parent.mkdir(parents=True, exist_ok=True)
    cfg = Config(str(root / "alembic.ini"))
    cfg.set_main_option("script_location", str(root / "migrations").replace("%", "%%"))
    cfg.set_main_option("sqlalchemy.url", ("sqlite:///" + str(database)).replace("%", "%%"))
    command.upgrade(cfg, "head")


def backup(database, output):
    if not database.is_file() or output.exists():
        raise ValueError("Database sumber harus ada dan nama backup harus belum digunakan.")
    output.parent.mkdir(parents=True, exist_ok=True)
    source = sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)
    target = sqlite3.connect(output)
    try:
        source.backup(target)
        if target.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise RuntimeError("Pemeriksaan backup gagal")
    finally:
        target.close()
        source.close()


def main():
    parser = argparse.ArgumentParser(description="Elaine Discord assistant v2")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("run")
    sub.add_parser("migrate")
    sub.add_parser("check")
    sub.add_parser("probe-ai")
    b = sub.add_parser("backup")
    b.add_argument("--output", type=Path, required=True)
    old = sub.add_parser("import-legacy")
    old.add_argument("--file", type=Path, required=True)
    old.add_argument("--guild", required=True)
    old.add_argument("--channel", required=True)
    args = parser.parse_args()
    os.umask(0o077)
    settings = Settings.load(require_secrets=args.command in {"run", "check", "probe-ai"})
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    if args.command == "migrate":
        migrate(settings.root, settings.database)
        print("Migration database selesai.")
    elif args.command == "backup":
        backup(settings.database, args.output.resolve())
        print("Backup tersimpan:", args.output)
    elif args.command == "run":
        import fcntl
        from .discord_adapter import ElaineClient

        lock = open(settings.database.parent / ".instance.lock", "w")
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SystemExit("Instance Elaine untuk database ini sudah berjalan.") from None
        client = ElaineClient(settings)
        client.run(settings.token, log_handler=None)
        if client.fatal_error:
            raise SystemExit(1)
    else:
        asyncio.run(async_command(args, settings))


async def async_command(args, settings):
    from .bootstrap import build
    from .core import Actor
    from .legacy import import_legacy

    app, db = build(settings)
    try:
        await db.check()
        if args.command == "check":
            print("Konfigurasi, prompt dan schema database valid. Belum menguji koneksi Discord/API.")
        elif args.command == "probe-ai":
            actor = Actor("probe", "probe", "probe", "probe", settings.timezone)
            p = await app.provider.propose(actor, "Halo Elaine", {}, app.clock.now())
            print("API terhubung; schema valid; jenis respons:", p.kind)
        else:
            actor = await app.identity.actor(args.guild, "0", "import", args.channel, admin=True)
            if not await app.identity.binding(args.channel):
                await app.identity.bind(actor, args.channel, "chat", settings.chat_mode)
            count = await import_legacy(app, args.file, args.guild, args.channel)
            print("Baris riwayat diperiksa/import idempotent:", count)
    finally:
        await app.provider.close()
        await db.close()


if __name__ == "__main__":
    main()
