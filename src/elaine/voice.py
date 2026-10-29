"""Bounded voice alarm tasks; text delivery never depends on voice success."""

import asyncio
import ctypes.util
import math
import struct
from contextlib import suppress

import discord
from .core import DomainError


class AlarmAudio(discord.AudioSource):
    """Generated chime, 48 kHz stereo signed PCM. No shell, URL or media download."""

    def __init__(self, seconds=30):
        self.frame = 0
        self.frames = int(seconds * 50)

    def read(self):
        if self.frame >= self.frames:
            return b""
        samples = []
        for i in range(960):
            t = (self.frame * 960 + i) / 48000
            phase = t % 1.2
            envelope = min(1, phase / 0.03, max(0, (0.8 - phase) / 0.08)) if phase < 0.8 else 0
            tone = 660 if int(t / 0.4) % 2 == 0 else 880
            value = int(5500 * envelope * math.sin(2 * math.pi * tone * t))
            samples.extend((value, value))
        self.frame += 1
        return struct.pack("<1920h", *samples)


def voice_available():
    try:
        import nacl  # noqa: F401
        import davey  # noqa: F401

        if not discord.opus.is_loaded():
            lib = ctypes.util.find_library("opus")
            if not lib:
                return False
            discord.opus.load_opus(lib)
        return True
    except (ImportError, OSError):
        return False


