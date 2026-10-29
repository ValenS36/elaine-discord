"""Discord-native presentation, shared by chat, commands, forms and buttons."""

from datetime import datetime, timezone
import discord
from .core import rupiah
from .reminders import display_time

VIOLET = 0x9B8AC4
GREEN = 0x5DA995
RED = 0xCC737D
AMBER = 0xD8AD67
PAGE_SIZE = 5
STATUS = {
    "active": "Terjadwal",
    "scheduled": "Terjadwal",
    "notified": "Menunggu respons",
    "acknowledged": "Diakui · belum selesai",
    "completed": "Selesai",
    "cancelled": "Dibatalkan",
    "missed": "Terlewat",
    "failed": "Gagal dikirim",
    "posted": "Tercatat",
    "voided": "Dibatalkan",
    "deleted": "Dihapus",
    "pending": "Menunggu konfirmasi",
    "succeeded": "Disimpan",
}
KIND = {"expense": "Pengeluaran", "income": "Pemasukan", "alarm": "Alarm", "reminder": "Pengingat"}
REPEAT = {"none": "Sekali", "daily": "Setiap hari", "weekly": "Mingguan"}


def clean(value, limit=900):
    value = discord.utils.escape_mentions(discord.utils.escape_markdown(str(value)))
    return value[: limit - 1] + "…" if len(value) > limit else value or "—"


def card(title, description="", color=VIOLET, footer="Elaine • Asisten pribadi"):
    embed = discord.Embed(title=clean(title, 250), description=description[:4000] or None, color=color)
    embed.set_footer(text=footer[:2000])
    return embed


def field(embed, name, value, inline=True):
    embed.add_field(name=name, value=str(value)[:1024] or "—", inline=inline)
    return embed


def notice(title, message, success=True):
    return card(title, clean(message, 3500), GREEN if success else RED)


def reminder_card(row, title="Pengingat tersimpan"):
    e = card(title, clean(row["title"], 300), GREEN if row["status"] == "completed" else VIOLET)
    field(e, "ID", f"`{row['id'][:8]}`")
    latest = row.get("last_occurrence")
    status = (
        latest["status"]
        if latest and row["status"] == "active" and row["recurrence"] == "none"
        else row["status"]
    )
    field(e, "Status tugas", STATUS.get(status, status))
    field(e, "Jenis", KIND.get(row["mode"], row["mode"]))
    field(
        e,
        "Jadwal berikutnya",
        display_time(row["next_due"], row["timezone"])
        if row["next_due"]
        else "Tidak ada jadwal seri berikutnya",
        False,
    )
    if latest:
        field(
            e,
            "Kejadian terakhir",
            display_time(latest["effective_at"], row["timezone"])
            + " · "
            + STATUS.get(latest["status"], latest["status"]),
            False,
        )
    field(e, "Pengulangan", REPEAT.get(row["recurrence"], row["recurrence"]))
    field(e, "Zona waktu", clean(row["timezone"]))
    if row.get("voice_channel_id"):
        field(e, "Alarm suara", f"<#{row['voice_channel_id']}> · 30 detik per notifikasi", False)
    return e


def occurrence_card(row, reminder, title=None):
    e = card(
        title or STATUS.get(row["status"], "Pengingat"),
        clean(reminder["title"], 300),
        GREEN if row["status"] == "completed" else VIOLET,
    )
    field(e, "Status tugas", STATUS.get(row["status"], row["status"]), False)
    field(e, "Jadwal kejadian", display_time(row["effective_at"], reminder["timezone"]), False)
    field(e, "Pengingat / kejadian", f"`{reminder['id'][:8]}` / `{row['id'][:8]}`")
    field(e, "Notifikasi terkirim", f"{row['send_count']} / {reminder['max_sends']}")
    if row["status"] == "acknowledged":
        field(e, "Berikutnya", "Alarm berhenti. Tekan Selesai setelah tugas benar-benar dikerjakan.", False)
    elif row["status"] == "scheduled" and row["revision"] > 1:
        field(e, "Ditunda", f"Notifikasi berikutnya <t:{row['effective_at']}:R>.", False)
    elif row["status"] == "completed" and reminder["recurrence"] != "none":
        field(e, "Seri berulang", "Kejadian ini selesai; jadwal seri berikutnya tetap aktif.", False)
    return e


