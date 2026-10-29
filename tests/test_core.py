import asyncio
from datetime import date
from pathlib import Path
from dataclasses import replace
import json
import httpx
import pytest
from elaine.cli import migrate, backup
from elaine.config import Settings
from elaine.bootstrap import build
from elaine.core import DomainError
from elaine.schemas import parse_amount
from elaine.provider import Provider
from elaine.reminders import local_timestamp

ROOT = Path(__file__).resolve().parents[1]


class Clock:
    value = local_timestamp(date(2026, 10, 28), "10:00", "Asia/Jakarta")

    def now(self):
        return self.value


@pytest.fixture
async def env(tmp_path, monkeypatch):
    for key in ("ALL_PROXY", "all_proxy", "HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("ELAINE_ROOT", str(ROOT))
    settings = replace(
        Settings.load(False),
        database=tmp_path / "test.db",
        token="test",
        channel="123",
        api_key="test",
        model="test",
    )
    migrate(ROOT, settings.database)
    clock = Clock()
    app, db = build(settings, clock)
    a = await app.identity.actor("1", "10", "A", "123", admin=True)
    await app.identity.bind(a, "123", "chat", "auto")
    b = await app.identity.actor("1", "20", "B", "123")
    yield app, db, clock, a, b, settings
    await app.provider.close()
    await db.close()


ITEM = dict(amount=25000, kind="expense", category="Makan", occurred_on="2026-10-28", note="Nasi")
REM = dict(title="Belajar", local_date="2026-10-28", local_time="10:01", mode="alarm", recurrence="none")


@pytest.mark.parametrize(
    "raw,expected",
    [("25 ribu", 25000), ("Rp25.000", 25000), ("25,5 rb", 25500), ("2 jt", 2000000), ("100", 100)],
)
def test_amount(raw, expected):
    assert parse_amount(raw) == expected


@pytest.mark.parametrize("raw", ["0", "-1", "25.5", "1,5", "1e3", "abc"])
def test_bad_amount(raw):
    with pytest.raises(DomainError):
        parse_amount(raw)


async def test_finance_idempotency_and_scope(env):
    app, db, clock, a, b, _ = env
    d = await app.finance.draft(a, [ITEM], "event1")
    assert (await app.finance.draft(a, [ITEM], "event1"))["id"] == d["id"]
    results = await asyncio.gather(*(app.finance.confirm(a, d["id"], 1) for _ in range(3)))
    assert results[0] == results[1] == results[2]
    assert (await app.finance.report(a, "2026-10"))["expense"] == 25000
    assert (await app.finance.report(b, "2026-10"))["expense"] == 0
    with pytest.raises(DomainError):
        await app.finance.confirm(b, d["id"], 1)
    assert len(await app.finance.history(a)) == 1


async def test_finance_edit_void_audit(env):
    app, _, _, a, _, _ = env
    d = await app.finance.draft(a, [ITEM], "e1")
    tx = (await app.finance.confirm(a, d["id"], 1))["ids"][0]
    edit = await app.finance.draft_change(a, tx, "e2", {**ITEM, "amount": 40000})
    stale = await app.finance.draft_change(a, tx, "e3", {**ITEM, "amount": 50000})
    await app.finance.confirm(a, edit["id"], 1)
    with pytest.raises(DomainError):
        await app.finance.confirm(a, stale["id"], 1)
    void = await app.finance.draft_change(a, tx, "e4")
    await app.finance.confirm(a, void["id"], 1)
    assert (await app.finance.report(a, "2026-10"))["expense"] == 0
    detail = await app.finance.detail(a, tx)
    assert detail["status"] == "voided" and len(detail["audit"]) == 3


async def test_draft_versions_expiry_csv(env):
    app, _, clock, a, _, _ = env
    d = await app.finance.draft(a, [{**ITEM, "note": '=HYPERLINK("bad")'}], "e1")
    d = await app.finance.change_draft(a, d["id"], 1, d["payload"]["items"])
    with pytest.raises(DomainError):
        await app.finance.confirm(a, d["id"], 1)
    await app.finance.confirm(a, d["id"], 2)
    assert b"'=HYPERLINK" in await app.finance.export(a, "2026-10")
    e = await app.finance.draft(a, [ITEM], "e2")
    clock.value += 86400
    with pytest.raises(DomainError):
        await app.finance.confirm(a, e["id"], 1)
    assert (await app.finance.report(a, "2026-11"))["count"] == 0


async def test_alarm_restart_ack(env):
    app, db, clock, a, b, settings = env
    r = await app.reminders.create(a, REM, "r1")
    assert (await app.reminders.create(a, REM, "r1"))["id"] == r["id"]
    clock.value += 60
    await app.reminders.tick()
    j = await app.reminders.claim()
    assert j
    await app.reminders.delivered(j["delivery"]["id"], "999")
    await db.close()
    app2, db2 = build(settings, clock)
    try:
        clock.value += 300
        await app2.reminders.tick()
        j = await app2.reminders.claim()
        assert j
        with pytest.raises(DomainError):
            await app2.reminders.occurrence_action(b, j["occurrence"]["id"], "ack", 1)
        await app2.reminders.occurrence_action(a, j["occurrence"]["id"], "ack", 1)
        assert not await app2.reminders.still_valid(j["delivery"]["id"])
        clock.value += 600
        await app2.reminders.tick()
        assert await app2.reminders.claim() is None
    finally:
        await app2.provider.close()
        await db2.close()


async def test_cancel_and_retry(env):
    app, _, clock, a, _, _ = env
    r = await app.reminders.create(a, REM, "r1")
    clock.value += 60
    await app.reminders.tick()
    j = await app.reminders.claim()
    await app.reminders.delivery_failed(j["delivery"]["id"], "network")
    assert await app.reminders.claim() is None
    clock.value += 60
    j = await app.reminders.claim()
    assert j["delivery"]["attempts"] == 2
    await app.reminders.cancel(a, r["id"], "cancel")
    assert not await app.reminders.still_valid(j["delivery"]["id"])


async def test_snooze_and_catchup(env):
    app, _, clock, a, _, _ = env
    await app.reminders.create(a, REM, "r1")
    clock.value += 60
    await app.reminders.tick()
    j = await app.reminders.claim()
    await app.reminders.occurrence_action(a, j["occurrence"]["id"], "snooze", 1)
    assert not await app.reminders.still_valid(j["delivery"]["id"])
    clock.value += 600
    await app.reminders.tick()
    j = await app.reminders.claim()
    assert j and j["occurrence"]["revision"] == 2


async def test_daily_catchup(env):
    app, _, clock, a, _, _ = env
    await app.reminders.create(a, {**REM, "recurrence": "daily"}, "r1")
    clock.value += 3 * 86400 + 60
    await app.reminders.tick()
    j = await app.reminders.claim()
    assert j["occurrence"]["scheduled_at"] == clock.value
    assert await app.reminders.claim() is None


async def test_memory_scope_sources_and_delete(env):
    app, _, clock, a, b, _ = env
    await app.memory.record(a, "101", a.user, "A", "Ingat aku suka kopi", clock.now())
    await app.memory.remember(a, "Suka kopi", "101")
    assert len(await app.memory.list_facts(a)) == 1
    assert not await app.memory.list_facts(b)
    other = await app.identity.actor("1", "10", "A", "456", admin=True)
    await app.identity.bind(other, "456", "chat", "auto")
    assert not (await app.memory.context(other, "kopi", "999"))["messages"]
    await app.memory.edit_or_delete("123", "101", "Suka teh")
    assert not await app.memory.list_facts(a)
    assert (await app.memory.context(a, "teh", "999"))["messages"][0]["content"] == "Suka teh"
    await app.memory.edit_or_delete("123", "101")
    assert not (await app.memory.context(a, "teh", "999"))["messages"]


async def test_provider_schema_and_repair(env):
    _, _, clock, a, _, settings = env
    calls = []

    def handler(request):
        calls.append(request)
        content = "invalid" if len(calls) == 1 else json.dumps({"kind": "chat", "text": "Halo"})
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

    p = Provider(settings, httpx.MockTransport(handler))
    try:
        assert (await p.propose(a, "Halo", {}, clock.now())).text == "Halo"
        assert len(calls) == 2
    finally:
        await p.close()


async def test_command_registration_and_backup(env, tmp_path):
    _, _, _, _, _, settings = env
    from elaine.discord_adapter import ElaineClient

    c = ElaineClient(settings)
    try:
        names = [x.qualified_name for x in c.tree.walk_commands()]
        assert "uang tambah" in names and "ingat buat" in names
        assert not any("gaya" in n for n in names)
        backup(settings.database, tmp_path / "backup.db")
    finally:
        await c.close()


async def test_private_channel_owner_and_schema(env):
    app, db, _, a, b, _ = env
    await db.check()
    await app.identity.bind(a, "888", "finance", "auto", a.membership)
    with pytest.raises(DomainError):
        await app.identity.actor("1", b.user, "B", "888")
    with pytest.raises(DomainError):
        await app.identity.bind(a, "888", "chat", "auto")


async def test_delivery_lease_and_permanent_failure(env):
    app, _, clock, a, _, _ = env
    await app.reminders.create(a, REM, "lease")
    clock.value += 60
    await app.reminders.tick()
    first = await app.reminders.claim()
    assert await app.reminders.claim() is None
    clock.value += 121
    again = await app.reminders.claim()
    assert again["delivery"]["id"] == first["delivery"]["id"]
    await app.reminders.delivery_failed(again["delivery"]["id"], "Forbidden", permanent=True)
    clock.value += 3600
    await app.reminders.tick()
    assert await app.reminders.claim() is None


async def test_provider_tools_and_http_error(env):
    _, _, clock, a, _, settings = env

    def good(request):
        payload = json.loads(request.content)
        assert payload["tool_choice"]["function"]["name"] == "elaine_response"
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "tool_calls": [
                                {
                                    "function": {
                                        "name": "elaine_response",
                                        "arguments": '{"kind":"chat","text":"Hai"}',
                                    }
                                }
                            ]
                        }
                    }
                ]
            },
        )

    p = Provider(replace(settings, ai_mode="tools"), httpx.MockTransport(good))
    try:
        assert (await p.propose(a, "Halo", {}, clock.now())).text == "Hai"
    finally:
        await p.close()
    p = Provider(settings, httpx.MockTransport(lambda r: httpx.Response(401, text="secret internal")))
    try:
        with pytest.raises(DomainError) as e:
            await p.propose(a, "Halo", {}, clock.now())
        assert "401" in str(e.value) and "secret" not in str(e.value)
    finally:
        await p.close()


