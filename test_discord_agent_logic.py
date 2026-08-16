"""
Standalone test of discord_agent.py's authorization gate. Like the other
test_*_logic.py files, this has zero network dependency — it uses plain
fake objects standing in for discord.py's Message/Author/Guild, since
_is_authorized only ever reads .author.id and .guild/.guild.id.
"""
from discord_agent import _is_authorized

config = {"discord_owner_id": "111", "discord_guild_id": "222"}


class FakeUser:
    def __init__(self, id):
        self.id = id


class FakeGuild:
    def __init__(self, id):
        self.id = id


class FakeMessage:
    def __init__(self, author_id, guild_id=None):
        self.author = FakeUser(author_id)
        self.guild = FakeGuild(guild_id) if guild_id is not None else None


print("=== _is_authorized ===")

assert _is_authorized(FakeMessage(111, 222), config) is True
print("owner in whitelisted guild: OK")

assert _is_authorized(FakeMessage(111, None), config) is True
print("owner via DM: OK")

assert _is_authorized(FakeMessage(111, 999), config) is False
print("owner in a different guild: rejected")

assert _is_authorized(FakeMessage(999, 222), config) is False
print("non-owner in whitelisted guild: rejected")

assert _is_authorized(FakeMessage(999, None), config) is False
print("non-owner via DM: rejected")

assert _is_authorized(FakeMessage(111, 222), {"discord_owner_id": "", "discord_guild_id": "222"}) is False
print("unconfigured owner id: rejected")

# int vs str author id shouldn't matter — discord.py ids are ints, config
# values are stored as strings for copy-paste safety
assert _is_authorized(FakeMessage(111, 222), {"discord_owner_id": 111, "discord_guild_id": 222}) is True
print("int-typed config values still compare correctly: OK")

print("\nAll sanity assertions passed.")
