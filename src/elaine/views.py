import re
from datetime import datetime
from zoneinfo import ZoneInfo
from contextlib import suppress

import discord
from .core import DomainError
from .schemas import MoneyItem, ReminderInput, parse_amount
from . import presentation as ui


async def send_card(interaction, embed, view=None):
    # Webhook.send rejects explicit view=None (unlike Message.edit).
    if isinstance(view, PageView):
        view.update_controls(embed)
    kwargs = {"view": view} if view is not None else {}
    return await interaction.followup.send(embed=embed, ephemeral=True, **kwargs)


async def update_card(interaction, embed, view=None):
    # A stale/deleted UI must not turn a committed action into a false failure.
    if interaction.message:
        with suppress(discord.HTTPException):
            await interaction.message.edit(content=None, embed=embed, view=view)


def button_view(kind, rid, version, verbs):
    view = discord.ui.View(timeout=None)
    for verb in verbs:
        view.add_item(ActionButton(kind, verb, rid, version))
    return view


def transaction_buttons(row):
    verbs = (
        ["edit", "delete"] if row["status"] == "posted" else (["delete"] if row["status"] == "voided" else [])
    )
    return button_view("t", row["id"], row["version"], verbs) if verbs else None


def draft_buttons(row):
    verbs = (
        ["confirm_delete", "cancel"]
        if row["kind"] == "finance.delete"
        else ["save", "edit", "cancel"]
        if row["kind"] == "finance.create"
        else ["save", "cancel"]
    )
    return button_view("d", row["id"], row["version"], verbs)


class ActionButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template=r"el:(?P<kind>d|o|r|t):(?P<verb>save|edit|cancel|ack|done|snooze|manage|delete|confirm_delete):(?P<rid>[a-f0-9]{32}):(?P<version>[0-9]+)",
):
    def __init__(self, kind, verb, rid, version):
        self.kind, self.verb, self.rid, self.version = kind, verb, rid, int(version)
        labels = {
            "save": "Simpan",
            "edit": "Ubah",
            "cancel": "Batal",
            "ack": "Akui",
            "done": "Selesai",
            "snooze": "Tunda 10 menit",
            "manage": "Kelola",
            "delete": "Hapus",
            "confirm_delete": "Hapus transaksi",
        }
        style = (
            discord.ButtonStyle.danger
            if verb in {"cancel", "delete", "confirm_delete"}
            else discord.ButtonStyle.secondary
        )
        if verb in {"save", "done"}:
            style = discord.ButtonStyle.success
        super().__init__(
            discord.ui.Button(label=labels[verb], style=style, custom_id=f"el:{kind}:{verb}:{rid}:{version}")
        )

    @classmethod
    async def from_custom_id(cls, interaction, item, match: re.Match):
        return cls(**match.groupdict())

    async def callback(self, interaction):
        client = interaction.client
        try:
            actor = await client.actor(interaction)
            if self.verb == "edit":
                if self.kind == "d":
                    row = await client.app.finance.get_draft(actor, self.rid)
                    client.app.finance._pending(row, self.version)
                    if row["kind"] != "finance.create":
                        raise DomainError("Buat ulang koreksi lewat /uang ubah.")
                    await interaction.response.send_modal(DraftEditModal(row))
                elif self.kind == "r":
                    row = await client.app.reminders.detail(actor, self.rid)
                    if row["version"] != self.version:
                        raise DomainError("Jadwal berubah. Buka /ingat detail lagi.")
                    await interaction.response.send_modal(ReminderModal(row))
                elif self.kind == "t":
                    row = await client.app.finance.detail(actor, self.rid)
                    if row["version"] != self.version or row["status"] != "posted":
                        raise DomainError("Transaksi berubah. Buka /uang detail lagi.")
                    await interaction.response.send_modal(MoneyModal(row["kind"], row["occurred_on"], row))
                return
            await interaction.response.defer(ephemeral=True)
            if self.kind == "d":
                if self.verb in {"save", "confirm_delete"}:
                    result = await client.app.finance.confirm(actor, self.rid, self.version)
                    embed = await ui.finance_receipt(client.app.finance, actor, result)
                    row = await client.app.finance.detail(actor, result["ids"][0])
                    view = transaction_buttons(row) if len(result["ids"]) == 1 else None
                elif self.verb == "cancel":
                    await client.app.finance.cancel_draft(actor, self.rid, self.version)
                    embed, view = ui.notice("Draf dibatalkan", "Tidak ada perubahan transaksi."), None
                else:
                    raise DomainError("Tombol tidak sesuai jenis tindakan.")
                await send_card(interaction, embed, view)
                await update_card(interaction, embed, view)
            elif self.kind == "t" and self.verb == "delete":
                tx = await client.app.finance.detail(actor, self.rid)
                if tx["version"] != self.version:
                    raise DomainError("Transaksi berubah. Buka /uang detail lagi.")
                row = await client.app.finance.draft_delete(actor, self.rid, f"interaction:{interaction.id}")
                await send_card(interaction, ui.draft_card(row), draft_buttons(row))
            elif self.kind == "o":
                if self.verb == "manage":
                    from .db import occurrences, reminders, one
                    from sqlalchemy import select

                    async with client.db.read() as session:
                        row = await one(
                            session,
                            select(reminders)
                            .join(occurrences)
                            .where(occurrences.c.id == self.rid, reminders.c.owner_id == actor.membership),
                        )
                    if not row:
                        raise DomainError("Pengingat bukan milikmu.")
                    await send_card(
                        interaction,
                        ui.reminder_card(row, "Detail pengingat"),
                        button_view("r", row["id"], row["version"], ["edit", "cancel"]),
                    )
                else:
                    row = await client.app.reminders.occurrence_action(
                        actor, self.rid, self.verb, self.version
                    )
                    await client.voice.stop_occurrence(row["id"])
                    reminder = await client.app.reminders.detail(actor, row["reminder_id"])
                    embed = ui.occurrence_card(row, reminder)
                    verbs = (
                        ["done", "snooze", "manage"]
                        if row["status"] == "acknowledged"
                        else ["done", "manage"]
                        if row["status"] == "scheduled"
                        else []
                    )
                    view = button_view("o", row["id"], row["revision"], verbs) if verbs else None
                    await send_card(interaction, embed, view)
                    await update_card(interaction, embed, view)
            elif self.kind == "r" and self.verb == "cancel":
                row = await client.app.reminders.detail(actor, self.rid)
                if row["version"] != self.version:
                    raise DomainError("Jadwal berubah. Buka /ingat detail lagi.")
                row = await client.app.reminders.cancel(actor, self.rid, f"interaction:{interaction.id}")
                await client.voice.stop_reminder(row["id"])
                embed = ui.reminder_card(row, "Pengingat dibatalkan")
                await send_card(interaction, embed)
                await update_card(interaction, embed)
        except Exception as exc:
            await client.respond_error(interaction, exc)


class SafeModal(discord.ui.Modal):
    async def on_error(self, interaction, error):
        await interaction.client.respond_error(interaction, error)


class MoneyModal(SafeModal):
    def __init__(self, kind, today, transaction=None):
        super().__init__(title="Koreksi transaksi" if transaction else "Simpan transaksi", timeout=600)
        self.kind, self.transaction = kind, transaction
        self.amount = discord.ui.TextInput(
            label="Nominal rupiah", default=str(transaction["amount"]) if transaction else "", max_length=30
        )
        self.category = discord.ui.TextInput(
            label="Kategori", default=transaction["category"] if transaction else "Lainnya", max_length=60
        )
        self.day = discord.ui.TextInput(
            label="Tanggal YYYY-MM-DD",
            default=transaction["occurred_on"] if transaction else today,
            max_length=10,
        )
        self.note = discord.ui.TextInput(
            label="Catatan",
            default=transaction["note"] if transaction else "",
            required=False,
            max_length=500,
        )
        for item in (self.amount, self.category, self.day, self.note):
            self.add_item(item)

    async def on_submit(self, interaction):
        await interaction.response.defer(ephemeral=True)
        client = interaction.client
        actor = await client.actor(interaction)
        item = MoneyItem(
            amount=parse_amount(self.amount.value),
            kind=self.kind,
            category=self.category.value,
            occurred_on=self.day.value,
            note=self.note.value,
        )
        source = f"interaction:{interaction.id}"
        if self.transaction:
            latest = await client.app.finance.detail(actor, self.transaction["id"])
            if latest["version"] != self.transaction["version"]:
                raise DomainError("Transaksi sudah berubah sejak formulir dibuka. Buka ulang.")
            row = await client.app.finance.draft_change(
                actor, self.transaction["id"], source, item.model_dump(mode="json")
            )
            await send_card(interaction, ui.draft_card(row), draft_buttons(row))
        else:
            row = await client.app.finance.draft(actor, [item.model_dump(mode="json")], source)
            result = await client.app.finance.confirm(actor, row["id"], row["version"])
            tx = await client.app.finance.detail(actor, result["ids"][0])
            await send_card(
                interaction,
                await ui.finance_receipt(client.app.finance, actor, result),
                transaction_buttons(tx),
            )


