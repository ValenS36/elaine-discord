from sqlalchemy import insert, select

from .core import DomainError, uid
from .db import actions, audits, one


async def new_action(session, actor, source_key, kind, payload, now, status="pending"):
    source_key = f"{actor.workspace}:{actor.user}:{source_key}"
    existing = await one(session, select(actions).where(actions.c.source_key == source_key))
    if existing:
        if existing["owner_id"] != actor.membership:
            raise DomainError("Permintaan bukan milikmu.")
        return dict(existing)
    row = dict(
        id=uid(),
        owner_id=actor.membership,
        source_key=source_key,
        kind=kind,
        payload=payload,
        result=None,
        status=status,
        expires_at=now + 600,
        version=1,
        created_at=now,
    )
    await session.execute(insert(actions).values(**row))
    return row


async def audit(session, actor, action_id, entity_type, entity_id, operation, now, before=None, after=None):
    await session.execute(
        insert(audits).values(
            id=uid(),
            owner_id=actor.membership,
            action_id=action_id,
            entity_type=entity_type,
            entity_id=entity_id,
            operation=operation,
            before_data=before,
            after_data=after,
            created_at=now,
        )
    )


async def resolve_owned(session, table, prefix, owner_column, owner):
    if not prefix or len(prefix) < 6:
        raise DomainError("Gunakan ID minimal 6 karakter dari daftar.")
    rows = (
        (await session.execute(select(table).where(table.c.id.startswith(prefix), owner_column == owner)))
        .mappings()
        .all()
    )
    if len(rows) != 1:
        raise DomainError("Data tidak ditemukan atau ID belum cukup spesifik.")
    return dict(rows[0])
