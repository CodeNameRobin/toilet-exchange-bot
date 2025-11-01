# cogs/admin.py
import datetime
import random
import aiosqlite
import discord
from discord import NotFound, HTTPException, Forbidden
from discord.ext import commands
from utils.database import (
    DB_PATH, DEFAULT_STOCKS,
    update_server_setting, get_server_settings,
    add_admin, remove_admin, list_admins, is_admin  # new admin helpers
)
from utils.helpers import dm_and_delete, resolve_member
import os, glob, aiofiles, errno, traceback
from datetime import UTC

# ================================
# WEEKLY LOGGING CONFIGURATION
# ================================
LOG_DIR = "logs"
LOG_PREFIX = "admin_log_"
LOG_EXT = ".txt"


def _week_key(dt: datetime.datetime):
    iso = dt.isocalendar()
    return iso.year, iso.week


def _current_log_path():
    now = datetime.datetime.now(UTC)
    year, week = _week_key(now)
    filename = f"{LOG_PREFIX}{year}-W{week:02d}{LOG_EXT}"
    os.makedirs(LOG_DIR, exist_ok=True)
    return os.path.join(LOG_DIR, filename)


def _list_log_files():
    return sorted(glob.glob(os.path.join(LOG_DIR, f"{LOG_PREFIX}*{LOG_EXT}")))


def _prune_old_logs(keep: int = 2):
    files = _list_log_files()
    if len(files) > keep:
        for old in files[:-keep]:
            try:
                os.remove(old)
            except OSError as e:
                if e.errno not in (errno.ENOENT, errno.EPERM):
                    raise


async def log_event(event_type: str, message: str, ctx=None, error: Exception | None = None):
    """Log events, rotating weekly and keeping only the last 2 files."""
    timestamp = datetime.datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")
    user_info = f" by {ctx.author} ({getattr(ctx.author, 'id', 'n/a')})" if ctx else ""
    guild_info = f" in {getattr(ctx.guild, 'name', 'DM')}" if ctx and ctx.guild else ""
    header = f"[{timestamp}] [{event_type.upper()}]{user_info}{guild_info}: {message}\n"

    path = _current_log_path()
    async with aiofiles.open(path, "a", encoding="utf-8") as f:
        await f.write(header)
        if error:
            await f.write(f"  ↳ ERROR: {error}\n")
            await f.write("".join(traceback.format_exception(type(error), error, error.__traceback__)))
            await f.write("\n")

    _prune_old_logs(keep=2)


async def is_bot_admin(ctx):
    """Allow real Discord admins or users listed in the admin table."""
    perms = ctx.author.guild_permissions
    if perms.administrator or perms.manage_guild or perms.manage_webhooks:
        return True
    return await is_admin(ctx.author.id, ctx.guild.id)


def bot_admin():
    return commands.check(is_bot_admin)