class DraftEditModal(SafeModal):
    def __init__(self, row):
        super().__init__(title="Ubah item draf keuangan", timeout=600)
        self.row = row
        self.index = discord.ui.TextInput(
            label=f"Nomor item (1–{len(row['payload']['items'])})", default="1", max_length=1
        )
        self.amount = discord.ui.TextInput(
            label="Nominal baru; kosong = tetap", required=False, max_length=30
        )
        self.category = discord.ui.TextInput(
            label="Kategori baru; kosong = tetap", required=False, max_length=60
        )
        self.day = discord.ui.TextInput(
            label="Tanggal baru YYYY-MM-DD; kosong = tetap", required=False, max_length=10
        )
        self.note = discord.ui.TextInput(
            label="Catatan baru; kosong = tetap, - = hapus", required=False, max_length=500
        )
        for item in (self.index, self.amount, self.category, self.day, self.note):
            self.add_item(item)

    async def on_submit(self, interaction):
        await interaction.response.defer(ephemeral=True)
        client = interaction.client
        actor = await client.actor(interaction)
        items = [dict(x) for x in self.row["payload"]["items"]]
        try:
            index = int(self.index.value) - 1
        except ValueError:
            raise DomainError("Nomor item harus angka.") from None
        if not 0 <= index < len(items):
            raise DomainError("Nomor item tidak ada.")
        original = items[index]
        items[index] = MoneyItem(
            amount=parse_amount(self.amount.value) if self.amount.value else original["amount"],
            kind=original["kind"],
            category=self.category.value or original["category"],
            occurred_on=self.day.value or original["occurred_on"],
            note="" if self.note.value == "-" else (self.note.value or original["note"]),
        ).model_dump(mode="json")
        row = await client.app.finance.change_draft(actor, self.row["id"], self.row["version"], items)
        await send_card(interaction, ui.draft_card(row), draft_buttons(row))


class ReminderModal(SafeModal):
    def __init__(self, row=None, title=""):
        super().__init__(title="Ubah pengingat" if row else "Buat pengingat", timeout=600)
        self.row = row
        local = (
            datetime.fromtimestamp(row["next_due"], ZoneInfo(row["timezone"]))
            if row and row["next_due"]
            else None
        )
        self.subject = discord.ui.TextInput(
            label="Judul", default=row["title"] if row else title[:250], max_length=250
        )
        self.day = discord.ui.TextInput(
            label="Tanggal YYYY-MM-DD", default=local.date().isoformat() if local else "", max_length=10
        )
        self.hour = discord.ui.TextInput(
            label="Jam HH:MM", default=row["local_time"] if row else "", max_length=5
        )
        self.mode = discord.ui.TextInput(
            label="Jenis: reminder atau alarm", default=row["mode"] if row else "reminder", max_length=8
        )
        self.repeat = discord.ui.TextInput(
            label="Ulang: none, daily, weekly", default=row["recurrence"] if row else "none", max_length=8
        )
        for item in (self.subject, self.day, self.hour, self.mode, self.repeat):
            self.add_item(item)

    async def on_submit(self, interaction):
        await interaction.response.defer(ephemeral=True)
        client = interaction.client
        actor = await client.actor(interaction)
        raw = ReminderInput(
            title=self.subject.value,
            local_date=self.day.value,
            local_time=self.hour.value,
            mode=self.mode.value,
            recurrence=self.repeat.value,
            weekdays=self.row["weekdays"] if self.row and self.repeat.value == "weekly" else [],
        ).model_dump(mode="json")
        if self.row:
            row = await client.app.reminders.change(
                actor, self.row["id"], raw, f"interaction:{interaction.id}", self.row["version"]
            )
        else:
            await client.check_send_permission(interaction.channel, interaction.user)
            row = await client.app.reminders.create(actor, raw, f"interaction:{interaction.id}")
        if self.row:
            await client.voice.stop_reminder(row["id"])
        await send_card(
            interaction,
            ui.reminder_card(row),
            button_view("r", row["id"], row["version"], ["edit", "cancel"]),
        )


