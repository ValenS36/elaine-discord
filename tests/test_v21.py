import asyncio
import json
import os
import sqlite3
from dataclasses import replace
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, MagicMock

import discord
import httpx
import pytest
from alembic.config import Config
from alembic import command

from test_core import ITEM, REM, ROOT
from elaine.core import DomainError
from elaine import presentation as ui
from elaine.cli import migrate
from elaine.views import ActionButton, AISettingsModal
from elaine.runtime_config import RuntimeConfig, validate_base, validate_public_dns
from elaine.reactions import chat_text
from elaine.voice import AlarmAudio, VoiceAlarms
from elaine.schemas import Proposal


def assert_embed(e):
    assert len(e) <= 6000
    assert len(e.title or "") <= 256
    assert len(e.description or "") <= 4096
    assert len(e.fields) <= 25
    for f in e.fields:
        assert 0 < len(f.name) <= 256
        assert 0 < len(f.value) <= 1024
    assert isinstance(e.to_dict(), dict)


def interaction(app, db, actor):
    async def strict_send(**kwargs):
        # Reproduce discord.py Webhook.send's View restriction, which plain mocks miss.
        if "view" in kwargs:
            assert isinstance(kwargs["view"], discord.ui.View), "view=None breaks Webhook.send"
        assert kwargs["ephemeral"]
        assert_embed(kwargs["embed"])
        return NS(edit=AsyncMock())

    client = NS(
        app=app,
        db=db,
        actor=AsyncMock(return_value=actor),
        respond_error=AsyncMock(),
        voice=NS(stop_occurrence=AsyncMock(), stop_reminder=AsyncMock()),
    )
    i = NS(
        id=812,
        client=client,
        user=NS(id=int(actor.user)),
        response=NS(defer=AsyncMock(), send_modal=AsyncMock()),
        followup=NS(send=AsyncMock(side_effect=strict_send)),
        message=NS(edit=AsyncMock()),
    )
    return i


async def test_real_button_ack_then_done(env):
    app, db, clock, a, _, _ = env
    reminder = await app.reminders.create(a, REM, "button")
    clock.value += 60
    await app.reminders.tick()
    job = await app.reminders.claim()
    await app.reminders.delivered(job["delivery"]["id"], "1234")
    oid = job["occurrence"]["id"]
    i = interaction(app, db, a)
    await ActionButton("o", "ack", oid, 1).callback(i)
    assert not i.client.respond_error.called
    assert (await app.reminders.detail(a, reminder["id"]))["status"] == "acknowledged"
    await ActionButton("o", "done", oid, 2).callback(i)
    assert not i.client.respond_error.called
    assert (await app.reminders.detail(a, reminder["id"]))["status"] == "completed"
    assert "view" not in i.followup.send.call_args.kwargs
    i.client.voice.stop_occurrence.assert_awaited_with(oid)
    await ActionButton("o", "done", oid, 2).callback(i)  # idempotent stale double-click
    assert not i.client.respond_error.called


async def test_real_button_snooze_schedules_and_refreshes(env):
    app, db, clock, a, _, _ = env
    await app.reminders.create(a, REM, "button")
    clock.value += 60
    await app.reminders.tick()
    job = await app.reminders.claim()
    await app.reminders.delivered(job["delivery"]["id"], "1234")
    i = interaction(app, db, a)
    await ActionButton("o", "snooze", job["occurrence"]["id"], 1).callback(i)
    assert not i.client.respond_error.called
    i.message.edit.assert_awaited()
    clock.value += 599
    await app.reminders.tick()
    assert await app.reminders.claim() is None
    clock.value += 1
    await app.reminders.tick()
    next_job = await app.reminders.claim()
    assert next_job["occurrence"]["revision"] == 2
    assert next_job["occurrence"]["effective_at"] == clock.now()


async def test_ack_can_snooze_and_completed_rejects_snooze(env):
    app, _, _, a, _, _ = env
    r = await app.reminders.create(a, REM, "r")
    o = (await app.reminders.active_occurrences(a, r["id"]))[0]
    await app.reminders.occurrence_action(a, o["id"], "ack", 1)
    await app.reminders.occurrence_action(a, o["id"], "snooze", 2)
    assert (await app.reminders.detail(a, r["id"]))["status"] == "active"
    await app.reminders.occurrence_action(a, o["id"], "done", 3)
    with pytest.raises(DomainError):
        await app.reminders.occurrence_action(a, o["id"], "snooze", 4)


