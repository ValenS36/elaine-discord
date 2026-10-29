from datetime import datetime, timedelta, time
from zoneinfo import ZoneInfo

from sqlalchemy import insert, select, update, or_, and_

from .actions import audit, new_action, resolve_owned
from .core import DomainError, uid
from .db import actions, reminders, occurrences, deliveries, memberships, users, one, all_rows
from .schemas import ReminderInput


def local_timestamp(day, clock_text, zone):
    naive = datetime.combine(day, time.fromisoformat(clock_text))
    local = naive.replace(tzinfo=ZoneInfo(zone), fold=0)
    roundtrip = datetime.fromtimestamp(local.timestamp(), ZoneInfo(zone)).replace(tzinfo=None)
    if roundtrip != naive:
        raise DomainError("Jam lokal tidak tersedia karena perubahan timezone. Pilih jam lain.")
    if local.utcoffset() != naive.replace(tzinfo=ZoneInfo(zone), fold=1).utcoffset():
        raise DomainError("Jam lokal ambigu karena perubahan timezone. Pilih jam lain.")
    return int(local.timestamp())


def future_due(reminder, after):
    """First recurrence strictly after a UTC instant; compute from local calendar."""
    if reminder["recurrence"] == "none":
        return None
    local_day = datetime.fromtimestamp(after, ZoneInfo(reminder["timezone"])).date()
    for offset in range(9):
        day = local_day + timedelta(days=offset)
        if reminder["recurrence"] == "weekly" and day.weekday() not in reminder["weekdays"]:
            continue
        try:
            candidate = local_timestamp(day, reminder["local_time"], reminder["timezone"])
        except DomainError:
            continue
        if candidate > after:
            return candidate
    raise DomainError("Jadwal berulang berikutnya tidak ditemukan.")


def latest_due(reminder, now):
    local_day = datetime.fromtimestamp(now, ZoneInfo(reminder["timezone"])).date()
    for offset in range(9):
        day = local_day - timedelta(days=offset)
        if reminder["recurrence"] == "weekly" and day.weekday() not in reminder["weekdays"]:
            continue
        try:
            candidate = local_timestamp(day, reminder["local_time"], reminder["timezone"])
        except DomainError:
            continue
        if candidate <= now:
            return candidate
    return reminder["next_due"]


def display_time(timestamp, zone):
    return datetime.fromtimestamp(timestamp, ZoneInfo(zone)).strftime("%d-%m-%Y %H:%M") + " " + zone


