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
# GLOBAL CHECK: Restrict commands to #toilet-exchange
# =====================================================
@bot.check
async def only_in_exchange_channel(ctx):
    """Restrict commands to the #toilet-exchange channel, except DMs and !feedback."""
    if ctx.command and ctx.command.name == "feedback":
        return True

    if not ctx.guild:
        await ctx.send(
            f"⚠️ {ctx.author.display_name}, commands can’t be used in DMs.\n"
            "Please use them in your server’s **#toilet-exchange** channel."
        )
        await log_error(
            "CHANNEL_NOTICE",
            Exception(f"{ctx.author} tried a DM command (ignored)."),
            ctx,
        )
        raise WrongChannel()

    if getattr(ctx.channel, "name", None) != "toilet-exchange":
        await ctx.send(
            f"⚠️ {ctx.author.display_name}, please use commands only in **#toilet-exchange**."
        )
        await log_error(
            "CHANNEL_NOTICE",
            Exception(f"{ctx.author} used command in #{ctx.channel.name} (ignored)."),
            ctx,
        )
        raise WrongChannel()

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
async def setup_hook():
    """Load cogs on startup"""
    for ext in initial_extensions:
        try:
            await bot.load_extension(ext)
            print(f"✅ Loaded {ext}")
        except Exception as e:
            print(f"❌ Failed to load {ext}: {e}")


@bot.event
async def on_ready():
    """Initialize DB and confirm bot readiness"""
    from utils.database import init_db, ensure_guild_market
    await init_db()

    # Auto-ensure every connected guild has market + settings
    for guild in bot.guilds:
        await ensure_guild_market(guild.id)

    await bot.tree.sync()
    print(f"🚽 Bot ready as {bot.user} | Connected to {len(bot.guilds)} guild(s)")


@bot.event
async def on_guild_join(guild):
    """Auto-create #toilet-exchange and initialize guild data"""
    from utils.database import ensure_guild_market, get_server_settings

    existing = discord.utils.get(guild.text_channels, name="toilet-exchange")
    if existing:
        channel = existing
    else:
        channel = await guild.create_text_channel("toilet-exchange")

    await ensure_guild_market(guild.id)
    await get_server_settings(guild.id)

    await channel.send(
        "The Toilet Exchange is open for business!\n"
        "Use `!register` to create your account."
    )
    print(f"🪙 Initialized new guild: {guild.name} ({guild.id})")


@bot.event
async def on_error(event, *_):
    """Global event error handler with traceback"""
    import sys, traceback
    exc_type, error, tb = sys.exc_info()
    if not isinstance(error, Exception):
        error = Exception(str(error) if error else "Unknown error")
    formatted_trace = "".join(traceback.format_exception(exc_type, error, tb))
    await log_error(f"on_error:{event}", error)
    print(f"[ERROR] Event: {event}\n{formatted_trace}")


if __name__ == "__main__":
    if not BOT_TOKEN:
        raise RuntimeError("❌ DISCORD_BOT_TOKEN not found in .env file.")
    bot.run(BOT_TOKEN)
