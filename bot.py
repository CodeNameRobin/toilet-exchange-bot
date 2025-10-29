# bot.py
import os
import discord
from discord.ext import commands
from dotenv import load_dotenv

from utils.errors import WrongChannel
from utils.logger import log_error

load_dotenv()

BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)

# =====================================================
# GLOBAL CHECK: Restrict all commands to #toilet-exchange
# =====================================================
@bot.check
async def only_in_exchange_channel(ctx):
    """Restrict all commands to the #toilet-exchange channel, except DMs and !feedback."""
    # -------------------------
    # Allow feedback command anywhere (intentionally DM-capable)
    # -------------------------
    if ctx.command and ctx.command.name == "feedback":
        return True

    # -------------------------
    # Handle DMs (non-feedback)
    # -------------------------
    if not ctx.guild:
        await ctx.send(
            f"⚠️ {ctx.author.display_name}, commands can’t be used in DMs.\n"
            "Please use them in your server’s **#toilet-exchange** channel."
        )
        # Log quietly without traceback
        await log_error(
            "CHANNEL_NOTICE",
            Exception(f"{ctx.author} tried to use a command in DM (ignored)."),
            ctx,
        )
        raise WrongChannel()

    # -------------------------
    # Enforce correct channel inside guild
    # -------------------------
    channel_name = getattr(ctx.channel, "name", None)
    if channel_name != "toilet-exchange":
        await ctx.send(
            f"⚠️ {ctx.author.display_name}, please use commands only in **#toilet-exchange**."
        )
        await log_error(
            "CHANNEL_NOTICE",
            Exception(f"{ctx.author} used command in #{channel_name} (ignored)."),
            ctx,
        )
        raise WrongChannel()

    # -------------------------
    # Everything else is valid
    # -------------------------
    return True

# =====================================================
# BOT SETUP
# =====================================================
initial_extensions = [
    "cogs.trading",
    "cogs.market",
    "cogs.leaderboard",
    "cogs.misc",
    "cogs.admin",
    "cogs.trading_p2p",
]

@bot.event
async def on_ready():
    from utils.database import init_db
    await init_db()
    await bot.tree.sync()  # Syncs all slash commands with Discord
    print(f"✅ Bot ready as {bot.user}")

@bot.event
async def setup_hook():
    for ext in initial_extensions:
        try:
            await bot.load_extension(ext)
            print(f"Loaded {ext}")
        except Exception as e:
            print(f"Failed to load {ext}: {e}")

@bot.event
async def on_guild_join(guild):
    # Look for an existing channel named toilet-exchange
    existing = discord.utils.get(guild.text_channels, name="toilet-exchange")
    if existing:
        channel = existing
    else:
        channel = await guild.create_text_channel("toilet-exchange")
    await channel.send("🚽 The Toilet Exchange is open for business! Type !info for command list.")

@bot.event
async def on_error(event, *_):
    import sys
    import traceback

    exc_type, error, tb = sys.exc_info()

    # Ensure the logged error is an Exception
    if not isinstance(error, Exception):
        error = Exception(str(error) if error else "Unknown error")

    # Capture full traceback for better debugging
    formatted_trace = "".join(traceback.format_exception(exc_type, error, tb))

    await log_error(f"on_error:{event}", error)
    print(f"[ERROR] Event: {event}\n{formatted_trace}")

bot.run(BOT_TOKEN)