class Admin(commands.Cog):
    """Admin commands for managing the Toilet Exchange (per server)."""

    def __init__(self, bot):
        self.bot = bot

    async def cog_check(self, ctx):
        """Default permission check for all commands."""
        if await is_bot_admin(ctx):
            return True
        await dm_and_delete(ctx, "ACCESS DENIED: ADMIN COMMAND ONLY.")
        return False

    async def cog_command_error(self, ctx, error):
        from utils.errors import WrongChannel
        if isinstance(error, WrongChannel):
            return
        await log_event("ERROR", f"Unhandled exception in '{ctx.command}': {error}", ctx, error)
        await ctx.send("⚠️ An internal error occurred. The issue has been logged.")

    # --------------------------------
    # ADMIN MANAGEMENT COMMANDS
    # --------------------------------
    @commands.command(name="add_admin")
    @bot_admin()
    async def add_admin_cmd(self, ctx, member: discord.Member):
        """Add a user as a Toilet Exchange admin for this server."""
        await add_admin(member.id, ctx.guild.id, added_by=ctx.author.id)
        await dm_and_delete(ctx, f"✅ {member.display_name} added as a Toilet Exchange admin.")
        await log_event("INFO", f"Added {member} as admin", ctx)

    @commands.command(name="remove_admin")
    @bot_admin()
    async def remove_admin_cmd(self, ctx, member: discord.Member):
        """Remove a user’s Toilet Exchange admin rights."""
        await remove_admin(member.id, ctx.guild.id)
        await dm_and_delete(ctx, f"🗑️ {member.display_name} removed from admin list.")
        await log_event("INFO", f"Removed {member} from admin list", ctx)

    @commands.command(name="list_admins")
    @bot_admin()
    async def list_admins_cmd(self, ctx):
        """List all Toilet Exchange admins for this server."""
        admins = await list_admins(ctx.guild.id)
        if not admins:
            return await dm_and_delete(ctx, "No custom admins found for this server.")
        embed = discord.Embed(
            title=f"🧰 Admins for {ctx.guild.name}",
            color=discord.Color.blurple(),
        )
        for discord_id, added_at in admins:
            member = ctx.guild.get_member(int(discord_id))
            name = member.display_name if member else f"<@{discord_id}>"
            embed.add_field(name=name, value=f"Added {added_at}", inline=False)
        await dm_and_delete(ctx, embed=embed)

    # --------------------------------
    # STOCK MANAGEMENT
    # --------------------------------
    @commands.command(name="add_stock")
    @bot_admin()
    async def add_stock(self, ctx, ticker: str = None, *, rest: str = None):
        if not ticker or not rest:
            return await dm_and_delete(ctx, "❌ Usage: `!add_stock GMD \"GOMADINC\" 150.25 high`")
        try:
            parts = rest.split()
            possible_risk = parts[-1].lower() if parts else "moderate"
            if possible_risk in ("low", "moderate", "high"):
                *name_parts, price_str, risk = parts
            else:
                *name_parts, price_str = parts
                risk = "moderate"
            name = " ".join(name_parts)
            price = float(price_str)
        except Exception as e:
            await log_event("ERROR", "Invalid args for add_stock", ctx, e)
            return await dm_and_delete(ctx, "❌ Invalid format. Example: `!add_stock GMD GOMADINC 150 high`")

        try:
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute(
                    "INSERT OR IGNORE INTO stocks(ticker, name, price, risk, guild_id) VALUES(?, ?, ?, ?, ?)",
                    (ticker.upper(), name, price, risk, str(ctx.guild.id)),
                )
                await db.commit()
            await dm_and_delete(ctx, f"✅ Added `{ticker.upper()}` — **{name}** (${price:.2f}, {risk})")
            await log_event("INFO", f"Added stock {ticker.upper()} in {ctx.guild.name}", ctx)
        except Exception as e:
            await ctx.send("⚠️ DB error while adding stock.")
            await log_event("ERROR", "Add stock DB fail", ctx, e)

    @commands.command(name="set_price")
    @bot_admin()
    async def set_price(self, ctx, ticker: str, price: float):
        try:
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute(
                    "UPDATE stocks SET price=? WHERE ticker=? AND guild_id=?",
                    (price, ticker.upper(), str(ctx.guild.id)),
                )
                await db.commit()
            await dm_and_delete(ctx, f"✅ `{ticker.upper()}` set to ${price:.2f}")
            await log_event("INFO", f"Price updated {ticker.upper()}", ctx)
        except Exception as e:
            await ctx.send("⚠️ Error updating stock price.")
            await log_event("ERROR", "Set price failed", ctx, e)

    @commands.command(name="set_risk")
    @bot_admin()
    async def set_risk(self, ctx, ticker: str, risk: str):
        risk = risk.lower()
        if risk not in ("low", "moderate", "high"):
            return await dm_and_delete(ctx, "❌ Invalid risk level.")
        try:
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute(
                    "UPDATE stocks SET risk=? WHERE ticker=? AND guild_id=?",
                    (risk, ticker.upper(), str(ctx.guild.id)),
                )
                await db.commit()
            await dm_and_delete(ctx, f"✅ `{ticker.upper()}` risk set to {risk}.")
        except Exception as e:
            await log_event("ERROR", "Set risk failed", ctx, e)

    # --------------------------------
    # REMOVE STOCK
    # --------------------------------
    @commands.command(name="remove_stock")
    @bot_admin()
    async def remove_stock(self, ctx, ticker: str):
        ticker = ticker.upper()
        try:
            async with aiosqlite.connect(DB_PATH) as db:
                cur = await db.execute(
                    "SELECT price FROM stocks WHERE ticker=? AND guild_id=?",
                    (ticker, str(ctx.guild.id)),
                )
                row = await cur.fetchone()
                if not row:
                    return await dm_and_delete(ctx, f"❌ Stock `{ticker}` not found.")
                price = row[0]

                cur = await db.execute("""
                    SELECT user_id, SUM(CASE WHEN side='BUY' THEN qty ELSE -qty END)
                    FROM trades
                    WHERE ticker=? AND guild_id=?
                    GROUP BY user_id
                """, (ticker, str(ctx.guild.id)))
                holders = await cur.fetchall()

                for user_id, qty in holders:
                    if qty <= 0:
                        continue
                    total_value = price * qty
                    await db.execute(
                        "UPDATE users SET cash=cash+? WHERE discord_id=? AND guild_id=?",
                        (total_value, str(user_id), str(ctx.guild.id)),
                    )
                    await db.execute("""
                        INSERT INTO trades(user_id, guild_id, ticker, qty, side, price)
                        VALUES(?, ?, ?, ?, 'SELL', ?)
                    """, (str(user_id), str(ctx.guild.id), ticker, qty, price))
                await db.execute("DELETE FROM stocks WHERE ticker=? AND guild_id=?", (ticker, str(ctx.guild.id)))
                await db.commit()

            await dm_and_delete(ctx, f"✅ `{ticker}` removed and holders cashed out.")
        except Exception as e:
            await ctx.send("⚠️ Error removing stock.")
            await log_event("ERROR", "Remove stock failed", ctx, e)

    # --------------------------------
    # RESET STOCKS (PER GUILD)
    # --------------------------------
    @commands.command(name="reset_stocks")
    @bot_admin()
    async def reset_stocks(self, ctx):
        try:
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute("DELETE FROM stocks WHERE guild_id=?", (str(ctx.guild.id),))
                await db.executemany(
                    "INSERT INTO stocks(ticker, name, price, risk, guild_id) VALUES (?, ?, ?, ?, ?)",
                    [(t, n, p, r, str(ctx.guild.id)) for (t, n, p, r) in DEFAULT_STOCKS],
                )
                await db.commit()
            await dm_and_delete(ctx, "✅ Stock market reset for this server.")
            await log_event("INFO", f"Reset market for {ctx.guild.name}", ctx)
        except Exception as e:
            await ctx.send("⚠️ Reset failed.")
            await log_event("ERROR", "Reset stocks failed", ctx, e)

    # --------------------------------
    # MARKET CRASH (PER GUILD)
    # --------------------------------
    @commands.command(name="market_crash")
    @bot_admin()
    async def market_crash(self, ctx):
        year = datetime.datetime.now().year
        try:
            async with aiosqlite.connect(DB_PATH) as db:
                cur = await db.execute(
                    "SELECT COUNT(*) FROM market_events WHERE event_type='crash' AND year=? AND guild_id=?",
                    (year, str(ctx.guild.id)),
                )
                if (await cur.fetchone())[0] > 0:
                    return await dm_and_delete(ctx, "Crash already triggered this year.")

                cur = await db.execute("SELECT ticker, price FROM stocks WHERE guild_id=?", (str(ctx.guild.id),))
                stocks = await cur.fetchall()
                if not stocks:
                    return await dm_and_delete(ctx, "❌ No stocks to crash.")

                crashed = []
                for ticker, price in stocks:
                    new_price = round(price * random.uniform(0.3, 0.6), 2)
                    await db.execute(
                        "UPDATE stocks SET price=? WHERE ticker=? AND guild_id=?",
                        (new_price, ticker, str(ctx.guild.id)),
                    )
                    crashed.append((ticker, price, new_price))

                await db.execute(
                    "INSERT INTO market_events(event_type, year, guild_id) VALUES('crash', ?, ?)",
                    (year, str(ctx.guild.id)),
                )
                await db.commit()

            embed = discord.Embed(
                title="💥 MARKET CRASH!",
                description=f"The Toilet Exchange in **{ctx.guild.name}** has collapsed!",
                color=discord.Color.red(),
            )
            for ticker, old, new in crashed:
                pct = ((new - old) / old) * 100
                embed.add_field(name=ticker, value=f"${old:.2f} → ${new:.2f} ({pct:.1f}%)", inline=False)
            await ctx.send(embed=embed)
            await log_event("INFO", f"{ctx.guild.name} crash: {len(crashed)} stocks affected", ctx)
        except Exception as e:
            await ctx.send("⚠️ Market crash failed.")
            await log_event("ERROR", "Crash failed", ctx, e)

    # --------------------------------
    # SERVER SETTINGS
    # --------------------------------
    @commands.command(name="list_settings")
    @bot_admin()
    async def list_settings(self, ctx):
        print("DEBUG: list_settings called")  # ✅ Add this line
        try:
            settings = await get_server_settings(ctx.guild.id)
            embed = discord.Embed(title=f"Server Settings — {ctx.guild.name}", color=discord.Color.blurple())
            for k, v in settings.items():
                if k != "guild_id":
                    embed.add_field(name=k.replace("_", " ").title(), value=str(v), inline=False)
            await ctx.send(embed=embed)
            print("DEBUG: sent embed")
        except Exception as e:
            print("DEBUG: list_settings error", e)
    @commands.command(name="set_setting")
    @bot_admin()
    async def set_setting(self, ctx, setting: str = None, *, value: str = None):
        if not setting or value is None:
            return await dm_and_delete(ctx, "❌ Usage: `!set_setting <setting> <value>`")
        try:
            settings = await get_server_settings(ctx.guild.id)
            if setting not in settings:
                valid = ", ".join([k for k in settings.keys() if k != "guild_id"])
                return await dm_and_delete(ctx, f"❌ Invalid setting. Options: {valid}")
            try:
                if setting == "secret_profiles":
                    if value.lower() in ("on", "true", "1"):
                        cast_value = 1
                    elif value.lower() in ("off", "false", "0"):
                        cast_value = 0
                    else:
                        return await dm_and_delete(ctx, "❌ Use `on` or `off` for secret_profiles.")
                elif "." in value:
                    cast_value = float(value)
                elif value.isdigit():
                    cast_value = int(value)
                else:
                    cast_value = value
            except ValueError:
                cast_value = value
            await update_server_setting(ctx.guild.id, setting, cast_value)
            await dm_and_delete(ctx, f"✅ `{setting}` updated to `{cast_value}`.")
        except Exception as e:
            await ctx.send("⚠️ Failed to update setting.")
            await log_event("ERROR", "Set setting failed", ctx, e)

        # --------------------------------
        # RESET ENTIRE GAME (PER GUILD)
        # --------------------------------
        @commands.command(name="reset_game")
        @bot_admin()
        async def reset_game(self, ctx):
            """Completely resets the Toilet Exchange for this server."""
            confirm_msg = await ctx.send(
                "⚠️ **WARNING:** This will ERASE all player data, portfolios, trades, and stocks "
                "for this server, resetting everything to default.\n\n"
                "Type `confirm` within 30 seconds to continue."
            )

            def check(m):
                return m.author == ctx.author and m.channel == ctx.channel and m.content.lower() == "confirm"

            try:
                msg = await self.bot.wait_for("message", check=check, timeout=30)
            except Exception:
                return await ctx.send("⏱️ Reset cancelled — no confirmation received.")

            try:
                async with aiosqlite.connect(DB_PATH) as db:
                    gid = str(ctx.guild.id)

                    # Wipe per-guild tables
                    await db.execute("DELETE FROM users WHERE guild_id=?", (gid,))
                    await db.execute("DELETE FROM portfolios WHERE guild_id=?", (gid,))
                    await db.execute("DELETE FROM trades WHERE guild_id=?", (gid,))
                    await db.execute("DELETE FROM stocks WHERE guild_id=?", (gid,))
                    await db.execute("DELETE FROM price_history WHERE guild_id=?", (gid,))
                    await db.execute("DELETE FROM leaderboard_cache WHERE guild_id=?", (gid,))
                    await db.execute("DELETE FROM market_events WHERE guild_id=?", (gid,))

                    # Reinsert default stocks
                    await db.executemany(
                        "INSERT INTO stocks(ticker, name, price, risk, guild_id) VALUES (?, ?, ?, ?, ?)",
                        [(t, n, p, r, gid) for (t, n, p, r) in DEFAULT_STOCKS],
                    )

                    # Reset server settings to defaults but preserve guild entry
                    await db.execute(
                        """
                        UPDATE server_settings
                        SET leaderboard_post_time='23:00',
                            leaderboard_update_rate=10,
                            market_update_rate=1,
                            starting_money=1000.0,
                            market_bias=0.0008,
                            target_price=100.0
                        WHERE guild_id=?;
                        """,
                        (gid,),
                    )

                    await db.commit()

                await dm_and_delete(ctx, "🧻 Game reset complete! All data restored to defaults.")
                await log_event("INFO", f"Full reset performed for {ctx.guild.name}", ctx)
            except Exception as e:
                await ctx.send("⚠️ Game reset failed.")
                await log_event("ERROR", "Reset game failed", ctx, e)


async def setup(bot):
    await bot.add_cog(Admin(bot))
    await log_event("INFO", "Admin cog loaded")