async def test_recurrent_done_keeps_series(env):
    app, _, _, a, _, _ = env
    r = await app.reminders.create(a, {**REM, "recurrence": "daily"}, "r")
    o = (await app.reminders.active_occurrences(a, r["id"]))[0]
    await app.reminders.occurrence_action(a, o["id"], "ack", 1)
    await app.reminders.occurrence_action(a, o["id"], "done", 2)
    assert (await app.reminders.detail(a, r["id"]))["status"] == "active"


async def test_delete_confirmation_ownership_and_totals(env):
    app, db, _, a, b, _ = env
    d = await app.finance.draft(a, [ITEM], "one")
    tx = (await app.finance.confirm(a, d["id"], 1))["ids"][0]
    with pytest.raises(DomainError):
        await app.finance.draft_delete(b, tx, "bad")
    i = interaction(app, db, a)
    await ActionButton("t", "delete", tx, 1).callback(i)
    assert not i.client.respond_error.called
    assert (await app.finance.report(a, "2026-10"))["expense"] == 25000  # not deleted at preview
    draft = (await app.finance.drafts(a))[0]
    await ActionButton("d", "confirm_delete", draft["id"], 1).callback(i)
    assert not i.client.respond_error.called
    assert (await app.finance.report(a, "2026-10"))["expense"] == 0
    assert not await app.finance.history(a)
    assert tx.encode() not in await app.finance.export(a, "2026-10")
    detail = await app.finance.detail(a, tx)
    assert detail["status"] == "deleted" and len(detail["audit"]) == 2
    result = await app.finance.confirm(a, draft["id"], 1)
    assert result["ids"] == [tx]
    assert len((await app.finance.detail(a, tx))["audit"]) == 2


async def test_delete_stale_confirmation_and_voided_transaction(env):
    app, _, _, a, _, _ = env
    d = await app.finance.draft(a, [ITEM], "one")
    tx = (await app.finance.confirm(a, d["id"], 1))["ids"][0]
    delete = await app.finance.draft_delete(a, tx, "delete1")
    edit = await app.finance.draft_change(a, tx, "edit", {**ITEM, "amount": 50000})
    await app.finance.confirm(a, edit["id"], 1)
    with pytest.raises(DomainError):
        await app.finance.confirm(a, delete["id"], 1)
    void = await app.finance.draft_change(a, tx, "void")
    await app.finance.confirm(a, void["id"], 1)
    delete = await app.finance.draft_delete(a, tx, "delete2")
    await app.finance.confirm(a, delete["id"], 1)
    assert not await app.finance.history(a)


async def test_presentation_extreme_inputs(env):
    app, _, _, a, _, settings = env
    draft = await app.finance.draft(a, [{**ITEM, "note": "*" * 500, "category": "*" * 60}] * 5, "big")
    assert_embed(ui.draft_card(draft))
    result = await app.finance.confirm(a, draft["id"], 1)
    assert_embed(await ui.finance_receipt(app.finance, a, result))
    rows = await app.finance.history(a, limit=5)
    assert_embed(ui.transaction_list(rows, 0))
    assert_embed(ui.report_card(await app.finance.report(a, "2026-10")))
    assert_embed(ui.profile_card(None, a.timezone))
    status = ui.status_card(settings, True, True)
    assert_embed(status)
    text = json.dumps(status.to_dict())
    assert "karakter" in text and "kapasitas token" in text
    for index in range(5):
        await app.reminders.create(a, {**REM, "title": "@everyone" * 27}, str(index))
    assert_embed(ui.reminder_list(await app.reminders.list(a), 0))


async def test_runtime_encryption_atomic_reload_and_endpoint_key(env, tmp_path):
    _, _, _, _, _, settings = env
    store = RuntimeConfig(tmp_path / "config")
    changed = store.candidate(settings, "sonnet-new", "https://example.com/v1", "new-secret", 24000, "json")
    store.save(changed)
    assert b"new-secret" not in store.path.read_bytes()
    assert os.stat(store.path).st_mode & 0o777 == 0o600
    assert os.stat(store.key_path).st_mode & 0o777 == 0o600
    assert store.load(settings).api_key == "new-secret"
    assert store.load(settings).context_chars == 24000
    with pytest.raises(DomainError):
        store.candidate(settings, "m", "https://example.org/v1", "", 18000, "json")
    store.path.write_bytes(b"broken")
    with pytest.raises(DomainError):
        store.load(settings)


