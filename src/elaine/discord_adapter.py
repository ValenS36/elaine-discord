import asyncio
import logging
import os
from contextlib import suppress

import discord
from discord import app_commands
from pydantic import ValidationError

from .bootstrap import build
from .commands import register_commands
from .core import DomainError
from .finance import describe_draft
from .legacy import import_legacy
from .reminders import display_time
from .views import ActionButton, button_view

LOG = logging.getLogger("elaine")


class ElaineClient(discord.Client):
    def __init__(self, settings):
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(intents=intents, allowed_mentions=discord.AllowedMentions.none())
        self.settings = settings
        self.app, self.db = build(settings)
        self.tree = app_commands.CommandTree(self)
        register_commands(self)
        self.add_dynamic_items(ActionButton)
        self.worker_task = None
        self.initialized = False
        self.ready_lock = asyncio.Lock()
        self.channel_locks = {}
        self.pending = {}
        self.last_call = {}
        self.fatal_error = None

    async def setup_hook(self):
        await self.db.check()

    async def actor(self, interaction):
        if not interaction.guild or not interaction.channel:
            raise DomainError("Command ini digunakan di server Discord, bukan DM.")
        if not interaction.channel.permissions_for(interaction.user).view_channel:
            raise DomainError("Akses channel tidak tersedia.")
        return await self.app.identity.actor(
            interaction.guild_id,
            interaction.user.id,
            interaction.user.display_name,
            interaction.channel_id,
            admin=interaction.user.guild_permissions.manage_guild,
        )

    async def check_send_permission(self, channel, member):
        if not isinstance(channel, (discord.TextChannel, discord.Thread)):
            raise DomainError("Gunakan text channel untuk pengingat.")
        perms = channel.permissions_for(channel.guild.me)
        send = perms.send_messages_in_threads if isinstance(channel, discord.Thread) else perms.send_messages
        if not perms.view_channel or not send or not channel.permissions_for(member).view_channel:
            raise DomainError("Bot atau pemilik pengingat tidak dapat mengakses/mengirim ke channel ini.")

    async def respond_error(self, interaction, error):
        if isinstance(error, DomainError):
            message = str(error)
        elif isinstance(error, (ValidationError, ValueError)):
            message = "Input tidak valid. Periksa nominal, tanggal YYYY-MM-DD, jam HH:MM, atau jenis pilihan."
        else:
            LOG.error("Interaction failed: %s; interaction=%s", type(error).__name__, interaction.id)
            message = "Operasi belum berhasil. Periksa status atau log aplikasi; jangan anggap data sudah tersimpan."
        try:
            if interaction.response.is_done():
                await interaction.followup.send(message[:1900], ephemeral=True)
            else:
                await interaction.response.send_message(message[:1900], ephemeral=True)
        except discord.HTTPException:
            LOG.warning("Could not return interaction error: %s", interaction.id)

    async def private_text(self, interaction, text):
        for start in range(0, len(text), 1900):
            await interaction.followup.send(text[start : start + 1900], ephemeral=True)

    async def on_ready(self):
        async with self.ready_lock:
            if self.initialized:
                LOG.info("Discord reconnected")
                return
            try:
                channel = self.get_channel(int(self.settings.channel)) or await self.fetch_channel(
                    int(self.settings.channel)
                )
                if not isinstance(channel, discord.TextChannel):
                    raise RuntimeError("CHANNEL_ID harus text channel pada server")
                actor = await self.app.identity.actor(
                    channel.guild.id, self.user.id, "Elaine", channel.id, admin=True
                )
                if not await self.app.identity.binding(channel.id):
                    await self.app.identity.bind(actor, channel.id, "chat", self.settings.chat_mode)
                guild = discord.Object(id=channel.guild.id)
                self.tree.copy_global_to(guild=guild)
                await self.tree.sync(guild=guild)
                legacy = os.getenv("LEGACY_DB_PATH", "").strip()
                if legacy:
                    count = await import_legacy(self.app, legacy, channel.guild.id, channel.id)
                    LOG.info("Legacy rows inspected: %d (idempotent)", count)
                if self.settings.history_limit:
                    async for message in channel.history(
                        limit=self.settings.history_limit, oldest_first=False
                    ):
                        if message.author.bot and message.author.id != self.user.id:
                            continue
                        await self.app.memory.record(
                            actor,
                            message.id,
                            message.author.id,
                            message.author.display_name,
                            message.content,
                            int(message.created_at.timestamp()),
                            "assistant" if message.author.bot else "user",
                        )
                self.initialized = True
                self.worker_task = asyncio.create_task(self.worker_loop(), name="elaine-scheduler-delivery")
                LOG.info(
                    "Elaine siap | guild=%s | channel=%s | personality=bawaan", channel.guild.id, channel.id
                )
            except Exception as exc:
                LOG.error("Startup failed: %s", type(exc).__name__)
                self.fatal_error = exc
                await self.close()

    async def is_called(self, message, binding):
        if binding["reply_mode"] == "off":
            return False
        if binding["reply_mode"] == "auto" or self.user in message.mentions:
            return True
        if message.reference and message.reference.message_id:
            parent = message.reference.resolved
            if parent is None:
                with suppress(discord.HTTPException):
                    parent = await message.channel.fetch_message(message.reference.message_id)
            return isinstance(parent, discord.Message) and parent.author.id == self.user.id
        return False

    async def on_message(self, message):
        if not self.initialized or not message.guild or message.author.bot or message.webhook_id:
            return
        binding = await self.app.identity.binding(message.channel.id)
        if not binding or binding["reply_mode"] == "off" or not message.content:
            return
        try:
            actor = await self.app.identity.actor(
                message.guild.id, message.author.id, message.author.display_name, message.channel.id
            )
            await self.app.memory.record(
                actor,
                message.id,
                message.author.id,
                message.author.display_name,
                message.content,
                int(message.created_at.timestamp()),
            )
            if not await self.is_called(message, binding):
                return
            now = self.app.clock.now()
            key = (message.guild.id, message.author.id)
            if now - self.last_call.get(key, 0) < self.settings.cooldown:
                await message.reply(
                    "Sebentar, beri aku beberapa detik sebelum pertanyaan berikutnya.", mention_author=False
                )
                return
            channel_id = message.channel.id
            if self.pending.get(channel_id, 0) >= 3:
                await message.reply(
                    "Antrean percakapan sedang penuh. Coba lagi sebentar.", mention_author=False
                )
                return
            self.last_call[key] = now
            self.pending[channel_id] = self.pending.get(channel_id, 0) + 1
            lock = self.channel_locks.setdefault(channel_id, asyncio.Lock())
            try:
                async with lock:
                    await self.check_send_permission(message.channel, message.author)
                    question = (
                        message.content.replace(f"<@{self.user.id}>", "")
                        .replace(f"<@!{self.user.id}>", "")
                        .strip()
                    )
                    async with message.channel.typing():
                        result = await self.app.chat(
                            actor, question, message.id, int(message.created_at.timestamp())
                        )
                    view = None
                    text = result.text
                    if result.kind == "draft":
                        text = describe_draft(result.data)
                        view = button_view(
                            "d", result.data["id"], result.data["version"], ["save", "edit", "cancel"]
                        )
                    elif result.kind == "reminder":
                        view = button_view("r", result.data["id"], result.data["version"], ["edit", "cancel"])
                    # Service receipts can contain user text. Escape mass mentions through AllowedMentions.
                    chunks = [text[i : i + 1900] for i in range(0, min(len(text), 6000), 1900)] or [
                        "Aku di sini."
                    ]
                    for index, chunk in enumerate(chunks):
                        sent = await message.channel.send(
                            chunk, view=view if index == len(chunks) - 1 else None
                        )
                        if not result.sensitive:
                            await self.app.memory.record(
                                actor,
                                sent.id,
                                self.user.id,
                                "Elaine",
                                sent.content,
                                int(sent.created_at.timestamp()),
                                "assistant",
                            )
                    # Summary failure must never hide a successful action receipt.
                    try:
                        candidate = await self.app.memory.summary_candidate(actor)
                        if candidate:
                            conversation, rows = candidate
                            summary = await self.app.provider.summarize(rows)
                            await self.app.memory.save_summary(conversation, rows, summary)
                    except Exception as exc:
                        LOG.warning("Summary skipped: %s", type(exc).__name__)
            finally:
                self.pending[channel_id] -= 1
        except DomainError as exc:
            await message.channel.send(str(exc)[:1900])
        except Exception as exc:
            LOG.error("Message failed: %s; message=%s", type(exc).__name__, message.id)
            with suppress(discord.HTTPException):
                await message.channel.send(
                    "Aku belum berhasil memproses pesan itu. Gunakan command terkait atau periksa log."
                )

    async def on_raw_message_edit(self, payload):
        if "content" in payload.data:
            await self.app.memory.edit_or_delete(
                payload.channel_id, payload.message_id, payload.data["content"]
            )

    async def on_raw_message_delete(self, payload):
        await self.app.memory.edit_or_delete(payload.channel_id, payload.message_id)

    async def on_raw_bulk_message_delete(self, payload):
        for mid in payload.message_ids:
            await self.app.memory.edit_or_delete(payload.channel_id, mid)

    async def worker_loop(self):
        try:
            while not self.is_closed():
                await self.app.reminders.tick()
                for _ in range(20):
                    job = await self.app.reminders.claim()
                    if not job:
                        break
                    await self.deliver(job)
                await asyncio.sleep(self.settings.scheduler_seconds)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            LOG.error("Critical worker failed: %s", type(exc).__name__)
            self.fatal_error = exc
            asyncio.create_task(self.close())

    async def deliver(self, job):
        d, occurrence, reminder = job["delivery"], job["occurrence"], job["reminder"]
        try:
            channel = self.get_channel(int(reminder["channel_id"])) or await self.fetch_channel(
                int(reminder["channel_id"])
            )
            if not isinstance(channel, (discord.TextChannel, discord.Thread)):
                raise DomainError("destination_unavailable")
            member = channel.guild.get_member(
                int(job["discord_user_id"])
            ) or await channel.guild.fetch_member(int(job["discord_user_id"]))
            await self.check_send_permission(channel, member)
            if not await self.app.reminders.still_valid(d["id"]):
                return
            label = "Alarm" if reminder["mode"] == "alarm" else "Pengingat"
            late = " (terlambat setelah layanan kembali aktif)" if occurrence["late"] else ""
            content = (
                f"<@{job['discord_user_id']}> **{label}{late}**\n{reminder['title']}\n"
                f"Jadwal: {display_time(occurrence['effective_at'], reminder['timezone'])}\n"
                f"ID `{reminder['id'][:8]}` · Notifikasi {occurrence['send_count'] + 1}/{reminder['max_sends']}"
            )
            sent = await asyncio.wait_for(
                channel.send(
                    content,
                    view=button_view(
                        "o", occurrence["id"], occurrence["revision"], ["ack", "snooze", "done", "manage"]
                    ),
                    allowed_mentions=discord.AllowedMentions(
                        users=[discord.Object(id=int(job["discord_user_id"]))]
                    ),
                ),
                60,
            )
            await self.app.reminders.delivered(d["id"], sent.id)
        except (discord.Forbidden, discord.NotFound, DomainError) as exc:
            await self.app.reminders.delivery_failed(d["id"], type(exc).__name__, permanent=True)
        except (discord.HTTPException, asyncio.TimeoutError) as exc:
            await self.app.reminders.delivery_failed(d["id"], type(exc).__name__)

    async def close(self):
        task = self.worker_task
        if task and task is not asyncio.current_task() and not task.done():
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        await super().close()
        await self.app.provider.close()
        await self.db.close()
