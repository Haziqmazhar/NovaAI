"""
discord_agent.py — Nova's optional third input/output channel: a Discord
bot providing "mouth and ears" via Discord messages, plus a small set of
server-management actions (channels, messages) scoped to one whitelisted
server.

Two hard gates keep this bounded the same way every other Nova capability
is:
- _is_authorized() is a deterministic, code-level check run before
  anything reaches GPT — only one whitelisted Discord user's messages are
  ever treated as commands, whether sent as a DM or in the whitelisted
  server. This is never an LLM judgment call.
- Every management action resolves its target only inside the one
  whitelisted guild (config["discord_guild_id"]) — even if the bot is
  later added to another server, nothing here will act in it.

The bot's actual Discord-granted permissions are also deliberately
minimal (Manage Channels, Manage Messages, Send Messages, View Channels,
Read Message History — no Kick/Ban/Manage Roles/Administrator; see
README). That grant, not this code, is the real backstop if the bot
token itself is ever compromised, so keeping it minimal matters
independently of everything below.
"""

import asyncio

import discord

MESSAGE_CHUNK_SIZE = 1900  # headroom under Discord's 2000-char message limit
CALL_TIMEOUT_SECONDS = 15.0
HISTORY_SEARCH_LIMIT = 50  # how far back to look for the bot's own last message


def _is_authorized(message, config: dict) -> bool:
    """Hard, deterministic gate — only this one Discord user's messages
    are ever treated as commands, and only via DM or the one whitelisted
    guild. Never involves GPT judgment."""
    owner_id = config.get("discord_owner_id") or ""
    guild_id = config.get("discord_guild_id") or ""
    if not owner_id or str(message.author.id) != str(owner_id):
        return False
    if message.guild is None:
        return True  # DM from the owner
    return str(message.guild.id) == str(guild_id)


class DiscordAgent:
    """Owns the bot connection. Created once in main.py and handed a
    reference to Brain (to route authorized messages into the same
    orchestrator every other input channel uses), then handed back to
    Brain (brain.discord_agent) so its tool handlers can call the
    synchronous send/create/delete/rename methods below — possibly from a
    different thread than the bot's own event loop, so every method
    bridges via asyncio.run_coroutine_threadsafe rather than assuming
    it's already running on the bot's loop."""

    def __init__(self, config: dict, brain):
        self.config = config
        self.brain = brain
        intents = discord.Intents.default()
        intents.message_content = True
        intents.guilds = True
        self.client = discord.Client(intents=intents)
        self.client.event(self.on_ready)
        self.client.event(self.on_message)

    async def on_ready(self):
        print(f"[Nova] Discord bot connected as {self.client.user}.")

    async def on_message(self, message):
        if self.client.user and message.author.id == self.client.user.id:
            return  # never react to the bot's own messages
        if not _is_authorized(message, self.config):
            return

        loop = asyncio.get_running_loop()
        reply = await loop.run_in_executor(None, self.brain.respond, message.content)
        if reply:
            await self._send_chunked(message.channel, reply)

    async def _send_chunked(self, channel, text: str):
        for i in range(0, len(text), MESSAGE_CHUNK_SIZE):
            await channel.send(text[i:i + MESSAGE_CHUNK_SIZE])

    def run(self, token: str):
        """Blocking — call this on its own thread. Never raises; a
        connection failure is caught and logged so Nova keeps running
        without Discord rather than crashing."""
        try:
            self.client.run(token)
        except Exception as e:
            print(f"[Nova] Discord bot failed to start: {e}")

    # ---- server-management actions — synchronous entry points called
    # from Brain's tool handlers, bridging onto the bot's own event loop ----

    def _run_sync(self, coro):
        if not self.client.is_ready():
            coro.close()
            return "The Discord bot isn't connected yet — try again in a moment."
        future = asyncio.run_coroutine_threadsafe(coro, self.client.loop)
        try:
            return future.result(timeout=CALL_TIMEOUT_SECONDS)
        except Exception as e:
            return f"Discord action failed: {e}"

    def _get_guild(self):
        guild_id = self.config.get("discord_guild_id") or ""
        if not guild_id:
            return None
        return self.client.get_guild(int(guild_id))

    def send_message(self, channel_name: str, text: str) -> str:
        return self._run_sync(self._send_message(channel_name, text))

    async def _send_message(self, channel_name: str, text: str) -> str:
        guild = self._get_guild()
        if not guild:
            return "The whitelisted Discord server isn't configured, or the bot isn't in it."
        channel = discord.utils.get(guild.text_channels, name=channel_name)
        if not channel:
            known = ", ".join(c.name for c in guild.text_channels) or "none"
            return f"No channel named '{channel_name}' in the server. Channels available: {known}."
        await self._send_chunked(channel, text)
        return f"Sent to #{channel_name}."

    def create_channel(self, channel_name: str) -> str:
        return self._run_sync(self._create_channel(channel_name))

    async def _create_channel(self, channel_name: str) -> str:
        guild = self._get_guild()
        if not guild:
            return "The whitelisted Discord server isn't configured, or the bot isn't in it."
        if discord.utils.get(guild.text_channels, name=channel_name):
            return f"A channel named '{channel_name}' already exists."
        await guild.create_text_channel(channel_name)
        return f"Created #{channel_name}."

    def delete_channel(self, channel_name: str) -> str:
        return self._run_sync(self._delete_channel(channel_name))

    async def _delete_channel(self, channel_name: str) -> str:
        guild = self._get_guild()
        if not guild:
            return "The whitelisted Discord server isn't configured, or the bot isn't in it."
        channel = discord.utils.get(guild.text_channels, name=channel_name)
        if not channel:
            return f"No channel named '{channel_name}' in the server."
        await channel.delete()
        return f"Deleted #{channel_name}."

    def rename_channel(self, channel_name: str, new_name: str) -> str:
        return self._run_sync(self._rename_channel(channel_name, new_name))

    async def _rename_channel(self, channel_name: str, new_name: str) -> str:
        guild = self._get_guild()
        if not guild:
            return "The whitelisted Discord server isn't configured, or the bot isn't in it."
        channel = discord.utils.get(guild.text_channels, name=channel_name)
        if not channel:
            return f"No channel named '{channel_name}' in the server."
        await channel.edit(name=new_name)
        return f"Renamed #{channel_name} to #{new_name}."

    def delete_last_bot_message(self, channel_name: str) -> str:
        return self._run_sync(self._delete_last_bot_message(channel_name))

    async def _delete_last_bot_message(self, channel_name: str) -> str:
        guild = self._get_guild()
        if not guild:
            return "The whitelisted Discord server isn't configured, or the bot isn't in it."
        channel = discord.utils.get(guild.text_channels, name=channel_name)
        if not channel:
            return f"No channel named '{channel_name}' in the server."
        async for msg in channel.history(limit=HISTORY_SEARCH_LIMIT):
            if msg.author.id == self.client.user.id:
                await msg.delete()
                return f"Deleted my last message in #{channel_name}."
        return f"I don't have a recent message in #{channel_name} to delete."