def reminder_list(rows, page):
    e = card(
        "Daftar pengingat",
        "Gunakan /ingat detail dengan ID untuk mengelola tugas.",
        footer=f"Elaine • Halaman {page + 1} • Maksimal {PAGE_SIZE} pengingat",
    )
    if not rows:
        e.description = "Belum ada pengingat pada halaman ini."
    for r in rows:
        when = (
            display_time(r["next_due"], r["timezone"])
            if r["next_due"]
            else "Tidak ada jadwal seri berikutnya"
        )
        last = r.get("last_occurrence")
        if last and not r["next_due"]:
            when = display_time(last["effective_at"], r["timezone"])
        status = (
            last["status"] if last and r["status"] == "active" and r["recurrence"] == "none" else r["status"]
        )
        field(
            e,
            clean(r["title"], 150),
            f"**{KIND[r['mode']]}** · {STATUS.get(status, status)}\n"
            f"{when}\n{REPEAT[r['recurrence']]} · ID `{r['id'][:8]}`",
            False,
        )
    return e


def money_lines(x):
    return f"**{rupiah(x['amount'])}** · {KIND[x['kind']]}\n{clean(x.get('category', ''), 70)} · {x['occurred_on']}\n{clean(x.get('note', ''), 250)}"


def draft_card(row):
    kind = row["kind"]
    if kind == "finance.create":
        e = card(
            "Periksa transaksi",
            "Draf belum dibukukan. Periksa rincian, lalu tekan Simpan.",
            AMBER,
            f"Elaine • Draf {row['id'][:8]} • Berlaku 10 menit",
        )
        for i, x in enumerate(row["payload"]["items"]):
            field(e, f"Transaksi {i + 1}", money_lines(x), False)
        field(e, "Total nominal", rupiah(sum(x["amount"] for x in row["payload"]["items"])), False)
        return e
    payload = row["payload"]
    title = {
        "finance.edit": "Konfirmasi koreksi",
        "finance.void": "Batalkan transaksi?",
        "finance.delete": "Hapus transaksi?",
    }[kind]
    e = card(title, f"Transaksi `{payload['id'][:8]}`", RED if kind == "finance.delete" else AMBER)
    if "item" in payload:
        field(e, "Rincian baru", money_lines(payload["item"]), False)
    if kind == "finance.delete":
        tx = payload["transaction"]
        field(
            e,
            "Transaksi yang dihapus",
            f"{rupiah(tx['amount'])} · {tx['occurred_on']}\n{clean(tx['note'], 250)}",
            False,
        )
        field(
            e,
            "Dampak",
            "Dihapus dari riwayat, ekspor, dan perhitungan. Audit internal tetap disimpan. Tekan Hapus transaksi untuk menyetujui.",
            False,
        )
    elif kind == "finance.void":
        field(e, "Dampak", "Tidak dihitung dalam rekap, tetapi tetap terlihat sebagai Dibatalkan.", False)
    return e


def transaction_card(row, title="Detail transaksi"):
    e = card(title, money_lines(row), RED if row["status"] == "deleted" else GREEN)
    field(e, "ID", f"`{row['id'][:8]}`")
    field(e, "Status", STATUS.get(row["status"], row["status"]))
    if row.get("audit"):
        lines = [
            f"{datetime.fromtimestamp(a['created_at'], timezone.utc):%d/%m %H:%M} UTC · "
            + {
                "create": "Dicatat",
                "finance.edit": "Dikoreksi",
                "finance.void": "Dibatalkan",
                "finance.delete": "Dihapus",
            }.get(a["operation"], a["operation"])
            for a in row["audit"][-6:]
        ]
        field(e, "Riwayat perubahan", "\n".join(lines), False)
    return e