async def test_summary_invalidated_and_legacy_import(env, tmp_path):
    import sqlite3
    from elaine.legacy import import_legacy

    app, _, clock, a, _, _ = env
    old = tmp_path / "old.db"
    with sqlite3.connect(old) as c:
        c.execute(
            "CREATE TABLE messages (id INTEGER, channel_id INTEGER, author_id INTEGER, author_name TEXT, created_at TEXT, role TEXT, content TEXT)"
        )
        c.execute("INSERT INTO messages VALUES (1001,123,10,'A','2026-10-28T03:00:00+00:00','user','kopi')")
    assert await import_legacy(app, old, "1", "123") == 1
    await import_legacy(app, old, "1", "123")
    assert len((await app.memory.context(a, "kopi", "99999"))["messages"]) == 1
    for i in range(80):
        await app.memory.record(a, str(2000 + i), a.user, "A", f"Pesan {i}", clock.now() + i)
    conversation, rows = await app.memory.summary_candidate(a)
    await app.memory.save_summary(conversation, rows, "Ringkasan singkat")
    assert (await app.memory.context(a, "apa", "99999"))["memories"]
    await app.memory.edit_or_delete("123", rows[0]["discord_id"])
    assert not (await app.memory.context(a, "apa", "99999"))["memories"]


async def test_max_draft_discord_length(env):
    from elaine.finance import describe_draft

    app, _, _, a, _, _ = env
    d = await app.finance.draft(a, [{**ITEM, "category": "x" * 60, "note": "n" * 500}] * 5, "max")
    assert len(describe_draft(d)) <= 1900