@pytest.mark.parametrize(
    "base",
    [
        "http://example.com",
        "https://127.0.0.1",
        "https://[::1]",
        "https://localhost",
        "https://api.internal",
        "https://a:b@example.com",
        "https://example.com?token=secret",
        "https://example.com:999",
    ],
)
def test_config_rejects_unsafe_url(base):
    with pytest.raises(DomainError):
        validate_base(base)


async def test_dns_private_rejected(monkeypatch):
    monkeypatch.setattr(
        asyncio.get_running_loop(), "getaddrinfo", AsyncMock(return_value=[(2, 1, 6, "", ("10.0.0.2", 443))])
    )
    with pytest.raises(DomainError):
        await validate_public_dns("https://api.example.com")


async def test_ai_config_modal_has_no_secret_default_and_owner_gate(env):
    from elaine.discord_adapter import ElaineClient

    _, _, _, _, _, settings = env
    c = ElaineClient(replace(settings, api_key="not-for-display"))
    try:
        modal = AISettingsModal(c)
        assert not modal.key.default
        assert all("not-for-display" not in str(x.to_component_dict()) for x in modal.children)
        c.ai_admin_ids = {10}
        with pytest.raises(DomainError):
            await c.require_ai_admin(NS(guild=object(), user=NS(id=20)))
        await c.require_ai_admin(NS(guild=object(), user=NS(id=10)))
        names = [x.qualified_name for x in c.tree.walk_commands()]
        assert {
            "admin ai",
            "admin ai_tes",
            "suara gabung",
            "suara tes",
            "uang hapus",
            "elaine preferensi",
        } <= set(names)
        assert not any("gaya" in n for n in names)
    finally:
        await c.close()


async def test_reactions_are_separate_and_text_sanitized(env):
    app, _, clock, a, _, _ = env
    from elaine.provider import Provider

    settings = app.provider.settings
    mock = httpx.MockTransport(
        lambda req: httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "kind": "chat",
                                    "text": "*tersenyum* Bagus! 🎉 <:yay:1234> :smile:",
                                    "reaction": "🎉",
                                }
                            )
                        }
                    }
                ]
            },
        )
    )
    p = Provider(settings, mock)
    app.provider = p
    try:
        result = await app.chat(a, "sudah selesai", "999", clock.now())
        assert result.text == "*tersenyum* Bagus!"
        assert result.reaction == "🎉"
        with pytest.raises(ValueError):
            Proposal(kind="chat", reaction="NOT_EMOJI")
        assert chat_text("Nominal 123 jam 10:30 👍") == "Nominal 123 jam 10:30"
    finally:
        await p.close()


def test_audio_frames_and_limit():
    audio = AlarmAudio(0.04)
    assert len(audio.read()) == 3840
    assert len(audio.read()) == 3840
    assert audio.read() == b""


def fake_voice(monkeypatch):
    monkeypatch.setattr("elaine.voice.voice_available", lambda: True)
    guild = NS(id=1, me=object(), voice_client=None)
    channel = MagicMock(spec=discord.VoiceChannel)
    channel.id = 555
    channel.guild = guild
    channel.voice_states = {10: object()}
    channel.permissions_for.return_value = NS(view_channel=True, connect=True, speak=True)
    member = NS(id=10, guild=guild, guild_permissions=NS(manage_guild=False))
    vc = NS(
        channel=channel, is_playing=lambda: False, play=MagicMock(), stop=MagicMock(), disconnect=AsyncMock()
    )
    channel.connect = AsyncMock(return_value=vc)
    guild.get_member = lambda mid: member
    client = NS(
        get_channel=lambda cid: channel,
        voice_clients=[],
        app=NS(reminders=NS(occurrence_valid=AsyncMock(return_value=True))),
    )
    manager = VoiceAlarms(client)
    job = {
        "discord_user_id": "10",
        "reminder": {"id": "r", "voice_channel_id": "555"},
        "occurrence": {"id": "o", "revision": 1},
    }
    return manager, job, guild, channel, vc


