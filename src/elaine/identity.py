from sqlalchemy import insert, select, update
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .core import Actor, DomainError, uid
from .db import bindings, conversations, memberships, users, workspaces, one


class Identity:
    def __init__(self, db, default_timezone="Asia/Jakarta"):
        self.db, self.default_timezone = db, default_timezone

    async def actor(self, guild, discord_user, name, channel, admin=False):
        async with self.db.write() as s:
            workspace = await one(s, select(workspaces).where(workspaces.c.guild_id == str(guild)))
            if workspace is None:
                workspace = {"id": uid(), "guild_id": str(guild), "timezone": self.default_timezone}
                await s.execute(insert(workspaces).values(**workspace))
            user = await one(s, select(users).where(users.c.discord_id == str(discord_user)))
            if user is None:
                user = {"id": uid(), "discord_id": str(discord_user), "name": str(name)[:100]}
                await s.execute(insert(users).values(**user))
            member = await one(
                s,
                select(memberships).where(
                    memberships.c.workspace_id == workspace["id"], memberships.c.user_id == user["id"]
                ),
            )
            if member is None:
                member = {
                    "id": uid(),
                    "workspace_id": workspace["id"],
                    "user_id": user["id"],
                    "timezone": self.default_timezone,
                }
                await s.execute(insert(memberships).values(**member))
            binding = await one(
                s,
                select(bindings).where(
                    bindings.c.channel_id == str(channel), bindings.c.workspace_id == workspace["id"]
                ),
            )
            private = bool(binding and binding["private_owner_id"] == member["id"])
            if binding and binding["private_owner_id"] and not private:
                raise DomainError("Channel ini ditetapkan untuk pemilik lain. Gunakan command pribadi.")
            return Actor(
                workspace["id"],
                str(discord_user),
                member["id"],
                str(channel),
                member["timezone"],
                private,
                admin,
            )

    async def bind(self, actor, channel, purpose, mode, private_owner=None):
        if not actor.admin:
            raise DomainError("Pengaturan channel membutuhkan izin Manage Server.")
        if purpose not in {"chat", "reminders", "finance", "admin"} or mode not in {
            "auto",
            "mention_only",
            "off",
        }:
            raise DomainError("Jenis channel atau mode tidak valid.")
        async with self.db.write() as s:
            if private_owner:
                member = await one(
                    s,
                    select(memberships).where(
                        memberships.c.id == private_owner, memberships.c.workspace_id == actor.workspace
                    ),
                )
                if not member:
                    raise DomainError("Pemilik channel tidak valid.")
            current = await one(s, select(bindings).where(bindings.c.channel_id == str(channel)))
            values = dict(
                workspace_id=actor.workspace,
                channel_id=str(channel),
                purpose=purpose,
                reply_mode=mode,
                private_owner_id=private_owner,
            )
            if current:
                if current["workspace_id"] != actor.workspace:
                    raise DomainError("Workspace tidak sesuai.")
                if current["private_owner_id"] != private_owner:
                    # Do not make an existing private transcript public by rebinding it.
                    raise DomainError(
                        "Pemilik/privasi channel lama tidak boleh diubah. Daftarkan channel baru."
                    )
                await s.execute(update(bindings).where(bindings.c.id == current["id"]).values(**values))
            else:
                binding_id = uid()
                await s.execute(insert(bindings).values(id=binding_id, **values))
                await s.execute(insert(conversations).values(id=uid(), binding_id=binding_id))

    async def binding(self, channel):
        async with self.db.read() as s:
            return await one(s, select(bindings).where(bindings.c.channel_id == str(channel)))

    async def timezone(self, actor, name):
        try:
            ZoneInfo(name)
        except (ZoneInfoNotFoundError, ValueError):
            raise DomainError("Timezone tidak dikenal. Contoh: Asia/Jakarta.") from None
        async with self.db.write() as s:
            await s.execute(
                update(memberships).where(memberships.c.id == actor.membership).values(timezone=name)
            )

    async def conversation(self, actor):
        async with self.db.read() as s:
            row = await one(
                s,
                select(conversations.c.id, bindings.c.private_owner_id)
                .join(bindings, conversations.c.binding_id == bindings.c.id)
                .where(bindings.c.workspace_id == actor.workspace, bindings.c.channel_id == actor.channel),
            )
            if not row or (row["private_owner_id"] and row["private_owner_id"] != actor.membership):
                raise DomainError("Channel belum terdaftar atau tidak dapat diakses.")
            return row["id"]
