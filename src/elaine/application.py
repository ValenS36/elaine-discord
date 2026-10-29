import re
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from .core import Result, rupiah
from .reminders import reminder_receipt


@dataclass
class Application:
    identity: object
    memory: object
    finance: object
    reminders: object
    provider: object
    clock: object
    settings: object

    async def chat(self, actor, text, message_id, received_at):
        context = await self.memory.context(actor, text, message_id, self.settings.context_chars)
        p = await self.provider.propose(actor, text, context, received_at)
        source = "message:" + str(message_id)
        if p.kind in {"chat", "clarify"}:
            return Result(p.text or "Aku di sini. Apa yang ingin kamu bahas?")
        if p.kind == "reminder.create":
            if not re.search(r"ingatkan|pengingat|alarm|jadwalkan|remind", text, re.I):
                return Result("Kamu ingin membuat pengingat? Sebutkan tugas, tanggal, dan jamnya.")
            if not re.search(
                r"besok|lusa|hari ini|nanti malam|tanggal|\d{4}-\d{2}-\d{2}|senin|selasa|rabu|kamis|jumat|sabtu|minggu|setiap",
                text,
                re.I,
            ):
                return Result(
                    "Untuk hari atau tanggal berapa? Aku perlu tanggal yang jelas sebelum menyimpan jadwal."
                )
            row = await self.reminders.create(actor, p.reminder.model_dump(mode="json"), source)
            return Result(reminder_receipt(row), "reminder", row)
        if p.kind == "reminder.list":
            rows = await self.reminders.list(actor)
            return (
                Result(
                    "Gunakan /ingat daftar untuk melihat dan mengelola jadwalmu secara pribadi.",
                    "private_hint",
                )
                if not actor.private
                else Result(format_reminders(rows))
            )
        if p.kind == "reminder.cancel":
            if not re.search(r"batal|hapus|cancel", text, re.I) or p.resource_id not in text:
                return Result("Gunakan /ingat batal dengan ID pengingat yang ingin dibatalkan.")
            row = await self.reminders.cancel(actor, p.resource_id, source)
            return Result("Pengingat " + row["id"][:8] + " dibatalkan.")
        if p.kind == "finance.draft":
            if not re.search(r"catat|simpan|tambah|masukkan|rekam", text, re.I):
                return Result(
                    "Kalau ingin mencatatnya, tulis ‘catat pengeluaran ...’ atau gunakan /uang tambah."
                )
            action = await self.finance.draft(actor, [x.model_dump(mode="json") for x in p.items], source)
            if not actor.private:
                return Result(
                    "Draf keuangan sudah disiapkan, belum dibukukan. Buka /uang draf untuk memeriksa dan menyimpannya secara pribadi."
                )
            return Result("", "draft", action, True)
        if p.kind in {"finance.report", "finance.history"}:
            if not actor.private:
                return Result("Gunakan /uang ringkasan atau /uang riwayat. Hasilnya hanya terlihat olehmu.")
            if p.kind == "finance.report":
                return Result(format_report(await self.finance.report(actor, p.period)), sensitive=True)
            return Result(format_transactions(await self.finance.history(actor, p.period)), sensitive=True)
        if p.kind == "memory.remember":
            if not re.search(r"ingat bahwa|ingat kalau|ingat ya|ingat ini|simpan fakta", text, re.I):
                return Result("Untuk menyimpan fakta khusus, tulis ‘ingat bahwa ...’.")
            mid = await self.memory.remember(actor, p.fact, message_id)
            return Result("Fakta disimpan. ID " + mid[:8] + ". Kamu bisa mengelolanya lewat /elaine ingatan.")
        return Result("Gunakan command yang sesuai agar tindakannya jelas.")


def format_report(report):
    lines = [
        f"Rekap {report['start']} sampai sebelum {report['end']}",
        f"Pemasukan: {rupiah(report['income'])}",
        f"Pengeluaran: {rupiah(report['expense'])}",
        f"Arus bersih: {rupiah(report['net'])}",
        f"Transaksi aktif: {report['count']}",
    ]
    lines += [f"• {x['name']} ({x['kind']}): {rupiah(x['total'])}" for x in report["categories"][:20]]
    return "\n".join(lines) + "\nArus bersih bukan saldo rekening."


def format_transactions(rows):
    if not rows:
        return "Belum ada transaksi pada halaman/periode ini."
    return "\n".join(
        f"`{r['id'][:8]}` {r['occurred_on']} · {r['kind']} · {rupiah(r['amount'])} · {r['category'][:25]} · {r['status']}\n{r['note'][:45]}"
        for r in rows
    )


def format_reminders(rows):
    if not rows:
        return "Belum ada pengingat. Gunakan /ingat buat."
    return "\n".join(
        f"`{r['id'][:8]}` {r['title'][:60]} · {r['status']} · {r['mode']}\n"
        + (
            datetime.fromtimestamp(r["next_due"], ZoneInfo(r["timezone"])).strftime("%d-%m-%Y %H:%M")
            + " "
            + r["timezone"]
            if r["next_due"]
            else "Tidak ada jadwal seri berikutnya"
        )
        for r in rows
    )