def saved_card(rows, operation):
    title = {
        "finance.create": "Transaksi tersimpan",
        "finance.edit": "Koreksi tersimpan",
        "finance.void": "Transaksi dibatalkan",
        "finance.delete": "Transaksi dihapus",
    }[operation]
    if len(rows) == 1:
        return transaction_card(rows[0], title)
    e = card(title, "Data sudah tersimpan pada buku keuanganmu.", GREEN)
    for row in rows:
        field(e, f"ID {row['id'][:8]}", money_lines(row) + f"\n{STATUS[row['status']]}", False)
    return e


async def finance_receipt(finance, actor, result):
    rows = [await finance.detail(actor, x) for x in result["ids"]]
    return saved_card(rows, result["operation"])


def transaction_list(rows, page, period=None):
    e = card(
        "Riwayat keuangan",
        "Periode: " + clean(period or "Semua tanggal"),
        footer=f"Elaine • Halaman {page + 1} • Detail / ubah / hapus memakai ID",
    )
    if not rows:
        e.description += "\nBelum ada transaksi pada halaman ini."
    for r in rows:
        field(
            e,
            f"{r['occurred_on']} · {KIND[r['kind']]} · {rupiah(r['amount'])}",
            f"{clean(r['category'], 70)} · {STATUS[r['status']]}\n{clean(r['note'], 180)}\nID `{r['id'][:8]}`",
            False,
        )
    return e


def report_card(r):
    e = card("Ringkasan keuangan", f"Mulai **{r['start']}** sampai sebelum **{r['end']}**")
    field(e, "Pemasukan", rupiah(r["income"]))
    field(e, "Pengeluaran", rupiah(r["expense"]))
    field(e, "Arus bersih", rupiah(r["net"]))
    field(e, "Transaksi aktif", r["count"], False)
    for kind in ("expense", "income"):
        lines = [
            f"{clean(x['name'], 80)} — **{rupiah(x['total'])}**" for x in r["categories"] if x["kind"] == kind
        ]
        field(e, "Kategori " + KIND[kind].lower(), "\n".join(lines[:8]) or "Belum ada.", False)
    e.set_footer(text="Elaine • Arus bersih bukan saldo rekening bank • Rincian lengkap lewat ekspor CSV")
    return e


def profile_card(user, zone):
    e = card(
        "Elaine",
        "Asisten AI dan teman percakapanmu. Tenang, teliti, tegas, dan perhatian; sesekali menyelipkan humor datar.",
    )
    if user:
        e.set_thumbnail(url=user.display_avatar.url)
    field(
        e,
        "Personality",
        "Ekspresif secara bawaan, gestur singkat, dan teguran sesuai konteks. Gaya tidak perlu diaktifkan.",
        False,
    )
    field(
        e,
        "Yang bisa kubantu",
        "Percakapan dan ingatan channel\nPengingat, alarm teks dan suara\nPencatatan dan rekap keuangan pribadi",
        False,
    )
    field(
        e,
        "Cara berinteraksi",
        "Ngobrol di channel yang terdaftar, atau gunakan /ingat dan /uang. Emoji hanya sebagai reaksi pesan, bukan dicampur ke teks obrolan.",
        False,
    )
    field(e, "Zona waktu kamu", zone)
    field(e, "Versi", "2.1.0")
    e.set_footer(text="Elaine • /elaine preferensi untuk timezone • /elaine bantuan untuk panduan")
    return e


def status_card(settings, worker, voice_ok):
    e = card("Status Elaine", "Versi 2.1.0 • Personality bawaan")
    field(e, "Database", "Terhubung · schema 0002")
    field(e, "Scheduler", "Aktif" if worker else "Tidak aktif")
    field(e, "Alarm suara", "Siap" if voice_ok else "Dependency suara belum lengkap")
    field(e, "Model AI", clean(settings.model, 200), False)
    field(e, "Context arsip", f"{settings.context_chars:,} karakter maksimum".replace(",", "."), False)
    field(
        e,
        "Jendela konteks model",
        "Tidak dilaporkan provider. Batas arsip di atas bukan kapasitas token model; prompt dan pesan terbaru juga memakai konteks.",
        False,
    )
    field(e, "Mode API", settings.ai_mode)
    field(e, "API key", "Tersedia" if settings.api_key else "Belum diisi")
    field(e, "Endpoint", clean(settings.api_base, 250), False)
    return e
