import re
from sqlalchemy import insert, select, update, or_

from .core import DomainError, uid
from .db import messages, memories, sources, one, all_rows


class Memory:
    def __init__(self, db, identity, clock):
        self.db, self.identity, self.clock = db, identity, clock

    async def record(self, actor, message_id, author_id, name, content, sent_at, role="user"):
        if not content:
            return None
        conversation = await self.identity.conversation(actor)
        async with self.db.write() as s:
            row = await one(s, select(messages).where(messages.c.discord_id == str(message_id)))
            if row:
                if row["conversation_id"] != conversation:
                    raise DomainError("Scope pesan tidak cocok.")
                if row["deleted_at"]:
                    return None
                if row["content"] != content:
                    await self._invalidate(s, row["id"])
                    await s.execute(
                        update(messages).where(messages.c.id == row["id"]).values(content=content)
                    )
                return row["id"]
            mid = uid()
            await s.execute(
                insert(messages).values(
                    id=mid,
                    conversation_id=conversation,
                    author_id=str(author_id),
                    author_name=name[:100],
                    discord_id=str(message_id),
                    role=role,
                    content=content,
                    sent_at=sent_at,
                    deleted_at=None,
                )
            )
            return mid

    async def _invalidate(self, s, mid):
        related = select(sources.c.memory_id).where(sources.c.message_id == mid)
        await s.execute(update(memories).where(memories.c.id.in_(related)).values(status="stale"))

    async def edit_or_delete(self, channel, discord_id, content=None):
        from .db import conversations, bindings

        async with self.db.write() as s:
            row = await one(
                s,
                select(messages)
                .select_from(
                    messages.join(conversations, messages.c.conversation_id == conversations.c.id).join(
                        bindings, conversations.c.binding_id == bindings.c.id
                    )
                )
                .where(messages.c.discord_id == str(discord_id), bindings.c.channel_id == str(channel)),
            )
            if not row:
                return
            await self._invalidate(s, row["id"])
            values = (
                {"content": content}
                if content is not None
                else {"content": "", "deleted_at": self.clock.now()}
            )
            await s.execute(update(messages).where(messages.c.id == row["id"]).values(**values))

    async def context(self, actor, question, before_id, budget=18000):
        conversation = await self.identity.conversation(actor)
        async with self.db.read() as s:
            # Discord snowflakes are decimal strings: compare numeric length first.
            from sqlalchemy import func, and_

            before = str(before_id)
            before_filter = or_(
                func.length(messages.c.discord_id) < len(before),
                and_(func.length(messages.c.discord_id) == len(before), messages.c.discord_id < before),
            )
            base = select(messages).where(
                messages.c.conversation_id == conversation, messages.c.deleted_at.is_(None), before_filter
            )
            recent = await all_rows(
                s, base.order_by(messages.c.sent_at.desc(), messages.c.discord_id.desc()).limit(40)
            )
            words = list(dict.fromkeys(w for w in re.findall(r"[^\W_]+", question.lower()) if len(w) > 3))[:8]
            old = []
            if words:
                old = await all_rows(
                    s,
                    base.where(
                        or_(*[messages.c.content.ilike("%" + w.replace("%", "") + "%") for w in words])
                    )
                    .order_by(messages.c.sent_at.desc())
                    .limit(12),
                )
            facts = await all_rows(
                s,
                select(memories)
                .where(
                    memories.c.conversation_id == conversation,
                    memories.c.status == "active",
                    or_(memories.c.owner_id == actor.membership, memories.c.owner_id.is_(None)),
                )
                .order_by(memories.c.created_at.desc())
                .limit(10),
            )
        selected = {}
        used = 0
        # Interleave so that older matches still have a chance within a small budget.
        candidates = recent[:20] + old + recent[20:]
        for row in candidates:
            if row["id"] in selected:
                continue
            body = row["content"][:3000]
            if used + len(body) + 180 > budget:
                continue
            used += len(body) + 180
            selected[row["id"]] = {k: row[k] for k in ("author_id", "author_name", "role", "sent_at")}
            selected[row["id"]]["content"] = body
        items = sorted(selected.values(), key=lambda r: r["sent_at"])
        fact_texts = []
        for fact in facts:
            if used + len(fact["content"]) + 100 > budget:
                continue
            used += len(fact["content"]) + 100
            fact_texts.append({"kind": fact["kind"], "content": fact["content"]})
        import json

        context = {"messages": items, "memories": fact_texts}
        while len(json.dumps(context, ensure_ascii=False)) > budget:
            if items:
                items.pop(0)
            elif fact_texts:
                fact_texts.pop()
            else:
                break
        return context

    async def remember(self, actor, fact, source_message):
        conversation = await self.identity.conversation(actor)
        if not fact.strip() or len(fact) > 800:
            raise DomainError("Fakta harus berisi 1–800 karakter.")
        async with self.db.write() as s:
            source = await one(
                s,
                select(messages).where(
                    messages.c.discord_id == str(source_message),
                    messages.c.conversation_id == conversation,
                    messages.c.author_id == actor.user,
                    messages.c.deleted_at.is_(None),
                ),
            )
            if not source:
                raise DomainError("Pesan sumber fakta tidak ditemukan.")
            existing = await one(
                s,
                select(memories)
                .join(sources)
                .where(
                    sources.c.message_id == source["id"],
                    memories.c.owner_id == actor.membership,
                    memories.c.kind == "fact",
                ),
            )
            if existing:
                return existing["id"]
            mid = uid()
            await s.execute(
                insert(memories).values(
                    id=mid,
                    conversation_id=conversation,
                    owner_id=actor.membership,
                    kind="fact",
                    content=fact.strip(),
                    status="active",
                    created_at=self.clock.now(),
                )
            )
            await s.execute(insert(sources).values(memory_id=mid, message_id=source["id"]))
            return mid

    async def list_facts(self, actor):
        async with self.db.read() as s:
            return await all_rows(
                s,
                select(memories)
                .where(memories.c.owner_id == actor.membership, memories.c.status == "active")
                .order_by(memories.c.created_at.desc())
                .limit(20),
            )

    async def forget(self, actor, prefix):
        from .actions import resolve_owned

        async with self.db.write() as s:
            row = await resolve_owned(s, memories, prefix, memories.c.owner_id, actor.membership)
            await s.execute(
                update(memories).where(memories.c.id == row["id"]).values(status="forgotten", content="")
            )

    async def summary_candidate(self, actor):
        conversation = await self.identity.conversation(actor)
        async with self.db.read() as s:
            recent = await all_rows(
                s,
                select(messages)
                .where(messages.c.conversation_id == conversation, messages.c.deleted_at.is_(None))
                .order_by(messages.c.sent_at.desc(), messages.c.discord_id.desc())
                .limit(120),
            )
            if len(recent) < 80:
                return None
            current = await one(
                s,
                select(memories)
                .where(
                    memories.c.conversation_id == conversation,
                    memories.c.kind == "summary",
                    memories.c.status == "active",
                )
                .order_by(memories.c.created_at.desc())
                .limit(1),
            )
            # At most one summary per 30 newer messages, with source IDs retained.
            if current and sum(r["sent_at"] > current["created_at"] for r in recent) < 30:
                return None
            return conversation, list(reversed(recent[20:100]))

    async def save_summary(self, conversation, source_rows, summary):
        if not summary.strip():
            return
        async with self.db.write() as s:
            ids = [r["id"] for r in source_rows]
            current = await all_rows(
                s, select(messages).where(messages.c.id.in_(ids), messages.c.deleted_at.is_(None))
            )
            original = {r["id"]: r["content"] for r in source_rows}
            if len(current) != len(ids) or any(original[r["id"]] != r["content"] for r in current):
                return
            await s.execute(
                update(memories)
                .where(
                    memories.c.conversation_id == conversation,
                    memories.c.kind == "summary",
                    memories.c.status == "active",
                )
                .values(status="superseded")
            )
            mid = uid()
            await s.execute(
                insert(memories).values(
                    id=mid,
                    conversation_id=conversation,
                    owner_id=None,
                    kind="summary",
                    content=summary[:3000],
                    status="active",
                    created_at=self.clock.now(),
                )
            )
            await s.execute(insert(sources), [{"memory_id": mid, "message_id": x} for x in ids])
