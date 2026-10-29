import asyncio
import sqlite3
from datetime import datetime
from pathlib import Path


async def import_legacy(app, path, guild, channel):
    path = Path(path).resolve()
    if not path.is_file():
        raise ValueError("Database lama tidak ditemukan.")

    def read():
        db = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
        db.row_factory = sqlite3.Row
        try:
            # Only the selected legacy channel is migrated; no historic actions are replayed.
            return [
                dict(r)
                for r in db.execute("SELECT * FROM messages WHERE channel_id=? ORDER BY id", (int(channel),))
            ]
        finally:
            db.close()

    rows = await asyncio.to_thread(read)
    count = 0
    for row in rows:
        actor = await app.identity.actor(str(guild), str(row["author_id"]), row["author_name"], str(channel))
        ts = int(datetime.fromisoformat(row["created_at"]).timestamp())
        await app.memory.record(
            actor, str(row["id"]), str(row["author_id"]), row["author_name"], row["content"], ts, row["role"]
        )
        count += 1
    return count
