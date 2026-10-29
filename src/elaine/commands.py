import io
from datetime import datetime
from typing import Literal
from zoneinfo import ZoneInfo
import discord
from discord import app_commands
from .core import DomainError
from .schemas import ReminderInput
from . import presentation as ui
from .voice import voice_available
from .views import (
    MoneyModal,
    ReminderModal,
    PageView,
    button_view,
    send_card,
    transaction_buttons,
    draft_buttons,
    AISettingsModal,
)


def register_commands(client):
    tree = client.tree
    elaine = app_commands.Group(name="elaine", description="Profil, bantuan dan preferensi")
    ingat = app_commands.Group(name="ingat", description="Pengingat dan alarm persisten")
    uang = app_commands.Group(name="uang", description="Buku keuangan pribadi")
    suara = app_commands.Group(name="suara", description="Elaine di voice channel")
    admin = app_commands.Group(
        name="admin",
        description="Konfigurasi Elaine",
        default_permissions=discord.Permissions(manage_guild=True),
    )

    @elaine.command(name="bantuan", description="Panduan singkat Elaine")
    async def help_command(i: discord.Interaction):
        await i.response.defer(ephemeral=True)
        e = ui.card("Panduan Elaine", "Ngobrol di channel yang terdaftar, atau gunakan command berikut.")
        for name, text in [
            (
                "Pengingat",
                "/ingat buat · daftar · detail · ubah · batal\nAkui menghentikan alarm; Selesai menandai tugas selesai; Tunda menjadwalkan ulang 10 menit.",
            ),
            (
                "Keuangan",
                "/uang tambah · draf · ringkasan · riwayat · detail · ubah · batal · hapus · ekspor\nHapus mengeluarkan transaksi dari riwayat dan rekap setelah konfirmasi.",
            ),
            ("Profil dan ingatan", "/elaine profil · preferensi · ingatan · lupakan"),
            (
                "Alarm suara",
                "/suara gabung · tes · keluar\nUntuk jadwal, /ingat buat jenis alarm dengan channel_suara. Kamu harus berada di channel itu saat alarm berbunyi.",
            ),
            (
                "Admin",
                "/admin status · channel\nPemilik aplikasi: /admin ai · ai_tes untuk model, endpoint, key dan context.",
            ),
        ]:
            ui.field(e, name, text, False)
        await send_card(i, e)

    @elaine.command(name="profil", description="Kenali Elaine: personality dan kemampuannya")
    async def profile(i: discord.Interaction):
        await i.response.defer(ephemeral=True)
        actor = await client.actor(i)
        await send_card(i, ui.profile_card(client.user, actor.timezone))

    @elaine.command(name="preferensi", description="Lihat atau ubah zona waktu pribadi")
    async def preferences(i: discord.Interaction, zona_waktu: str | None = None):
        await i.response.defer(ephemeral=True)
        actor = await client.actor(i)
        if zona_waktu:
            await client.app.identity.timezone(actor, zona_waktu)
        e = ui.card("Preferensi kamu")
        ui.field(e, "Zona waktu", zona_waktu or actor.timezone)
        ui.field(
            e,
            "Berlaku untuk",
            "Jadwal baru. Jadwal lama dan timezone buku keuangan tetap memakai setting saat dibuat.",
            False,
        )
        await send_card(i, e)

    @elaine.command(name="ingatan", description="Lihat fakta pribadi yang disimpan")
    async def facts(i: discord.Interaction):
        await i.response.defer(ephemeral=True)

        async def page(interaction, n):
            rows = await client.app.memory.list_facts(await client.actor(interaction))
            e = ui.card("Ingatan khusus", footer=f"Elaine • Halaman {n + 1} • Hapus lewat /elaine lupakan")
            for row in rows[n * 5 : n * 5 + 5]:
                ui.field(e, "ID " + row["id"][:8], ui.clean(row["content"], 800), False)
            if not e.fields:
                e.description = "Belum ada fakta pada halaman ini."
            return e

        await send_card(i, await page(i, 0), PageView(i.user.id, 0, page))

    @elaine.command(name="lupakan", description="Hapus fakta khusus, bukan arsip pesan atau transaksi")
    async def forget(i: discord.Interaction, id: str):
        await i.response.defer(ephemeral=True)
        await client.app.memory.forget(await client.actor(i), id)
        await send_card(
            i, ui.notice("Fakta dihapus", "Pesan sumber di arsip dan Discord tidak ikut dihapus.")
        )

    @ingat.command(name="buat", description="Buat pengingat atau alarm dengan tanggal dan jam pasti")
    async def create_reminder(
        i: discord.Interaction,
        judul: str,
        tanggal: str,
        jam: str,
        jenis: Literal["reminder", "alarm"] = "reminder",
        ulang: Literal["none", "daily", "weekly"] = "none",
        hari: str = "",
        channel_suara: discord.VoiceChannel | None = None,
    ):
        await i.response.defer(ephemeral=True)
        actor = await client.actor(i)
        await client.check_send_permission(i.channel, i.user)
        try:
            days = [int(x.strip()) for x in hari.split(",")] if hari else []
        except ValueError:
            raise DomainError("Hari harus angka 0=Senin sampai 6=Minggu, dipisahkan koma.") from None
        if channel_suara:
            if jenis != "alarm":
                raise DomainError("Pilih jenis alarm untuk memakai channel_suara.")
            client.voice.validate(channel_suara, i.user)
        raw = ReminderInput(
            title=judul, local_date=tanggal, local_time=jam, mode=jenis, recurrence=ulang, weekdays=days
        ).model_dump(mode="json")
        row = await client.app.reminders.create(
            actor, raw, f"interaction:{i.id}", channel_suara.id if channel_suara else None
        )
        await send_card(
            i, ui.reminder_card(row), button_view("r", row["id"], row["version"], ["edit", "cancel"])
        )

    @ingat.command(name="daftar", description="Lihat daftar dan status pengingat")
    async def list_reminders(i: discord.Interaction):
        await i.response.defer(ephemeral=True)

        async def page(interaction, n):
            return ui.reminder_list(await client.app.reminders.list(await client.actor(interaction), n), n)

        await send_card(i, await page(i, 0), PageView(i.user.id, 0, page))

    @ingat.command(name="detail", description="Lihat detail dan kelola kejadian pengingat")
    async def reminder_detail(i: discord.Interaction, id: str):
        await i.response.defer(ephemeral=True)
        actor = await client.actor(i)
        row = await client.app.reminders.detail(actor, id)
        await send_card(
            i,
            ui.reminder_card(row, "Detail pengingat"),
            button_view("r", row["id"], row["version"], ["edit", "cancel"]),
        )
        for occ in await client.app.reminders.active_occurrences(actor, row["id"]):
            verbs = (
                ["done", "snooze", "manage"]
                if occ["status"] == "acknowledged"
                else ["ack", "snooze", "done", "manage"]
            )
            if occ["status"] == "failed":
                verbs = ["manage"]
            await send_card(
                i, ui.occurrence_card(occ, row), button_view("o", occ["id"], occ["revision"], verbs)
            )

    @ingat.command(name="ubah", description="Ubah jadwal atau judul pengingat")
    async def edit_reminder(i: discord.Interaction, id: str):
        row = await client.app.reminders.detail(await client.actor(i), id)
        await i.response.send_modal(ReminderModal(row))

    @ingat.command(name="batal", description="Batalkan seluruh seri pengingat")
    async def cancel_reminder(i: discord.Interaction, id: str):
        await i.response.defer(ephemeral=True)
        row = await client.app.reminders.cancel(await client.actor(i), id, f"interaction:{i.id}")
        await client.voice.stop_reminder(row["id"])
        await send_card(i, ui.reminder_card(row, "Pengingat dibatalkan"))

    @uang.command(name="tambah", description="Formulir transaksi pribadi")
    async def add_money(i: discord.Interaction, jenis: Literal["expense", "income"] = "expense"):
        actor = await client.actor(i)
        today = datetime.fromtimestamp(client.app.clock.now(), ZoneInfo(actor.timezone)).date().isoformat()
        await i.response.send_modal(MoneyModal(jenis, today))

    @uang.command(name="draf", description="Periksa draf keuangan sebelum disimpan")
    async def drafts(i: discord.Interaction):
        await i.response.defer(ephemeral=True)
        rows = await client.app.finance.drafts(await client.actor(i))
        if not rows:
            await send_card(
                i,
                ui.notice(
                    "Tidak ada draf aktif",
                    "Draf berlaku 10 menit. Buat transaksi lewat chat atau /uang tambah.",
                ),
            )
        for row in rows:
            await send_card(i, ui.draft_card(row), draft_buttons(row))

    @uang.command(name="ringkasan", description="Rekap harian atau bulanan: YYYY-MM-DD atau YYYY-MM")
    async def report(i: discord.Interaction, periode: str | None = None):
        await i.response.defer(ephemeral=True)
        await send_card(i, ui.report_card(await client.app.finance.report(await client.actor(i), periode)))

    @uang.command(name="riwayat", description="Daftar transaksi pribadi yang belum dihapus")
    async def history(i: discord.Interaction, periode: str | None = None):
        await i.response.defer(ephemeral=True)

        async def page(interaction, n):
            rows = await client.app.finance.history(
                await client.actor(interaction), periode, n, limit=ui.PAGE_SIZE
            )
            return ui.transaction_list(rows, n, periode)

        await send_card(i, await page(i, 0), PageView(i.user.id, 0, page))

    @uang.command(name="detail", description="Detail, audit dan tombol ubah/hapus transaksi")
    async def detail(i: discord.Interaction, id: str):
        await i.response.defer(ephemeral=True)
        row = await client.app.finance.detail(await client.actor(i), id)
        await send_card(i, ui.transaction_card(row), transaction_buttons(row))

    @uang.command(name="ubah", description="Koreksi transaksi dengan preview")
    async def edit_money(i: discord.Interaction, id: str):
        row = await client.app.finance.detail(await client.actor(i), id)
        if row["status"] != "posted":
            raise DomainError("Hanya transaksi aktif yang dapat diubah.")
        await i.response.send_modal(MoneyModal(row["kind"], row["occurred_on"], row))

    @uang.command(name="batal", description="Batalkan transaksi; tetap terlihat dalam riwayat")
    async def void_money(i: discord.Interaction, id: str):
        await i.response.defer(ephemeral=True)
        row = await client.app.finance.draft_change(await client.actor(i), id, f"interaction:{i.id}")
        await send_card(i, ui.draft_card(row), draft_buttons(row))

    @uang.command(name="hapus", description="Hapus transaksi dari riwayat dan rekap dengan konfirmasi")
    async def delete_money(i: discord.Interaction, id: str):
        await i.response.defer(ephemeral=True)
        row = await client.app.finance.draft_delete(await client.actor(i), id, f"interaction:{i.id}")
        await send_card(i, ui.draft_card(row), draft_buttons(row))

    @uang.command(name="ekspor", description="Unduh CSV periode YYYY-MM atau YYYY-MM-DD")
    async def export(i: discord.Interaction, periode: str):
        await i.response.defer(ephemeral=True)
        data = await client.app.finance.export(await client.actor(i), periode)
        await i.followup.send(
            embed=ui.notice("Ekspor keuangan", "File CSV siap diunduh."),
            file=discord.File(io.BytesIO(data), filename="elaine-transaksi.csv"),
            ephemeral=True,
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
            if channel.permissions_for(i.guild.default_role).view_channel:
                raise DomainError(
                    "Jadikan channel privat terlebih dahulu: deny View Channel untuk @everyone."
                )
        if fungsi == "finance" and not owner:
            raise DomainError("Channel keuangan perlu pemilik dan akses privat.")
        await client.app.identity.bind(actor, channel.id, fungsi, mode, owner)
        await send_card(
            i,
            ui.notice(
                "Channel terdaftar",
                "Periksa izin role lain agar channel pribadi hanya dapat dibaca pemiliknya.",
            ),
        )

    @admin.command(name="status", description="Model, context arsip, database dan worker")
    async def status(i: discord.Interaction):
        await i.response.defer(ephemeral=True)
        if not (await client.actor(i)).admin:
            raise DomainError("Butuh izin Manage Server.")
        await client.db.check()
        await send_card(
            i,
            ui.status_card(
                client.app.provider.settings,
                client.worker_task and not client.worker_task.done(),
                voice_available(),
            ),
        )

    @admin.command(name="ai", description="Formulir model, API key, endpoint dan context; pemilik aplikasi")
    async def ai_settings(i: discord.Interaction):
        await client.require_ai_admin(i)
        await i.response.send_modal(AISettingsModal(client))

    @admin.command(name="ai_tes", description="Uji koneksi model aktif; memakai kuota API")
    async def ai_probe(i: discord.Interaction):
        await i.response.defer(ephemeral=True)
        await client.require_ai_admin(i)
        actor = await client.actor(i)
        p = await client.app.provider.propose(actor, "Halo Elaine", {}, client.app.clock.now())
        await send_card(i, ui.notice("Koneksi AI berhasil", "Schema valid. Jenis respons: " + p.kind))

    def member_channel(i):
        if (
            not i.guild
            or not isinstance(i.user, discord.Member)
            or not i.user.voice
            or not i.user.voice.channel
        ):
            raise DomainError("Masuk ke voice channel dahulu.")
        return i.user.voice.channel

    @suara.command(name="gabung", description="Elaine bergabung ke voice channel tempatmu berada")
    async def voice_join(i: discord.Interaction):
        await i.response.defer(ephemeral=True)
        await client.actor(i)
        channel = member_channel(i)
        await client.voice.join(channel, i.user)
        await send_card(
            i,
            ui.notice(
                "Terhubung ke voice",
                "Elaine ada di "
                + channel.name
                + ". Gunakan /suara tes atau buat alarm dengan channel_suara.",
            ),
        )

    @suara.command(name="keluar", description="Hentikan suara dan keluarkan Elaine dari voice channel")
    async def voice_leave(i: discord.Interaction):
        await i.response.defer(ephemeral=True)
        await client.actor(i)
        await client.voice.leave(i.user)
        await send_card(
            i,
            ui.notice(
                "Sesi suara berakhir",
                "Elaine keluar dari voice channel. Jadwal pengingat mendatang tetap tersimpan.",
            ),
        )

    @suara.command(name="tes", description="Tes bunyi alarm 5 detik di voice channel kamu")
    async def voice_test(i: discord.Interaction):
        await i.response.defer(ephemeral=True)
        await client.actor(i)
        channel = member_channel(i)
        client.voice.validate(channel, i.user)
        msg = await send_card(i, ui.notice("Tes alarm suara", "Menyiapkan bunyi 5 detik."))

        async def report(text):
            await msg.edit(embed=ui.notice("Tes alarm suara", text))

        job = {
            "discord_user_id": str(i.user.id),
            "reminder": {"id": "test", "voice_channel_id": str(channel.id)},
            "occurrence": {"id": "test", "revision": 0},
        }
        if not client.voice.schedule(job, i.guild, report, test=True):
            raise DomainError("Server ini sedang memutar alarm suara lain.")

    async def message_reminder(i: discord.Interaction, message: discord.Message):
        await client.actor(i)
        await i.response.send_modal(ReminderModal(title=message.content[:250]))

    tree.add_command(app_commands.ContextMenu(name="Buat pengingat", callback=message_reminder))
    for group in (elaine, ingat, uang, admin, suara):
        tree.add_command(group)

    @tree.error
    async def on_error(i, error):
        await client.respond_error(i, getattr(error, "original", error))
