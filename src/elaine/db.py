from contextlib import asynccontextmanager
from pathlib import Path

from sqlalchemy import (
    JSON,
    Column,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
    CheckConstraint,
    event,
    text,
)
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

metadata = MetaData()


def table(name, *columns, **kw):
    return Table(name, metadata, Column("id", String(32), primary_key=True), *columns, **kw)


workspaces = table(
    "workspaces",
    Column("guild_id", String, unique=True, nullable=False),
    Column("timezone", String, nullable=False),
)
users = table(
    "users", Column("discord_id", String, unique=True, nullable=False), Column("name", String, nullable=False)
)
memberships = table(
    "memberships",
    Column("workspace_id", ForeignKey("workspaces.id"), nullable=False),
    Column("user_id", ForeignKey("users.id"), nullable=False),
    Column("timezone", String, nullable=False),
    UniqueConstraint("workspace_id", "user_id"),
)
bindings = table(
    "channel_bindings",
    Column("workspace_id", ForeignKey("workspaces.id"), nullable=False),
    Column("channel_id", String, unique=True, nullable=False),
    Column("purpose", String, nullable=False),
    Column("reply_mode", String, nullable=False),
    Column("private_owner_id", ForeignKey("memberships.id")),
)
conversations = table(
    "conversations", Column("binding_id", ForeignKey("channel_bindings.id"), unique=True, nullable=False)
)
messages = table(
    "messages",
    Column("conversation_id", ForeignKey("conversations.id"), nullable=False),
    Column("author_id", String, nullable=False),
    Column("author_name", String, nullable=False),
    Column("discord_id", String, unique=True, nullable=False),
    Column("role", String, nullable=False),
    Column("content", Text, nullable=False),
    Column("sent_at", Integer, nullable=False),
    Column("deleted_at", Integer),
)
Index("ix_messages_conversation_time", messages.c.conversation_id, messages.c.sent_at)
memories = table(
    "memory_items",
    Column("conversation_id", ForeignKey("conversations.id"), nullable=False),
    Column("owner_id", ForeignKey("memberships.id")),
    Column("kind", String, nullable=False),
    Column("content", Text, nullable=False),
    Column("status", String, nullable=False),
    Column("created_at", Integer, nullable=False),
)
sources = Table(
    "memory_sources",
    metadata,
    Column("memory_id", ForeignKey("memory_items.id", ondelete="CASCADE"), primary_key=True),
    Column("message_id", ForeignKey("messages.id"), primary_key=True),
)
actions = table(
    "action_requests",
    Column("owner_id", ForeignKey("memberships.id"), nullable=False),
    Column("source_key", String, unique=True, nullable=False),
    Column("kind", String, nullable=False),
    Column("payload", JSON, nullable=False),
    Column("result", JSON),
    Column("status", String, nullable=False),
    Column("expires_at", Integer, nullable=False),
    Column("version", Integer, nullable=False, server_default="1"),
    Column("created_at", Integer, nullable=False),
)
ledgers = table(
    "ledgers",
    Column("owner_id", ForeignKey("memberships.id"), unique=True, nullable=False),
    Column("currency", String, nullable=False),
    Column("timezone", String, nullable=False),
)
categories = table(
    "categories",
    Column("ledger_id", ForeignKey("ledgers.id"), nullable=False),
    Column("name", String, nullable=False),
    Column("kind", String, nullable=False),
    UniqueConstraint("ledger_id", "kind", "name"),
)
transactions = table(
    "transactions",
    Column("ledger_id", ForeignKey("ledgers.id"), nullable=False),
    Column("category_id", ForeignKey("categories.id"), nullable=False),
    Column("action_id", ForeignKey("action_requests.id"), nullable=False),
    Column("item_index", Integer, nullable=False),
    Column("amount", Integer, nullable=False),
    Column("kind", String, nullable=False),
    Column("occurred_on", String(10), nullable=False),
    Column("note", String, nullable=False),
    Column("status", String, nullable=False),
    Column("version", Integer, nullable=False),
    Column("created_at", Integer, nullable=False),
    UniqueConstraint("action_id", "item_index"),
    CheckConstraint("amount > 0"),
    CheckConstraint("kind IN ('income','expense')"),
)
Index("ix_transactions_report", transactions.c.ledger_id, transactions.c.occurred_on, transactions.c.status)
audits = table(
    "audit_events",
    Column("action_id", ForeignKey("action_requests.id")),
    Column("owner_id", ForeignKey("memberships.id"), nullable=False),
    Column("entity_type", String, nullable=False),
    Column("entity_id", String, nullable=False),
    Column("operation", String, nullable=False),
    Column("before_data", JSON),
    Column("after_data", JSON),
    Column("created_at", Integer, nullable=False),
)
reminders = table(
    "reminders",
    Column("owner_id", ForeignKey("memberships.id"), nullable=False),
    Column("action_id", ForeignKey("action_requests.id"), unique=True, nullable=False),
    Column("title", String, nullable=False),
    Column("timezone", String, nullable=False),
    Column("local_time", String(5), nullable=False),
    Column("recurrence", String, nullable=False),
    Column("weekdays", JSON, nullable=False),
    Column("next_due", Integer),
    Column("channel_id", String, nullable=False),
    Column("mode", String, nullable=False),
    Column("max_sends", Integer, nullable=False),
    Column("interval_seconds", Integer, nullable=False),
    Column("status", String, nullable=False),
    Column("version", Integer, nullable=False),
    Column("created_at", Integer, nullable=False),
)
Index("ix_reminders_due", reminders.c.status, reminders.c.next_due)
occurrences = table(
    "reminder_occurrences",
    Column("reminder_id", ForeignKey("reminders.id"), nullable=False),
    Column("scheduled_at", Integer, nullable=False),
    Column("effective_at", Integer, nullable=False),
    Column("series_version", Integer, nullable=False),
    Column("revision", Integer, nullable=False),
    Column("status", String, nullable=False),
    Column("send_count", Integer, nullable=False),
    Column("next_fire", Integer),
    Column("acknowledged_at", Integer),
    Column("completed_at", Integer),
    Column("late", Integer, nullable=False, server_default="0"),
    UniqueConstraint("reminder_id", "scheduled_at", "series_version"),
)
Index("ix_occurrences_fire", occurrences.c.status, occurrences.c.next_fire)
deliveries = table(
    "deliveries",
    Column("occurrence_id", ForeignKey("reminder_occurrences.id"), nullable=False),
    Column("event_key", String, unique=True, nullable=False),
    Column("revision", Integer, nullable=False),
    Column("status", String, nullable=False),
    Column("attempts", Integer, nullable=False),
    Column("next_attempt", Integer, nullable=False),
    Column("lease_until", Integer),
    Column("message_id", String),
    Column("error_code", String),
    Column("created_at", Integer, nullable=False),
)
Index("ix_deliveries_pending", deliveries.c.status, deliveries.c.next_attempt)


class Database:
    def __init__(self, path: Path):
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.engine = create_async_engine(
            "sqlite+aiosqlite:///" + str(self.path), connect_args={"timeout": 15}
        )

        @event.listens_for(self.engine.sync_engine, "connect")
        def setup(connection, _):
            cursor = connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA busy_timeout=15000")
            cursor.close()

        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)

    @asynccontextmanager
    async def read(self):
        async with self.sessions() as session:
            yield session

    @asynccontextmanager
    async def write(self):
        async with self.sessions() as session:
            # SQLite serializes writers. Never await network inside this transaction.
            await session.execute(text("BEGIN IMMEDIATE"))
            try:
                yield session
                await session.commit()
            except BaseException:
                await session.rollback()
                raise

    async def check(self):
        async with self.read() as session:
            version = (await session.execute(text("SELECT version_num FROM alembic_version"))).scalar()
            if version != "0001":
                raise RuntimeError("Versi database berbeda. Jalankan elaine migrate.")

    async def close(self):
        await self.engine.dispose()


async def one(session, statement):
    return (await session.execute(statement)).mappings().first()


async def all_rows(session, statement):
    return [dict(row) for row in (await session.execute(statement)).mappings().all()]