async def test_voice_play_disconnect_and_busy(monkeypatch):
    manager, job, guild, channel, vc = fake_voice(monkeypatch)
    vc.play.side_effect = lambda src, after: after(None)
    report = AsyncMock()
    assert manager.schedule(job, guild, report)
    task = manager.active[1][1]
    assert not manager.schedule(job, guild, report)
    await task
    vc.play.assert_called_once()
    vc.disconnect.assert_awaited_once()
    assert not manager.active


async def test_voice_absent_owner_fallback_and_cancel(monkeypatch):
    manager, job, guild, channel, vc = fake_voice(monkeypatch)
    channel.voice_states = {}
    report = AsyncMock()
    manager.schedule(job, guild, report)
    await manager.active[1][1]
    vc.play.assert_not_called()
    assert "Masuk ke voice" in report.call_args.args[0]
    channel.voice_states = {10: object()}
    started = asyncio.Event()
    vc.play.side_effect = lambda src, after: started.set()
    manager.schedule(job, guild, report)
    await asyncio.wait_for(started.wait(), 1)
    await manager.stop_occurrence("o")
    vc.stop.assert_called()
    assert not manager.active


async def test_voice_setting_persistence_and_edit(env):
    app, _, _, a, _, _ = env
    r = await app.reminders.create(a, REM, "voice", voice_channel_id="555")
    assert (await app.reminders.detail(a, r["id"]))["voice_channel_id"] == "555"
    r = await app.reminders.change(a, r["id"], {**REM, "local_time": "10:02"}, "edit")
    assert r["voice_channel_id"] == "555"
    r = await app.reminders.change(a, r["id"], {**REM, "mode": "reminder", "local_time": "10:03"}, "edit2")
    assert r["voice_channel_id"] is None


async def test_migrate_v20_ack_preserves_data(env, tmp_path):
    app, db, clock, a, _, _ = env
    # Build a genuine 0001 schema, then copy rows present before the voice column was introduced.
    path = tmp_path / "v20.db"
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    cfg.set_main_option("sqlalchemy.url", "sqlite:///" + str(path))
    command.upgrade(cfg, "0001")
    r = await app.reminders.create(a, REM, "old")
    o = (await app.reminders.active_occurrences(a, r["id"]))[0]
    await app.reminders.occurrence_action(a, o["id"], "ack", 1)
    with sqlite3.connect(db.path) as src, sqlite3.connect(path) as dst:
        src.row_factory = sqlite3.Row
        for table in (
            "workspaces",
            "users",
            "memberships",
            "action_requests",
            "reminders",
            "reminder_occurrences",
        ):
            columns = [x[1] for x in dst.execute("PRAGMA table_info(" + table + ")")]
            for row in src.execute("SELECT * FROM " + table):
                data = dict(row)
                if table == "reminders":
                    data["status"] = "completed"  # v2.0 bug
                dst.execute(
                    "INSERT INTO "
                    + table
                    + " ("
                    + ",".join(columns)
                    + ") VALUES ("
                    + ",".join("?" for _ in columns)
                    + ")",
                    [data[k] for k in columns],
                )
    migrate(ROOT, path)
    with sqlite3.connect(path) as c:
        assert c.execute("SELECT status FROM reminders").fetchone()[0] == "acknowledged"
        assert c.execute("SELECT title FROM reminders").fetchone()[0] == "Belajar"
        assert c.execute("SELECT voice_channel_id FROM reminders").fetchone()[0] is None


async def test_cancel_acknowledged_disables_old_buttons(env):
    app, _, _, a, _, _ = env
    r = await app.reminders.create(a, REM, "cancel-ack")
    o = (await app.reminders.active_occurrences(a, r["id"]))[0]
    await app.reminders.occurrence_action(a, o["id"], "ack", 1)
    await app.reminders.cancel(a, r["id"], "cancel")
    with pytest.raises(DomainError):
        await app.reminders.occurrence_action(a, o["id"], "done", 2)
    assert (await app.reminders.detail(a, r["id"]))["status"] == "cancelled"


