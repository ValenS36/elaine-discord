import io
from datetime import datetime
from typing import Literal
from zoneinfo import ZoneInfo

import discord
from discord import app_commands

from .application import format_report, format_reminders, format_transactions
from .core import DomainError
from .finance import describe_draft
from .reminders import reminder_receipt
from .schemas import ReminderInput
from .views import MoneyModal, ReminderModal, PageView, button_view


def register_commands(client):
    tree = client.tree
    elaine = app_commands.Group(name="elaine", description="Bantuan dan profil Elaine")
    ingat = app_commands.Group(name="ingat", description="Pengingat dan alarm persisten")
    uang = app_commands.Group(name="uang", description="Buku keuangan pribadi")
    admin = app_commands.Group(
        name="admin",
        description="Konfigurasi Elaine",
        default_permissions=discord.Permissions(manage_guild=True),
    )

    @elaine.command(name="bantuan", description="Cara menggunakan Elaine")
    async def help_command(i: discord.Interaction):
        await i.response.send_message(
            "Elaine memiliki personality ekspresif bawaan: ngobrol, gestur singkat, dan teguran sesuai konteks.\n"
            "• /ingat buat, daftar, detail, ubah, batal\n"
            "• /uang tambah, draf, riwayat, ringkasan, detail, ubah, batal, ekspor\n"
            "• /elaine profil, ingatan, lupakan\n"
            "Di channel khusus, langsung mengobrol. Di channel lain yang terdaftar, mention/reply bot.\n"
            "Contoh: ‘ingatkan besok jam 10:30 mengerjakan laporan’ atau ‘catat makan 25 ribu’.\n"
            "Alarm berupa notifikasi Discord, bukan jaminan suara alarm HP.",
            ephemeral=True,
        )

    @elaine.command(name="profil", description="Lihat profil atau ganti timezone")
    async def profile(i: discord.Interaction, zona_waktu: str | None = None):
        await i.response.defer(ephemeral=True)
        actor = await client.actor(i)
        if zona_waktu:
            await client.app.identity.timezone(actor, zona_waktu)
        await i.followup.send(
            "Timezone: "
            + (zona_waktu or actor.timezone)
            + "\nJadwal lama dan timezone buku keuangan tidak berubah otomatis.",
            ephemeral=True,
        )

    @elaine.command(name="ingatan", description="Lihat fakta pribadi yang disimpan")
    async def facts(i: discord.Interaction):
        await i.response.defer(ephemeral=True)
        rows = await client.app.memory.list_facts(await client.actor(i))
        body = (
            "\n".join(f"`{x['id'][:8]}` {x['content'][:200]}" for x in rows)
            or "Belum ada fakta khusus. Tulis ‘ingat bahwa ...’ di chat."
        )
        await client.private_text(i, body)

    @elaine.command(name="lupakan", description="Hapus fakta khusus; tidak menghapus transaksi atau jadwal")
    async def forget(i: discord.Interaction, id: str):
        await i.response.defer(ephemeral=True)
        await client.app.memory.forget(await client.actor(i), id)
        await i.followup.send(
            "Fakta khusus dihapus. Pesan sumber di channel tidak ikut dihapus.", ephemeral=True
        )

    @ingat.command(name="buat", description="Buat pengingat dengan tanggal dan jam pasti")
    async def create_reminder(
        i: discord.Interaction,
        judul: str,
        tanggal: str,
        jam: str,
        jenis: Literal["reminder", "alarm"] = "reminder",
        ulang: Literal["none", "daily", "weekly"] = "none",
        hari: str = "",
    ):
        await i.response.defer(ephemeral=True)
        actor = await client.actor(i)
        await client.check_send_permission(i.channel, i.user)
        try:
            weekdays = [int(x.strip()) for x in hari.split(",")] if hari else []
        except ValueError:
            raise DomainError("Hari harus angka 0=Senin sampai 6=Minggu, dipisahkan koma.") from None
        raw = ReminderInput(
            title=judul, local_date=tanggal, local_time=jam, mode=jenis, recurrence=ulang, weekdays=weekdays
        ).model_dump(mode="json")
        row = await client.app.reminders.create(actor, raw, f"interaction:{i.id}")
        await i.followup.send(
            reminder_receipt(row),
            view=button_view("r", row["id"], row["version"], ["edit", "cancel"]),
            ephemeral=True,
        )

    @ingat.command(name="daftar", description="Lihat jadwal dan status pengingatmu")
    async def list_reminders(i: discord.Interaction):
        await i.response.defer(ephemeral=True)

        async def page(interaction, n):
            return f"Halaman {n + 1}\n" + format_reminders(
                await client.app.reminders.list(await client.actor(interaction), n)
            )

        await i.followup.send(await page(i, 0), view=PageView(i.user.id, 0, page), ephemeral=True)

    @ingat.command(name="detail", description="Buka tombol kelola dan kejadian pengingat")
    async def reminder_detail(i: discord.Interaction, id: str):
        await i.response.defer(ephemeral=True)
        actor = await client.actor(i)
        row = await client.app.reminders.detail(actor, id)
        await i.followup.send(
            format_reminders([row]),
            view=button_view("r", row["id"], row["version"], ["edit", "cancel"]),
            ephemeral=True,
        )
        for occ in await client.app.reminders.active_occurrences(actor, row["id"]):
            verbs = (
                ["done", "manage"] if occ["status"] == "acknowledged" else ["ack", "snooze", "done", "manage"]
            )
            await i.followup.send(
                f"Kejadian `{occ['id'][:8]}` · {occ['status']} · terkirim {occ['send_count']} kali",
                view=button_view("o", occ["id"], occ["revision"], verbs),
                ephemeral=True,
            )

    @ingat.command(name="ubah", description="Buka formulir untuk mengubah seri pengingat")
    async def edit_reminder(i: discord.Interaction, id: str):
        row = await client.app.reminders.detail(await client.actor(i), id)
        await i.response.send_modal(ReminderModal(row))

    @ingat.command(name="batal", description="Batalkan seluruh seri pengingat berdasarkan ID")
    async def cancel_reminder(i: discord.Interaction, id: str):
        await i.response.defer(ephemeral=True)
        row = await client.app.reminders.cancel(await client.actor(i), id, f"interaction:{i.id}")
        await i.followup.send("Seri pengingat " + row["id"][:8] + " dibatalkan.", ephemeral=True)

    @uang.command(name="tambah", description="Formulir untuk menyimpan transaksi pribadi")
    async def add_money(i: discord.Interaction, jenis: Literal["expense", "income"] = "expense"):
        actor = await client.actor(i)
        today = datetime.fromtimestamp(client.app.clock.now(), ZoneInfo(actor.timezone)).date().isoformat()
        await i.response.send_modal(MoneyModal(jenis, today))

    @uang.command(name="draf", description="Buka draf keuangan yang belum disimpan")
    async def drafts(i: discord.Interaction):
        await i.response.defer(ephemeral=True)
        rows = await client.app.finance.drafts(await client.actor(i))
        if not rows:
            await i.followup.send("Tidak ada draf aktif. Draf kedaluwarsa setelah 10 menit.", ephemeral=True)
        for row in rows:
            verbs = ["save", "edit", "cancel"] if row["kind"] == "finance.create" else ["save", "cancel"]
            await i.followup.send(
                describe_draft(row)[:1900],
                view=button_view("d", row["id"], row["version"], verbs),
                ephemeral=True,
            )

    @uang.command(name="ringkasan", description="Total harian atau bulanan, periode YYYY-MM atau YYYY-MM-DD")
    async def report(i: discord.Interaction, periode: str | None = None):
        await i.response.defer(ephemeral=True)
        result = await client.app.finance.report(await client.actor(i), periode)
        await client.private_text(i, format_report(result))

    @uang.command(name="riwayat", description="Riwayat transaksi dan status; gunakan periode YYYY-MM")
    async def history(i: discord.Interaction, periode: str | None = None):
        await i.response.defer(ephemeral=True)

        async def page(interaction, n):
            return f"Halaman {n + 1}\n" + format_transactions(
                await client.app.finance.history(await client.actor(interaction), periode, n)
            )

        await i.followup.send((await page(i, 0))[:1950], view=PageView(i.user.id, 0, page), ephemeral=True)

    @uang.command(name="detail", description="Lihat detail transaksi dan riwayat koreksinya")
    async def detail(i: discord.Interaction, id: str):
        await i.response.defer(ephemeral=True)
        row = await client.app.finance.detail(await client.actor(i), id)
        body = (
            format_transactions([row])
            + "\nAudit:\n"
            + "\n".join(
                datetime.fromtimestamp(x["created_at"], ZoneInfo("UTC")).isoformat() + " · " + x["operation"]
                for x in row["audit"][-15:]
            )
        )
        await client.private_text(i, body)

    @uang.command(name="ubah", description="Koreksi transaksi dengan preview dan audit")
    async def edit_money(i: discord.Interaction, id: str):
        row = await client.app.finance.detail(await client.actor(i), id)
        if row["status"] != "posted":
            raise DomainError("Transaksi sudah dibatalkan.")
        await i.response.send_modal(MoneyModal(row["kind"], row["occurred_on"], row))

    @uang.command(name="batal", description="Buat konfirmasi pembatalan transaksi tanpa menghapus audit")
    async def void_money(i: discord.Interaction, id: str):
        await i.response.defer(ephemeral=True)
        row = await client.app.finance.draft_change(await client.actor(i), id, f"interaction:{i.id}")
        await i.followup.send(
            describe_draft(row),
            view=button_view("d", row["id"], row["version"], ["save", "cancel"]),
            ephemeral=True,
        )

    @uang.command(name="ekspor", description="Unduh CSV transaksi milikmu pada periode tertentu")
    async def export(i: discord.Interaction, periode: str):
        await i.response.defer(ephemeral=True)
        data = await client.app.finance.export(await client.actor(i), periode)
        await i.followup.send(
            file=discord.File(io.BytesIO(data), filename="elaine-transaksi.csv"), ephemeral=True
        )

    @admin.command(name="channel", description="Daftarkan fungsi channel; hanya Manage Server")
    async def set_channel(
        i: discord.Interaction,
        channel: discord.TextChannel,
        fungsi: Literal["chat", "reminders", "finance", "admin"],
        mode: Literal["auto", "mention_only", "off"] = "mention_only",
        pemilik: discord.Member | None = None,
    ):
        await i.response.defer(ephemeral=True)
        actor = await client.actor(i)
        if not actor.admin:
            raise DomainError("Butuh izin Manage Server.")
        owner = None
        if pemilik:
            owner_actor = await client.app.identity.actor(i.guild_id, pemilik.id, pemilik.display_name, "0")
            owner = owner_actor.membership
            # Ensure it is actually a private channel for finance, not merely a logical owner.
            if channel.permissions_for(i.guild.default_role).view_channel:
                raise DomainError(
                    "Jadikan channel privat terlebih dahulu (deny View Channel untuk @everyone)."
                )
        if fungsi == "finance" and not owner:
            raise DomainError(
                "Channel keuangan perlu pemilik dan akses privat. Command /uang tetap bisa digunakan di channel biasa."
            )
        await client.app.identity.bind(actor, channel.id, fungsi, mode, owner)
        await i.followup.send(
            "Channel terdaftar. Atur izin role lain agar channel pribadi hanya dapat dibaca orang yang dituju.",
            ephemeral=True,
        )

    @admin.command(name="status", description="Status worker, database, dan versi")
    async def status(i: discord.Interaction):
        await i.response.defer(ephemeral=True)
        actor = await client.actor(i)
        if not actor.admin:
            raise DomainError("Butuh izin Manage Server.")
        await client.db.check()
        await i.followup.send(
            f"Elaine 2.0.0\nDatabase: valid\nWorker: {'aktif' if client.worker_task and not client.worker_task.done() else 'tidak aktif'}\nMode API: {client.settings.ai_mode}\nPersonality: Elaine bawaan.",
            ephemeral=True,
        )

    async def message_reminder(i: discord.Interaction, message: discord.Message):
        await client.actor(i)
        await i.response.send_modal(ReminderModal(title=message.content[:250]))

    tree.add_command(app_commands.ContextMenu(name="Buat pengingat", callback=message_reminder))
    for group in (elaine, ingat, uang, admin):
        tree.add_command(group)

    @tree.error
    async def on_error(i, error):
        await client.respond_error(i, getattr(error, "original", error))