class PageView(discord.ui.View):
    def __init__(self, owner_id, page, callback):
        super().__init__(timeout=300)
        self.owner_id, self.page, self.callback = owner_id, page, callback
        self.previous.disabled = page == 0

    def update_controls(self, embed):
        self.previous.disabled = self.page == 0
        self.next.disabled = len(embed.fields) < ui.PAGE_SIZE

    async def interaction_check(self, interaction):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("Panel ini bukan milikmu.", ephemeral=True)
            return False
        return True

    async def turn(self, interaction, delta):
        try:
            await interaction.response.defer(ephemeral=True)
            new_page = max(0, self.page + delta)
            content = await self.callback(interaction, new_page)
            self.page = new_page
            self.update_controls(content)
            await interaction.edit_original_response(content=None, embed=content, view=self)
        except Exception as exc:
            await interaction.client.respond_error(interaction, exc)

    @discord.ui.button(label="Sebelumnya")
    async def previous(self, interaction, button):
        await self.turn(interaction, -1)

    @discord.ui.button(label="Berikutnya")
    async def next(self, interaction, button):
        await self.turn(interaction, 1)


class AISettingsModal(SafeModal):
    def __init__(self, client):
        super().__init__(title="Konfigurasi AI Elaine", timeout=600)
        cfg = client.app.provider.settings
        self.version = client.runtime.version
        self.model = discord.ui.TextInput(label="ID model AI", default=cfg.model, max_length=200)
        self.base = discord.ui.TextInput(
            label="Base URL HTTPS (kompatibel OpenAI)", default=cfg.api_base, max_length=250
        )
        self.key = discord.ui.TextInput(
            label="API key baru; kosong = pertahankan", required=False, max_length=2000
        )
        self.context = discord.ui.TextInput(
            label="Context arsip (karakter, 4000–60000)", default=str(cfg.context_chars), max_length=5
        )
        self.mode = discord.ui.TextInput(label="Mode API: json atau tools", default=cfg.ai_mode, max_length=5)
        for item in (self.model, self.base, self.key, self.context, self.mode):
            self.add_item(item)

    async def on_submit(self, interaction):
        await interaction.response.defer(ephemeral=True)
        client = interaction.client
        await client.require_ai_admin(interaction)
        from .runtime_config import validate_public_dns

        async with client.runtime.lock:
            if self.version != client.runtime.version:
                raise DomainError("Konfigurasi berubah sejak formulir dibuka. Buka /admin ai lagi.")
            settings = client.runtime.candidate(
                client.app.provider.settings,
                self.model.value,
                self.base.value,
                self.key.value,
                int(self.context.value),
                self.mode.value,
            )
            await validate_public_dns(settings.api_base)
            client.runtime.save(settings)
            client.app.provider.settings = settings
            client.app.settings = settings
            client.settings = settings
        await send_card(
            interaction,
            ui.notice(
                "Konfigurasi AI disimpan",
                "Berlaku untuk permintaan berikutnya dan tetap tersimpan setelah restart. Jalankan /admin ai_tes untuk memeriksa koneksi. API key tidak ditampilkan kembali.",
            ),
        )