async def test_config_modal_submit_and_restart(env, monkeypatch):
    from elaine.discord_adapter import ElaineClient
    from elaine.bootstrap import build

    _, _, _, _, _, settings = env
    c = ElaineClient(settings)
    try:
        c.ai_admin_ids = {10}
        modal = AISettingsModal(c)
        modal.model._value = "configured-sonnet"
        modal.base._value = "https://example.com/v1"
        modal.key._value = "secret-modal-key"
        modal.context._value = "22000"
        modal.mode._value = "json"
        monkeypatch.setattr("elaine.runtime_config.validate_public_dns", AsyncMock())
        i = NS(
            client=c,
            guild=object(),
            user=NS(id=10),
            response=NS(defer=AsyncMock()),
            followup=NS(send=AsyncMock()),
        )
        await modal.on_submit(i)
        assert c.app.provider.settings.model == "configured-sonnet"
        assert "secret-modal-key" not in str(i.followup.send.call_args)
        app2, db2 = build(settings)
        try:
            assert app2.provider.settings.api_key == "secret-modal-key"
            assert app2.provider.settings.context_chars == 22000
        finally:
            await app2.provider.close()
            await db2.close()
        with pytest.raises(DomainError):  # stale form cannot overwrite newer settings
            await modal.on_submit(i)
    finally:
        await c.close()


async def test_voice_cancellation_during_connect(monkeypatch):
    manager, job, guild, channel, vc = fake_voice(monkeypatch)
    entered = asyncio.Event()

    async def connect(**kwargs):
        guild.voice_client = vc
        entered.set()
        await asyncio.Event().wait()

    channel.connect.side_effect = connect
    manager.schedule(job, guild, AsyncMock())
    await asyncio.wait_for(entered.wait(), 1)
    await manager.stop_occurrence("o")
    vc.disconnect.assert_awaited_once()
    vc.play.assert_not_called()
    assert not manager.active


async def test_absent_owner_does_not_disconnect_existing_voice(monkeypatch):
    manager, job, guild, channel, vc = fake_voice(monkeypatch)
    guild.voice_client = vc
    channel.voice_states = {}
    manager.schedule(job, guild, AsyncMock())
    await manager.active[guild.id][1]
    vc.disconnect.assert_not_called()


async def test_dynamic_buttons_and_modal_limits(env):
    from elaine.views import button_view, ReminderModal, DraftEditModal, MoneyModal
    from elaine.discord_adapter import ElaineClient

    app, _, _, a, _, settings = env
    draft = await app.finance.draft(a, [ITEM], "limits")
    c = ElaineClient(settings)
    try:
        for modal in (
            AISettingsModal(c),
            MoneyModal("expense", "2026-10-29"),
            ReminderModal(),
            DraftEditModal(draft),
        ):
            assert len(modal.title) <= 45
            assert len(modal.children) <= 5
            for child in modal.children:
                assert len(child.label) <= 45
        for kind, verb in [("o", "done"), ("o", "snooze"), ("d", "confirm_delete"), ("t", "delete")]:
            view = button_view(kind, "a" * 32, 5, [verb])
            item = view.children[0]
            matched = item.template.fullmatch(item.item.custom_id)
            rebuilt = await ActionButton.from_custom_id(None, item.item, matched)
            assert rebuilt.version == 5 and rebuilt.verb == verb
    finally:
        await c.close()


async def test_memory_budget_is_serialized_character_limit(env):
    app, _, clock, a, _, _ = env
    for index in range(50):
        await app.memory.record(a, str(1000 + index), a.user, "Name" * 25, '"\\\n' * 800, clock.now())
    context = await app.memory.context(a, "message", "99999", 4000)
    assert len(json.dumps(context, ensure_ascii=False)) <= 4000


def test_chat_emoji_filter_preserves_clock():
    assert chat_text("Jam 10:30:45, nominal 1234. 🙂") == "Jam 10:30:45, nominal 1234."


async def test_pagination_controls_and_embeds(env):
    from elaine.views import PageView, send_card

    app, db, _, a, _, _ = env
    page0 = ui.transaction_list([dict(ITEM, id=str(n) * 32, status="posted") for n in range(5)], 0)
    page1 = ui.transaction_list([], 1)
    callback = AsyncMock(return_value=page1)
    view = PageView(int(a.user), 0, callback)
    i = interaction(app, db, a)
    i.edit_original_response = AsyncMock()
    await send_card(i, page0, view)
    assert view.previous.disabled and not view.next.disabled
    await view.turn(i, 1)
    assert view.next.disabled and not view.previous.disabled
    assert i.edit_original_response.call_args.kwargs["embed"] is page1
