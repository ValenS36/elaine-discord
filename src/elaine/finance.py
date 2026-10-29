import csv
import io
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import insert, select, update, func, case

from .actions import audit, new_action, resolve_owned
from .core import DomainError, uid, rupiah
from .db import actions, categories, ledgers, transactions, audits, one, all_rows
from .schemas import MoneyItem


class Finance:
    def __init__(self, db, clock):
        self.db, self.clock = db, clock

    async def ledger(self, s, actor):
        row = await one(s, select(ledgers).where(ledgers.c.owner_id == actor.membership))
        if row:
            return dict(row)
        row = dict(id=uid(), owner_id=actor.membership, currency="IDR", timezone=actor.timezone)
        await s.execute(insert(ledgers).values(**row))
        return row

    async def draft(self, actor, items, source):
        validated = [MoneyItem.model_validate(x).model_dump(mode="json") for x in items]
        if not 1 <= len(validated) <= 5:
            raise DomainError("Satu permintaan harus berisi 1 sampai 5 transaksi.")
        async with self.db.write() as s:
            return await new_action(
                s, actor, source, "finance.create", {"items": validated}, self.clock.now()
            )

    async def draft_change(self, actor, txid, source, item=None):
        async with self.db.write() as s:
            ledger = await self.ledger(s, actor)
            tx = await resolve_owned(s, transactions, txid, transactions.c.ledger_id, ledger["id"])
            if tx["status"] != "posted":
                raise DomainError("Transaksi ini sudah dibatalkan.")
            payload = {"id": tx["id"], "version": tx["version"]}
            kind = "finance.void"
            if item is not None:
                payload["item"] = MoneyItem.model_validate(item).model_dump(mode="json")
                kind = "finance.edit"
            return await new_action(s, actor, source, kind, payload, self.clock.now())

    async def draft_delete(self, actor, txid, source):
        async with self.db.write() as s:
            ledger = await self.ledger(s, actor)
            tx = await resolve_owned(s, transactions, txid, transactions.c.ledger_id, ledger["id"])
            if tx["status"] == "deleted":
                raise DomainError("Transaksi sudah dihapus dari riwayat.")
            payload = {"id": tx["id"], "version": tx["version"], "transaction": tx}
            return await new_action(s, actor, source, "finance.delete", payload, self.clock.now())

    async def get_draft(self, actor, action_id):
        async with self.db.read() as s:
            row = await resolve_owned(s, actions, action_id, actions.c.owner_id, actor.membership)
            if not row["kind"].startswith("finance."):
                raise DomainError("Bukan draf keuangan.")
            return row

    async def drafts(self, actor):
        async with self.db.read() as s:
            return await all_rows(
                s,
                select(actions)
                .where(
                    actions.c.owner_id == actor.membership,
                    actions.c.kind.like("finance.%"),
                    actions.c.status == "pending",
                    actions.c.expires_at > self.clock.now(),
                )
                .order_by(actions.c.created_at.desc())
                .limit(10),
            )

    async def change_draft(self, actor, action_id, version, items):
        values = [MoneyItem.model_validate(x).model_dump(mode="json") for x in items]
        if not 1 <= len(values) <= 5:
            raise DomainError("Draf harus berisi 1–5 item.")
        async with self.db.write() as s:
            row = await resolve_owned(s, actions, action_id, actions.c.owner_id, actor.membership)
            self._pending(row, version)
            if row["kind"] != "finance.create":
                raise DomainError("Draf perubahan ini perlu dibuat ulang lewat /uang ubah.")
            await s.execute(
                update(actions)
                .where(actions.c.id == row["id"])
                .values(payload={"items": values}, version=version + 1)
            )
            return {**row, "payload": {"items": values}, "version": version + 1}

    def _pending(self, row, version):
        if row["status"] != "pending" or row["expires_at"] <= self.clock.now():
            raise DomainError("Draf sudah diproses atau kedaluwarsa. Buat draf baru.")
        if row["version"] != version:
            raise DomainError("Draf sudah berubah. Buka lagi lewat /uang draf.")

    async def cancel_draft(self, actor, action_id, version):
        async with self.db.write() as s:
            row = await resolve_owned(s, actions, action_id, actions.c.owner_id, actor.membership)
            self._pending(row, version)
            await s.execute(update(actions).where(actions.c.id == row["id"]).values(status="cancelled"))

    async def category(self, s, ledger_id, item):
        row = await one(
            s,
            select(categories).where(
                categories.c.ledger_id == ledger_id,
                categories.c.name == item["category"],
                categories.c.kind == item["kind"],
            ),
        )
        if row:
            return row["id"]
        category_id = uid()
        await s.execute(
            insert(categories).values(
                id=category_id, ledger_id=ledger_id, name=item["category"], kind=item["kind"]
            )
        )
        return category_id

    async def confirm(self, actor, action_id, version):
        async with self.db.write() as s:
            action = await resolve_owned(s, actions, action_id, actions.c.owner_id, actor.membership)
            if action["status"] == "succeeded":
                return action["result"]
            self._pending(action, version)
            ledger = await self.ledger(s, actor)
            now = self.clock.now()
            result = {"ids": [], "operation": action["kind"]}
            if action["kind"] == "finance.create":
                for index, raw in enumerate(action["payload"]["items"]):
                    item = MoneyItem.model_validate(raw).model_dump(mode="json")
                    tx = dict(
                        id=uid(),
                        ledger_id=ledger["id"],
                        category_id=await self.category(s, ledger["id"], item),
                        action_id=action["id"],
                        item_index=index,
                        amount=item["amount"],
                        kind=item["kind"],
                        occurred_on=item["occurred_on"],
                        note=item["note"],
                        status="posted",
                        version=1,
                        created_at=now,
                    )
                    await s.execute(insert(transactions).values(**tx))
                    await audit(s, actor, action["id"], "transaction", tx["id"], "create", now, after=tx)
                    result["ids"].append(tx["id"])
            elif action["kind"] in {"finance.edit", "finance.void", "finance.delete"}:
                payload = action["payload"]
                tx = await resolve_owned(
                    s, transactions, payload["id"], transactions.c.ledger_id, ledger["id"]
                )
                if (
                    tx["version"] != payload["version"]
                    or tx["status"] == "deleted"
                    or (action["kind"] != "finance.delete" and tx["status"] != "posted")
                ):
                    raise DomainError("Transaksi sudah berubah. Buat draf koreksi baru.")
                values = {"version": tx["version"] + 1}
                if action["kind"] == "finance.delete":
                    values["status"] = "deleted"
                elif action["kind"] == "finance.void":
                    values["status"] = "voided"
                else:
                    item = MoneyItem.model_validate(payload["item"]).model_dump(mode="json")
                    values.update({k: item[k] for k in ("amount", "kind", "occurred_on", "note")})
                    values["category_id"] = await self.category(s, ledger["id"], item)
                await s.execute(update(transactions).where(transactions.c.id == tx["id"]).values(**values))
                await audit(
                    s,
                    actor,
                    action["id"],
                    "transaction",
                    tx["id"],
                    action["kind"],
                    now,
                    before=tx,
                    after={**tx, **values},
                )
                result["ids"].append(tx["id"])
            else:
                raise DomainError("Jenis draf tidak didukung.")
            await s.execute(
                update(actions).where(actions.c.id == action["id"]).values(status="succeeded", result=result)
            )
            return result

    @staticmethod
    def bounds(period, zone, now):
        if not period or period == "hari_ini":
            start = datetime.fromtimestamp(now, ZoneInfo(zone)).date()
            end = start + timedelta(days=1)
        elif len(period) == 7:
            try:
                start = date.fromisoformat(period + "-01")
                end = date(start.year + (start.month == 12), 1 if start.month == 12 else start.month + 1, 1)
            except ValueError:
                raise DomainError("Periode harus YYYY-MM atau YYYY-MM-DD.") from None
        else:
            try:
                start = date.fromisoformat(period)
                end = start + timedelta(days=1)
            except ValueError:
                raise DomainError("Periode harus YYYY-MM atau YYYY-MM-DD.") from None
        return start.isoformat(), end.isoformat()

    async def report(self, actor, period=None):
        async with self.db.write() as s:
            ledger = await self.ledger(s, actor)
            start, end = self.bounds(period, ledger["timezone"], self.clock.now())
            where = (
                transactions.c.ledger_id == ledger["id"],
                transactions.c.status == "posted",
                transactions.c.occurred_on >= start,
                transactions.c.occurred_on < end,
            )
            sums = (
                await s.execute(
                    select(
                        func.coalesce(
                            func.sum(case((transactions.c.kind == "income", transactions.c.amount), else_=0)),
                            0,
                        ),
                        func.coalesce(
                            func.sum(
                                case((transactions.c.kind == "expense", transactions.c.amount), else_=0)
                            ),
                            0,
                        ),
                        func.count(),
                    )
                    .select_from(transactions)
                    .where(*where)
                )
            ).one()
            grouped = await all_rows(
                s,
                select(categories.c.name, transactions.c.kind, func.sum(transactions.c.amount).label("total"))
                .join(categories, categories.c.id == transactions.c.category_id)
                .where(*where)
                .group_by(categories.c.name, transactions.c.kind),
            )
            return dict(
                start=start,
                end=end,
                income=int(sums[0]),
                expense=int(sums[1]),
                net=int(sums[0] - sums[1]),
                count=sums[2],
                categories=grouped,
            )

    async def history(self, actor, period=None, page=0, limit=10):
        async with self.db.write() as s:
            ledger = await self.ledger(s, actor)
            statement = (
                select(transactions, categories.c.name.label("category"))
                .join(categories, categories.c.id == transactions.c.category_id)
                .where(transactions.c.ledger_id == ledger["id"], transactions.c.status != "deleted")
            )
            if period:
                start, end = self.bounds(period, ledger["timezone"], self.clock.now())
                statement = statement.where(
                    transactions.c.occurred_on >= start, transactions.c.occurred_on < end
                )
            return await all_rows(
                s,
                statement.order_by(
                    transactions.c.occurred_on.desc(), transactions.c.created_at.desc(), transactions.c.id
                )
                .offset(max(0, page) * limit)
                .limit(limit),
            )

    async def detail(self, actor, txid):
        async with self.db.write() as s:
            ledger = await self.ledger(s, actor)
            tx = await resolve_owned(s, transactions, txid, transactions.c.ledger_id, ledger["id"])
            category = await one(s, select(categories).where(categories.c.id == tx["category_id"]))
            history = await all_rows(
                s,
                select(audits)
                .where(audits.c.entity_id == tx["id"], audits.c.owner_id == actor.membership)
                .order_by(audits.c.created_at, audits.c.id),
            )
            return {**tx, "category": category["name"], "audit": history}

    async def export(self, actor, period):
        rows = await self.history(actor, period, limit=10001)
        if len(rows) > 10000:
            raise DomainError("Ekspor melebihi 10.000 baris. Pilih periode harian yang lebih kecil.")
        stream = io.StringIO(newline="")
        writer = csv.writer(stream)
        writer.writerow(["id", "tanggal", "jenis", "rupiah", "kategori", "catatan", "status"])

        def safe(value):
            value = str(value)
            return (
                "'" + value
                if (value.startswith(("\t", "\r", "\n")) or value.lstrip().startswith(("=", "+", "-", "@")))
                else value
            )

        for row in rows:
            writer.writerow(
                [safe(row[k]) for k in ("id", "occurred_on", "kind", "amount", "category", "note", "status")]
            )
        return ("\ufeff" + stream.getvalue()).encode("utf-8")


def describe_draft(row):
    if row["kind"] == "finance.create":
        items = row["payload"]["items"]
        lines = [
            f"{i + 1}. {x['occurred_on']} · {x['kind']} · {rupiah(x['amount'])} · {x['category']}\n{x['note'][:100]}"
            for i, x in enumerate(items)
        ]
        return (
            "Draf transaksi — belum disimpan\n"
            + "\n".join(lines)
            + "\nTotal nominal: "
            + rupiah(sum(x["amount"] for x in items))
        )
    if row["kind"] == "finance.void":
        return "Batalkan transaksi " + row["payload"]["id"][:8] + "? Riwayat audit akan tetap ada."
    x = row["payload"]["item"]
    return f"Koreksi {row['payload']['id'][:8]} → {x['occurred_on']} · {rupiah(x['amount'])} · {x['category']} · {x['note'][:100]}"
