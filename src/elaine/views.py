import re
from datetime import datetime
from zoneinfo import ZoneInfo

import discord

from .core import DomainError
from .finance import describe_draft
from .schemas import MoneyItem, ReminderInput, parse_amount
from .reminders import reminder_receipt


def button_view(kind, rid, version, verbs):
    view = discord.ui.View(timeout=None)
    for verb in verbs:
        view.add_item(ActionButton(kind, verb, rid, version))
    return view


class ActionButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template=r"el:(?P<kind>d|o|r):(?P<verb>save|edit|cancel|ack|done|snooze|manage):(?P<rid>[a-f0-9]{32}):(?P<version>[0-9]+)",
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
        }
        style = discord.ButtonStyle.danger if verb == "cancel" else discord.ButtonStyle.secondary
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
                        raise DomainError("Jadwal sudah berubah. Buka /ingat daftar lagi.")
                    await interaction.response.send_modal(ReminderModal(row))
                return
            await interaction.response.defer(ephemeral=True)
            if self.kind == "d":
                if self.verb == "save":
                    result = await client.app.finance.confirm(actor, self.rid, self.version)
                    await interaction.followup.send(
                        "Tersimpan. ID transaksi: " + ", ".join(x[:8] for x in result["ids"]), ephemeral=True
                    )
                elif self.verb == "cancel":
                    await client.app.finance.cancel_draft(actor, self.rid, self.version)
                    await interaction.followup.send("Draf dibatalkan.", ephemeral=True)
            elif self.kind == "o":
                if self.verb == "manage":
                    from .db import occurrences, reminders, one
                    from sqlalchemy import select

                    async with client.db.read() as s:
                        row = await one(
                            s,
                            select(reminders)
                            .join(occurrences)
                            .where(occurrences.c.id == self.rid, reminders.c.owner_id == actor.membership),
                        )
                    if not row:
                        raise DomainError("Pengingat bukan milikmu.")
                    await interaction.followup.send(
                        f"Pengingat `{row['id'][:8]}` · {row['title']} · {row['status']}",
                        view=button_view("r", row["id"], row["version"], ["edit", "cancel"]),
                        ephemeral=True,
                    )
                else:
                    row = await client.app.reminders.occurrence_action(
                        actor, self.rid, self.verb, self.version
                    )
                    labels = {
                        "ack": "Pengingat diakui. Alarm untuk kejadian ini berhenti.",
                        "done": "Ditandai selesai.",
                        "snooze": "Ditunda 10 menit.",
                    }
                    view = (
                        button_view("o", row["id"], row["revision"], ["done", "manage"])
                        if self.verb == "ack"
                        else None
                    )
                    await interaction.followup.send(labels[self.verb], view=view, ephemeral=True)
            elif self.kind == "r" and self.verb == "cancel":
                row = await client.app.reminders.detail(actor, self.rid)
                if row["version"] != self.version:
                    raise DomainError("Jadwal sudah berubah. Buka /ingat daftar lagi.")
                await client.app.reminders.cancel(actor, self.rid, f"interaction:{interaction.id}")
                await interaction.followup.send("Seluruh seri pengingat dibatalkan.", ephemeral=True)
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
            await interaction.followup.send(
                describe_draft(row),
                view=button_view("d", row["id"], row["version"], ["save", "cancel"]),
                ephemeral=True,
            )
        else:
            row = await client.app.finance.draft(actor, [item.model_dump(mode="json")], source)
            result = await client.app.finance.confirm(actor, row["id"], row["version"])
            await interaction.followup.send(
                "Transaksi tersimpan. ID: " + result["ids"][0][:8], ephemeral=True
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
        await interaction.followup.send(
            describe_draft(row),
            view=button_view("d", row["id"], row["version"], ["save", "edit", "cancel"]),
            ephemeral=True,
        )


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
        await interaction.followup.send(
            reminder_receipt(row),
            view=button_view("r", row["id"], row["version"], ["edit", "cancel"]),
            ephemeral=True,
        )


class PageView(discord.ui.View):
    def __init__(self, owner_id, page, callback):
        super().__init__(timeout=300)
        self.owner_id, self.page, self.callback = owner_id, page, callback

    async def interaction_check(self, interaction):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("Panel ini bukan milikmu.", ephemeral=True)
            return False
        return True

    async def turn(self, interaction, delta):
        try:
            await interaction.response.defer(ephemeral=True)
            self.page = max(0, self.page + delta)
            content = await self.callback(interaction, self.page)
            await interaction.edit_original_response(content=content, view=self)
        except Exception as exc:
            await interaction.client.respond_error(interaction, exc)

    @discord.ui.button(label="Sebelumnya")
    async def previous(self, interaction, button):
        await self.turn(interaction, -1)

    @discord.ui.button(label="Berikutnya")
    async def next(self, interaction, button):
        await self.turn(interaction, 1)