class VoiceAlarms:
    def __init__(self, client):
        self.client = client
        self.active = {}
        self.manual_owners = {}
        self.locks = {}

    def validate(self, channel, member):
        if not isinstance(channel, discord.VoiceChannel) or channel.guild.id != member.guild.id:
            raise DomainError("Pilih voice channel biasa pada server ini, bukan Stage Channel.")
        if member.id not in channel.voice_states:
            raise DomainError(
                "Masuk ke voice channel tujuan dahulu. Alarm hanya berbunyi jika kamu ada di sana."
            )
        for who in (member, channel.guild.me):
            perms = channel.permissions_for(who)
            if not perms.view_channel or not perms.connect:
                raise DomainError("Pemilik alarm dan bot memerlukan izin View Channel dan Connect.")
        if not channel.permissions_for(channel.guild.me).speak:
            raise DomainError("Bot memerlukan izin Speak pada voice channel.")
        if not voice_available():
            raise DomainError("Dukungan suara belum lengkap. Install dependency voice dan libopus0 di VPS.")

    async def join(self, channel, member):
        lock = self.locks.setdefault(channel.guild.id, asyncio.Lock())
        async with lock:
            self.validate(channel, member)
            if channel.guild.id in self.active:
                raise DomainError("Ada alarm suara yang sedang berjalan pada server ini.")
            vc = channel.guild.voice_client
            if vc and vc.channel.id != channel.id:
                raise DomainError(
                    "Elaine masih terhubung ke channel lain. Pemilik sesi dapat memakai /suara keluar."
                )
            if not vc:
                vc = await channel.connect(timeout=20, reconnect=False, self_deaf=True)
            self.manual_owners[channel.guild.id] = member.id
            return vc

    def schedule(self, job, guild, report, test=False):
        if guild.id in self.active:
            return False
        task = asyncio.create_task(self._run(job, guild, report, test), name="elaine-voice-alarm")
        self.active[guild.id] = (job, task)

        def cleanup(done):
            if self.active.get(guild.id, (None, None))[1] is done:
                self.active.pop(guild.id, None)

        task.add_done_callback(cleanup)
        return True

    async def _run(self, job, guild, report, test):
        vc = None
        connecting = False
        message = "Alarm suara selesai; Elaine keluar dari channel."
        try:
            channel = self.client.get_channel(int(job["reminder"]["voice_channel_id"]))
            member = guild.get_member(int(job["discord_user_id"])) or await guild.fetch_member(
                int(job["discord_user_id"])
            )
            self.validate(channel, member)

            async def valid():
                return await self.client.app.reminders.occurrence_valid(
                    job["occurrence"]["id"], job["occurrence"]["revision"]
                )

            if not test and not await valid():
                return
            async with self.locks.setdefault(guild.id, asyncio.Lock()):
                existing = guild.voice_client
                if existing and existing.channel.id != channel.id:
                    raise DomainError("Elaine berada di voice channel lain; alarm teks tetap dikirim.")
                if existing:
                    vc = existing
                else:
                    connecting = True
                    vc = await channel.connect(timeout=20, reconnect=False, self_deaf=True)
                    connecting = False
            self.validate(channel, member)
            if not test and not await valid():
                return
            if vc.is_playing():
                raise DomainError("Audio lain sedang diputar; alarm teks tetap tersedia.")
            finished = asyncio.Event()
            errors = []
            loop = asyncio.get_running_loop()

            def after(error):
                if error:
                    errors.append(type(error).__name__)
                loop.call_soon_threadsafe(finished.set)

            vc.play(AlarmAudio(5 if test else 30), after=after)
            with suppress(discord.HTTPException):
                await report(
                    "Berbunyi di voice channel; otomatis berhenti maksimal "
                    + ("5" if test else "30")
                    + " detik."
                )
            for _ in range(6 if test else 31):
                if member.id not in channel.voice_states or (not test and not await valid()):
                    message = "Alarm suara dihentikan."
                    break
                try:
                    await asyncio.wait_for(finished.wait(), 1)
                    break
                except asyncio.TimeoutError:
                    pass
            if errors:
                message = "Pemutaran suara terputus; notifikasi teks tetap tersedia."
        except asyncio.CancelledError:
            message = "Alarm suara dihentikan."
            raise
        except DomainError as exc:
            message = str(exc)
        except Exception as exc:
            message = (
                "Alarm suara gagal ("
                + type(exc).__name__
                + "); periksa izin/koneksi voice. Notifikasi teks tetap tersedia."
            )
        finally:
            # connect() may attach a voice client before it is cancelled or raises.
            if (
                vc is None
                and connecting
                and guild.voice_client
                and guild.voice_client.channel.id == int(job["reminder"]["voice_channel_id"])
            ):
                vc = guild.voice_client
            if vc:
                with suppress(Exception):
                    vc.stop()
                    await asyncio.wait_for(vc.disconnect(force=True), 5)
            self.active.pop(guild.id, None)
            self.manual_owners.pop(guild.id, None)
            with suppress(Exception):
                await report(message)

    async def stop_occurrence(self, occurrence_id):
        jobs = [task for job, task in list(self.active.values()) if job["occurrence"]["id"] == occurrence_id]
        for task in jobs:
            task.cancel()
        if jobs:
            await asyncio.gather(*jobs, return_exceptions=True)

    async def stop_reminder(self, reminder_id):
        jobs = [task for job, task in list(self.active.values()) if job["reminder"]["id"] == reminder_id]
        for task in jobs:
            task.cancel()
        if jobs:
            await asyncio.gather(*jobs, return_exceptions=True)

    async def leave(self, member):
        active = self.active.get(member.guild.id)
        owner = int(active[0]["discord_user_id"]) if active else self.manual_owners.get(member.guild.id)
        if owner != member.id and not member.guild_permissions.manage_guild:
            raise DomainError("Hanya pemilik sesi suara atau admin yang dapat menghentikannya.")
        if active:
            active[1].cancel()
            await asyncio.gather(active[1], return_exceptions=True)
        if member.guild.voice_client:
            await member.guild.voice_client.disconnect(force=True)
        self.manual_owners.pop(member.guild.id, None)

    async def close(self):
        tasks = [t for _, t in self.active.values()]
        for t in tasks:
            t.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        for vc in list(self.client.voice_clients):
            with suppress(Exception):
                await asyncio.wait_for(vc.disconnect(force=True), 5)