class Reminders:
    def __init__(self, db, clock, alarm_interval=300, alarm_max=3):
        self.db, self.clock = db, clock
        self.alarm_interval, self.alarm_max = alarm_interval, alarm_max

    def normalize(self, actor, raw):
        item = ReminderInput.model_validate(raw)
        due = local_timestamp(item.local_date, item.local_time, actor.timezone)
        if due <= self.clock.now():
            raise DomainError("Waktu tersebut sudah lewat. Pilih tanggal dan jam yang akan datang.")
        days = item.weekdays or [item.local_date.weekday()]
        if item.recurrence == "weekly" and item.local_date.weekday() not in days:
            raise DomainError("Hari tanggal pertama harus termasuk hari pengulangan.")
        return item, due, days

    async def create(self, actor, raw, source, voice_channel_id=None):
        async with self.db.write() as s:
            action = await new_action(s, actor, source, "reminder.create", raw, self.clock.now())
            if action["status"] == "succeeded":
                return action["result"]
            item, due, days = self.normalize(actor, raw)
            if voice_channel_id and item.mode != "alarm":
                raise DomainError("Alarm suara membutuhkan jenis alarm.")
            row = dict(
                id=uid(),
                owner_id=actor.membership,
                action_id=action["id"],
                title=item.title,
                timezone=actor.timezone,
                local_time=item.local_time,
                recurrence=item.recurrence,
                weekdays=days,
                next_due=due,
                channel_id=actor.channel,
                voice_channel_id=str(voice_channel_id) if voice_channel_id else None,
                mode=item.mode,
                max_sends=self.alarm_max if item.mode == "alarm" else 1,
                interval_seconds=self.alarm_interval,
                status="active",
                version=1,
                created_at=self.clock.now(),
            )
            await s.execute(insert(reminders).values(**row))
            await self._occurrence(s, row, due)
            await audit(s, actor, action["id"], "reminder", row["id"], "create", self.clock.now(), after=row)
            await s.execute(
                update(actions).where(actions.c.id == action["id"]).values(status="succeeded", result=row)
            )
            return row

    async def _occurrence(self, s, reminder, due, late=0):
        current = await one(
            s,
            select(occurrences).where(
                occurrences.c.reminder_id == reminder["id"],
                occurrences.c.scheduled_at == due,
                occurrences.c.series_version == reminder["version"],
            ),
        )
        if current:
            return dict(current)
        row = dict(
            id=uid(),
            reminder_id=reminder["id"],
            scheduled_at=due,
            effective_at=due,
            series_version=reminder["version"],
            revision=1,
            status="scheduled",
            send_count=0,
            next_fire=due,
            acknowledged_at=None,
            completed_at=None,
            late=late,
        )
        await s.execute(insert(occurrences).values(**row))
        return row

    async def _with_latest(self, session, row):
        row = dict(row)
        row["last_occurrence"] = await one(
            session,
            select(occurrences)
            .where(occurrences.c.reminder_id == row["id"], occurrences.c.series_version == row["version"])
            .order_by(occurrences.c.scheduled_at.desc())
            .limit(1),
        )
        if row["last_occurrence"]:
            row["last_occurrence"] = dict(row["last_occurrence"])
        return row

    async def list(self, actor, page=0, limit=5):
        async with self.db.read() as s:
            rows = await all_rows(
                s,
                select(reminders)
                .where(reminders.c.owner_id == actor.membership)
                .order_by(reminders.c.created_at.desc(), reminders.c.id)
                .offset(max(0, page) * limit)
                .limit(limit),
            )
            return [await self._with_latest(s, row) for row in rows]

    async def detail(self, actor, prefix):
        async with self.db.read() as s:
            row = await resolve_owned(s, reminders, prefix, reminders.c.owner_id, actor.membership)
            return await self._with_latest(s, row)

    async def cancel(self, actor, prefix, source):
        async with self.db.write() as s:
            row = await resolve_owned(s, reminders, prefix, reminders.c.owner_id, actor.membership)
            action = await new_action(
                s, actor, source, "reminder.cancel", {"id": row["id"]}, self.clock.now()
            )
            if action["status"] == "succeeded" or row["status"] == "cancelled":
                return row
            await self._cancel_pending(s, row["id"])
            after = {**row, "status": "cancelled", "next_due": None, "version": row["version"] + 1}
            await s.execute(
                update(reminders)
                .where(reminders.c.id == row["id"])
                .values(status="cancelled", next_due=None, version=after["version"])
            )
            await audit(
                s,
                actor,
                action["id"],
                "reminder",
                row["id"],
                "cancel",
                self.clock.now(),
                before=row,
                after=after,
            )
            await s.execute(
                update(actions).where(actions.c.id == action["id"]).values(status="succeeded", result=after)
            )
            return after

    async def change(self, actor, prefix, raw, source, expected_version=None):
        async with self.db.write() as s:
            row = await resolve_owned(s, reminders, prefix, reminders.c.owner_id, actor.membership)
            action = await new_action(
                s, actor, source, "reminder.edit", {"id": row["id"], "raw": raw}, self.clock.now()
            )
            if action["status"] == "succeeded":
                return action["result"]
            if expected_version is not None and row["version"] != expected_version:
                raise DomainError("Pengingat sudah berubah. Buka formulir baru.")
            item, due, days = self.normalize(actor, raw)
            await self._cancel_pending(s, row["id"])
            changes = dict(
                title=item.title,
                timezone=actor.timezone,
                local_time=item.local_time,
                recurrence=item.recurrence,
                weekdays=days,
                next_due=due,
                mode=item.mode,
                max_sends=self.alarm_max if item.mode == "alarm" else 1,
                interval_seconds=self.alarm_interval,
                status="active",
                voice_channel_id=row.get("voice_channel_id") if item.mode == "alarm" else None,
                version=row["version"] + 1,
            )
            # Keep original destination; UI cannot silently move reminders to another channel.
            after = {**row, **changes}
            await s.execute(update(reminders).where(reminders.c.id == row["id"]).values(**changes))
            await self._occurrence(s, after, due)
            await audit(
                s,
                actor,
                action["id"],
                "reminder",
                row["id"],
                "edit",
                self.clock.now(),
                before=row,
                after=after,
            )
            await s.execute(
                update(actions).where(actions.c.id == action["id"]).values(status="succeeded", result=after)
            )
            return after

    async def _cancel_pending(self, s, reminder_id):
        ids = select(occurrences.c.id).where(
            occurrences.c.reminder_id == reminder_id,
            occurrences.c.status.in_(["scheduled", "notified", "acknowledged", "failed"]),
        )
        await s.execute(
            update(deliveries)
            .where(deliveries.c.occurrence_id.in_(ids), deliveries.c.status.in_(["pending", "sending"]))
            .values(status="cancelled")
        )
        await s.execute(
            update(occurrences)
            .where(occurrences.c.id.in_(ids))
            .values(status="cancelled", next_fire=None, revision=occurrences.c.revision + 1)
        )

    async def occurrence_action(self, actor, occurrence_id, verb, revision, minutes=10):
        if verb not in {"ack", "done", "snooze"} or not 1 <= minutes <= 1440:
            raise DomainError("Tindakan pengingat tidak valid.")
        async with self.db.write() as s:
            occurrence = await one(
                s,
                select(occurrences)
                .join(reminders)
                .where(occurrences.c.id == occurrence_id, reminders.c.owner_id == actor.membership),
            )
            if not occurrence:
                raise DomainError("Pengingat tidak ditemukan atau bukan milikmu.")
            occurrence = dict(occurrence)
            if occurrence["status"] == "completed":
                if verb == "done":
                    return occurrence
                raise DomainError("Tugas sudah selesai. Buat pengingat baru untuk menjadwalkan lagi.")
            if verb == "ack" and occurrence["status"] == "acknowledged":
                return occurrence
            if occurrence["revision"] != revision or occurrence["status"] not in {
                "scheduled",
                "notified",
                "acknowledged",
            }:
                raise DomainError("Tombol sudah kedaluwarsa. Gunakan /ingat daftar untuk status terbaru.")
            now = self.clock.now()
            values = {"revision": revision + 1, "next_fire": None}
            if verb == "snooze":
                values.update(
                    status="scheduled",
                    effective_at=now + minutes * 60,
                    next_fire=now + minutes * 60,
                    send_count=0,
                    acknowledged_at=None,
                )
            else:
                values.update(status="completed" if verb == "done" else "acknowledged", acknowledged_at=now)
                if verb == "done":
                    values["completed_at"] = now
            await s.execute(
                update(deliveries)
                .where(
                    deliveries.c.occurrence_id == occurrence_id,
                    deliveries.c.status.in_(["pending", "sending"]),
                )
                .values(status="cancelled")
            )
            await s.execute(update(occurrences).where(occurrences.c.id == occurrence_id).values(**values))
            reminder = await one(s, select(reminders).where(reminders.c.id == occurrence["reminder_id"]))
            if reminder["recurrence"] == "none":
                await s.execute(
                    update(reminders)
                    .where(reminders.c.id == reminder["id"])
                    .values(
                        status={"done": "completed", "ack": "acknowledged", "snooze": "active"}[verb],
                        next_due=None,
                    )
                )
            await audit(
                s,
                actor,
                None,
                "occurrence",
                occurrence_id,
                verb,
                now,
                before=occurrence,
                after={**occurrence, **values},
            )
            return {**occurrence, **values}

    async def active_occurrences(self, actor, reminder_id):
        async with self.db.read() as s:
            return await all_rows(
                s,
                select(occurrences)
                .join(reminders)
                .where(
                    reminders.c.owner_id == actor.membership,
                    reminders.c.id == reminder_id,
                    occurrences.c.status.in_(["scheduled", "notified", "acknowledged", "failed"]),
                )
                .order_by(occurrences.c.scheduled_at.desc())
                .limit(5),
            )

    async def tick(self):
        """DB-only scheduler: never call AI or Discord here."""
        now = self.clock.now()
        async with self.db.write() as s:
            due_rows = await all_rows(
                s,
                select(reminders)
                .where(reminders.c.status == "active", reminders.c.next_due <= now)
                .order_by(reminders.c.next_due)
                .limit(200),
            )
            for row in due_rows:
                scheduled = row["next_due"]
                if row["recurrence"] != "none":
                    latest = latest_due(row, now)
                    if latest > scheduled:
                        await s.execute(
                            update(occurrences)
                            .where(
                                occurrences.c.reminder_id == row["id"],
                                occurrences.c.status == "scheduled",
                                occurrences.c.revision == 1,
                                occurrences.c.scheduled_at < latest,
                            )
                            .values(status="missed", next_fire=None)
                        )
                        scheduled = latest
                occ = await self._occurrence(s, row, scheduled, late=int(now - scheduled > 60))
                if now - scheduled > 86400:
                    await s.execute(
                        update(occurrences)
                        .where(occurrences.c.id == occ["id"])
                        .values(status="missed", next_fire=None)
                    )
                    if row["recurrence"] == "none":
                        await s.execute(
                            update(reminders).where(reminders.c.id == row["id"]).values(status="missed")
                        )
                elif now - scheduled > 60:
                    await s.execute(update(occurrences).where(occurrences.c.id == occ["id"]).values(late=1))
                next_due = future_due(row, now)
                await s.execute(
                    update(reminders).where(reminders.c.id == row["id"]).values(next_due=next_due)
                )
            fires = await all_rows(
                s,
                select(occurrences)
                .join(reminders)
                .where(
                    reminders.c.status == "active",
                    occurrences.c.status.in_(["scheduled", "notified"]),
                    occurrences.c.next_fire <= now,
                )
                .order_by(occurrences.c.next_fire)
                .limit(200),
            )
            for occ in fires:
                event_key = f"{occ['id']}:{occ['revision']}:{occ['send_count']}"
                existing = await one(s, select(deliveries.c.id).where(deliveries.c.event_key == event_key))
                if not existing:
                    await s.execute(
                        insert(deliveries).values(
                            id=uid(),
                            occurrence_id=occ["id"],
                            event_key=event_key,
                            revision=occ["revision"],
                            status="pending",
                            attempts=0,
                            next_attempt=now,
                            lease_until=None,
                            message_id=None,
                            error_code=None,
                            created_at=now,
                        )
                    )
                await s.execute(
                    update(occurrences).where(occurrences.c.id == occ["id"]).values(next_fire=None)
                )

    async def claim(self):
        now = self.clock.now()
        async with self.db.write() as s:
            delivery = await one(
                s,
                select(deliveries)
                .where(
                    or_(
                        and_(deliveries.c.status == "pending", deliveries.c.next_attempt <= now),
                        and_(deliveries.c.status == "sending", deliveries.c.lease_until <= now),
                    )
                )
                .order_by(deliveries.c.next_attempt)
                .limit(1),
            )
            if not delivery:
                return None
            occurrence = await one(
                s, select(occurrences).where(occurrences.c.id == delivery["occurrence_id"])
            )
            reminder = await one(s, select(reminders).where(reminders.c.id == occurrence["reminder_id"]))
            if (
                occurrence["revision"] != delivery["revision"]
                or occurrence["status"] not in {"scheduled", "notified"}
                or reminder["status"] != "active"
            ):
                await s.execute(
                    update(deliveries).where(deliveries.c.id == delivery["id"]).values(status="cancelled")
                )
                return None
            owner = await one(
                s,
                select(users.c.discord_id)
                .join(memberships, users.c.id == memberships.c.user_id)
                .where(memberships.c.id == reminder["owner_id"]),
            )
            attempt = delivery["attempts"] + 1
            await s.execute(
                update(deliveries)
                .where(deliveries.c.id == delivery["id"])
                .values(status="sending", attempts=attempt, lease_until=now + 120)
            )
            return {
                "delivery": {**dict(delivery), "attempts": attempt},
                "occurrence": dict(occurrence),
                "reminder": dict(reminder),
                "discord_user_id": owner["discord_id"],
            }

    async def occurrence_valid(self, occurrence_id, revision):
        async with self.db.read() as s:
            return bool(
                await one(
                    s,
                    select(occurrences.c.id)
                    .select_from(occurrences.join(reminders, occurrences.c.reminder_id == reminders.c.id))
                    .where(
                        occurrences.c.id == occurrence_id,
                        occurrences.c.revision == revision,
                        occurrences.c.status.in_(["scheduled", "notified"]),
                        reminders.c.status == "active",
                    ),
                )
            )

    async def still_valid(self, delivery_id):
        async with self.db.read() as s:
            row = await one(
                s,
                select(deliveries.c.id)
                .select_from(
                    deliveries.join(occurrences, deliveries.c.occurrence_id == occurrences.c.id).join(
                        reminders, occurrences.c.reminder_id == reminders.c.id
                    )
                )
                .where(
                    deliveries.c.id == delivery_id,
                    deliveries.c.status == "sending",
                    deliveries.c.revision == occurrences.c.revision,
                    occurrences.c.status.in_(["scheduled", "notified"]),
                    reminders.c.status == "active",
                ),
            )
            return bool(row)

    async def delivered(self, delivery_id, message_id):
        now = self.clock.now()
        async with self.db.write() as s:
            delivery = await one(s, select(deliveries).where(deliveries.c.id == delivery_id))
            if not delivery or delivery["status"] == "sent":
                return
            await s.execute(
                update(deliveries)
                .where(deliveries.c.id == delivery_id)
                .values(status="sent", message_id=str(message_id), lease_until=None)
            )
            occ = await one(s, select(occurrences).where(occurrences.c.id == delivery["occurrence_id"]))
            rem = await one(s, select(reminders).where(reminders.c.id == occ["reminder_id"]))
            if (
                occ["revision"] != delivery["revision"]
                or occ["status"] not in {"scheduled", "notified"}
                or rem["status"] != "active"
            ):
                return
            count = occ["send_count"] + 1
            await s.execute(
                update(occurrences)
                .where(occurrences.c.id == occ["id"])
                .values(
                    status="notified",
                    send_count=count,
                    next_fire=now + rem["interval_seconds"] if count < rem["max_sends"] else None,
                )
            )

    async def delivery_failed(self, delivery_id, code, permanent=False, retry_after=None):
        now = self.clock.now()
        async with self.db.write() as s:
            row = await one(s, select(deliveries).where(deliveries.c.id == delivery_id))
            if not row or row["status"] != "sending":
                return
            final = permanent or row["attempts"] >= 5
            delay = max(5, int(retry_after or min(600, 5 * 2 ** row["attempts"])))
            await s.execute(
                update(deliveries)
                .where(deliveries.c.id == delivery_id)
                .values(
                    status="failed" if final else "pending",
                    next_attempt=now + delay,
                    lease_until=None,
                    error_code=str(code)[:60],
                )
            )
            if final:
                await s.execute(
                    update(occurrences)
                    .where(
                        occurrences.c.id == row["occurrence_id"],
                        occurrences.c.revision == row["revision"],
                        occurrences.c.status.in_(["scheduled", "notified"]),
                    )
                    .values(status="failed", next_fire=None)
                )


def reminder_receipt(row):
    return (
        f"Pengingat tersimpan · ID {row['id'][:8]}\n{row['title']}\n"
        f"{display_time(row['next_due'], row['timezone'])}\n"
        f"Jenis: {row['mode']} · Pengulangan: {row['recurrence']}"
    )
