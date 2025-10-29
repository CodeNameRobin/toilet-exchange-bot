# cogs/admin.py
import datetime
import random
import aiosqlite
import discord
from discord import NotFound, HTTPException, Forbidden
from discord.ext import commands
from utils.database import DB_PATH
from utils.helpers import dm_and_delete
from utils.database import update_server_setting, get_server_settings
from utils.helpers import resolve_member
import os
import glob
import aiofiles
import errno
import traceback
from datetime import UTC
from utils.database import DB_PATH, DEFAULT_STOCKS

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
                else:
                    pass

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
    """Allow full admins or users with bot management permissions."""
    perms = ctx.author.guild_permissions
    return perms.administrator or perms.manage_guild or perms.manage_webhooks

def bot_admin():
    """Decorator for commands restricted to bot admins."""
    return commands.check(is_bot_admin)


class Admin(commands.Cog):
    """Admin commands for managing the stock market"""

    def __init__(self, bot):
        self.bot = bot

    # -------------------------------
    # Global check for admin commands
    # -------------------------------
    async def cog_check(self, ctx):
        """Allow admins or users with bot/integration management permissions."""
        perms = ctx.author.guild_permissions
        if perms.administrator or perms.manage_guild or perms.manage_webhooks:
            return True
        await dm_and_delete(ctx, "ACCESS DENIED: ADMIN COMMAND ONLY.")
        return False

    async def cog_command_error(self, ctx, error):
        from utils.errors import WrongChannel  # import your custom exception

        # Ignore wrong-channel errors (no need to spam users or logs)
        if isinstance(error, WrongChannel):
            return

        # Log all other errors normally
        await log_event("ERROR", f"Unhandled exception in command '{ctx.command}': {error}", ctx, error)
        await ctx.send("⚠️ An internal error occurred. The issue has been logged.")

    # -------------------------------
    # Commands
    # -------------------------------
    @commands.command(name="add_stock")
    @bot_admin()
    async def add_stock(self, ctx, ticker: str = None, *, rest: str = None):
        """ADMIN ONLY: Add a new stock to the market.
        Usage:
          !add_stock TICKER "NAME" PRICE RISK
          Example: !add_stock GMD "GOMADINC" 150.25 high
        """
        if not ticker or not rest:
            await log_event("WARN", f"{ctx.author} used add_stock incorrectly (missing args)", ctx)
            return await dm_and_delete(
                ctx,
                "❌ Incorrect usage. Example: `!add_stock GMD \"GOMADINC\" 150.25 high`"
            )

        try:
            # Split the args so that risk is optional at the end
            parts = rest.split()
            if len(parts) < 2:
                raise ValueError("Not enough arguments")

            # Check if last token is a valid risk level
            possible_risk = parts[-1].lower()
            if possible_risk in ("low", "moderate", "high"):
                *name_parts, price_str, risk = parts
            else:
                *name_parts, price_str = parts
                risk = "moderate"  # default if none provided

            name = " ".join(name_parts)
            price = float(price_str)

        except Exception as e:
            await log_event("ERROR", f"Invalid input format for add_stock by {ctx.author}", ctx, e)
            return await dm_and_delete(
                ctx,
                "❌ Invalid input. Example: `!add_stock GMD GOMADINC! 150.25 high`"
            )

        try:
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute(
                    "INSERT OR IGNORE INTO stocks(ticker, name, price, risk) VALUES(?, ?, ?, ?)",
                    (ticker.upper(), name, price, risk)
                )
                await db.commit()

            await dm_and_delete(
                ctx,
                f"✅ Added stock `{ticker.upper()}` — **{name}** at ${price:.2f} (Risk: {risk.title()})"
            )
            await log_event(
                "INFO",
                f"Added stock {ticker.upper()} — {name} at ${price:.2f} (Risk: {risk.title()})",
                ctx
            )

        except Exception as e:
            await ctx.send("⚠️ Database error while adding stock. Logged for review.")
            await log_event("ERROR", f"Database failure adding stock {ticker.upper()}", ctx, e)

    @commands.command(name="set_price")
    @bot_admin()
    async def set_price(self, ctx, ticker: str, price: float):
        """ADMIN ONLY: Set the price of an existing stock"""
        try:
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute("UPDATE stocks SET price=? WHERE ticker=?", (price, ticker.upper()))
                await db.commit()

            await dm_and_delete(ctx, f"✅ Set `{ticker.upper()}` price to ${price:.2f}")
            await log_event("INFO", f"Set price for {ticker.upper()} to ${price:.2f}", ctx)
        except Exception as e:
            await ctx.send("⚠️ Error updating stock price. Logged for review.")
            await log_event("ERROR", f"Failed to set price for {ticker.upper()}", ctx, e)

    @commands.command(name="set_risk")
    @bot_admin()
    async def set_risk(self, ctx, ticker: str, risk: str):
        """ADMIN ONLY: Set the risk level of a stock (low, moderate, high)."""
        try:
            risk = risk.lower()
            if risk not in ("low", "moderate", "high"):
                await log_event("WARN", f"Invalid risk '{risk}' provided by {ctx.author.display_name}", ctx)
                return await dm_and_delete(ctx, "❌ Invalid risk. Choose: low, moderate, or high.")

            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute("UPDATE stocks SET risk=? WHERE ticker=?", (risk, ticker.upper()))
                await db.commit()

            await dm_and_delete(ctx, f"✅ Set risk of `{ticker.upper()}` to **{risk.title()}**.")
            await log_event("INFO", f"Set risk for {ticker.upper()} to {risk.title()}", ctx)

        except Exception as e:
            await ctx.send("⚠️ Error updating stock risk. Logged for review.")
            await log_event("ERROR", f"Failed to set risk for {ticker.upper()}", ctx, e)

    @commands.command(name="remove_stock")
    @bot_admin()
    async def remove_stock(self, ctx, ticker: str):
        """ADMIN ONLY: Remove a stock and auto-cash out all holders."""
        ticker = ticker.upper()
        try:
            async with aiosqlite.connect(DB_PATH) as db:
                # Get current stock price
                cur = await db.execute("SELECT price FROM stocks WHERE ticker=?", (ticker,))
                row = await cur.fetchone()
                if not row:
                    await log_event("WARN", f"Attempted to remove non-existent stock {ticker}", ctx)
                    return await dm_and_delete(ctx, f"❌ Stock `{ticker}` not found.")
                price = row[0]

                # Find all users holding this stock (total BUY-SELL)
                cur = await db.execute("""
                    SELECT user_id, guild_id, SUM(CASE WHEN side='BUY' THEN qty ELSE -qty END)
                    FROM trades
                    WHERE ticker=? AND guild_id=?
                    GROUP BY user_id
                """, (ticker, str(ctx.guild.id)))
                holders = await cur.fetchall()

                if holders:
                    for user_id, guild_id, qty in holders:
                        if qty <= 0:
                            continue
                        total_value = price * qty

                        # Add cash to user balance
                        await db.execute("""
                            UPDATE users
                            SET cash = cash + ?
                            WHERE discord_id=? AND guild_id=?
                        """, (total_value, str(user_id), str(guild_id)))

                        # Record a SELL trade
                        await db.execute("""
                            INSERT INTO trades(user_id, guild_id, ticker, qty, side, price)
                            VALUES(?, ?, ?, ?, 'SELL', ?)
                        """, (str(user_id), str(guild_id), ticker, qty, price))

                        # Notify user if possible
                        user = ctx.guild.get_member(int(user_id))
                        if user:
                            try:
                                await user.send(
                                    f"`{ticker}` has been delisted from the market.\n"
                                    f"Your {qty} shares were automatically sold for ${total_value:,.2f} "
                                    f"at ${price:.2f} each."
                                )
                            except discord.Forbidden:
                                pass

                    await db.commit()
                    await log_event("INFO", f"Stock {ticker} removed; {len(holders)} users cashed out.", ctx)
                else:
                    await log_event("INFO", f"Stock {ticker} removed; no active holders found.", ctx)

                # Finally, remove stock from table
                await db.execute("DELETE FROM stocks WHERE ticker=?", (ticker,))
                await db.commit()

            await dm_and_delete(ctx, f"✅ `{ticker}` removed from the market. All holdings were sold automatically.")
        except Exception as e:
            await ctx.send("⚠️ Error while removing stock. Logged for review.")
            await log_event("ERROR", f"Failed to remove stock {ticker}", ctx, e)

    @commands.command(name="users")
    @bot_admin()
    async def list_users(self, ctx):
        """ADMIN ONLY: List all users with their cash, stocks, and total portfolio values."""
        try:
            async with aiosqlite.connect(DB_PATH) as db:
                # Fetch all users in this guild
                cur = await db.execute("SELECT discord_id, cash FROM users WHERE guild_id=?", (str(ctx.guild.id),))
                users = await cur.fetchall()

                if not users:
                    await log_event("INFO", f"No registered users found in {ctx.guild.name}", ctx)
                    return await dm_and_delete(ctx, "No registered users found.")

                embed = discord.Embed(
                    title="Registered Users Overview",
                    description=f"Server: **{ctx.guild.name}**",
                    color=discord.Color.purple()
                )

                total_users = 0
                for discord_id, cash in users:
                    total_users += 1

                    # Get each user's stock holdings
                    cur = await db.execute("""
                        SELECT t.ticker, SUM(CASE WHEN t.side='BUY' THEN t.qty ELSE -t.qty END) AS qty,
                               s.price, s.name
                        FROM trades t
                        JOIN stocks s ON t.ticker = s.ticker
                        WHERE t.user_id=? AND t.guild_id=?
                        GROUP BY t.ticker
                    """, (str(discord_id), str(ctx.guild.id)))
                    holdings = await cur.fetchall()

                    total_value = cash
                    stock_lines = []
                    for ticker, qty, price, name in holdings:
                        if qty <= 0:
                            continue
                        value = qty * price
                        total_value += value
                        stock_lines.append(f"> {name} ({ticker}) — {qty} × ${price:.2f} = **${value:,.2f}**")

                    # Build display text
                    try:
                        user_obj = await self.bot.fetch_user(int(discord_id))
                        display_name = user_obj.name
                    except (NotFound, HTTPException, Forbidden):
                        member = ctx.guild.get_member(int(discord_id))
                        display_name = member.display_name if member else f"User {discord_id}"

                    value_text = "\n".join(stock_lines) if stock_lines else "_No stocks owned_"

                    embed.add_field(
                        name=f"{display_name} — ${cash:,.2f}",
                        value=f"{value_text}\n**Total Portfolio:** ${total_value:,.2f}",
                        inline=False
                    )

            await dm_and_delete(ctx, embed=embed)
            await log_event("INFO", f"Listed {total_users} registered users for {ctx.guild.name}", ctx)

        except Exception as e:
            await ctx.send("⚠️ Error fetching user data. Logged for review.")
            await log_event("ERROR", "Failed to list users", ctx, e)

    @commands.command(name="delete_user")
    @bot_admin()
    async def delete_user(self, ctx, target: str = None):
        """ADMIN ONLY: Permanently delete a user's account and all related data.
        Usage:
          !delete_user @User
          !delete_user username
        """
        if not target:
            await log_event("WARN", "delete_user called with no target", ctx)
            return await ctx.author.send("❌ Usage: `!delete_user @User` or `!delete_user username`")

        try:
            member = await resolve_member(self.bot, ctx, target)
            if not member:
                await log_event("WARN", f"User '{target}' not found in {ctx.guild.name}", ctx)
                return await ctx.author.send(f"❌ Could not find user `{target}` in this server.")

            user_id = str(member.id)
            guild_id = str(ctx.guild.id)

            async with aiosqlite.connect(DB_PATH) as db:
                # Delete all related records
                await db.execute("DELETE FROM trades WHERE user_id=? AND guild_id=?", (user_id, guild_id))
                await db.execute(
                    "DELETE FROM portfolios WHERE user_id=(SELECT id FROM users WHERE discord_id=? AND guild_id=?)",
                    (user_id, guild_id)
                )
                await db.execute("DELETE FROM users WHERE discord_id=? AND guild_id=?", (user_id, guild_id))
                await db.commit()

            # Notify target user (if possible)
            try:
                await member.send("⚠️ Your Toilet Exchange account has been deleted by an admin.")
                await log_event("INFO", f"User {member.display_name} notified of deletion", ctx)
            except discord.Forbidden:
                await log_event("WARN", f"Could not DM user {member.display_name} about deletion", ctx)

            # Confirm to admin privately and delete their command message
            await ctx.author.send(f"✅ Deleted account and data for {member.display_name}.")
            await ctx.message.delete()

            await log_event("INFO", f"Deleted account and all data for {member.display_name}", ctx)

        except Exception as e:
            await ctx.send("⚠️ Error deleting user. Logged for review.")
            await log_event("ERROR", f"Failed to delete user '{target}'", ctx, e)

    @commands.command(name="reset_stocks")
    @bot_admin()
    async def reset_stocks(self, ctx):
        """ADMIN ONLY: Reset all stocks and auto-cash out existing holdings."""
        try:
            total_users_cashed = 0
            total_stocks = 0
            _ = total_stocks

            async with aiosqlite.connect(DB_PATH) as db:
                # Get all current stocks
                cur = await db.execute("SELECT ticker, price FROM stocks")
                stocks = await cur.fetchall()
                total_stocks = len(stocks)

                if not stocks:
                    await log_event("WARN", "No stocks found during reset", ctx)
                    return await dm_and_delete(ctx, "❌ No stocks available to reset.")

                # Loop and cash out each stock before deleting
                for ticker, price in stocks:
                    cur = await db.execute("""
                        SELECT user_id, guild_id, SUM(CASE WHEN side='BUY' THEN qty ELSE -qty END)
                        FROM trades
                        WHERE ticker=? AND guild_id=?
                        GROUP BY user_id
                    """, (ticker, str(ctx.guild.id)))
                    holders = await cur.fetchall()

                    for user_id, guild_id, qty in holders:
                        if qty <= 0:
                            continue
                        total_value = price * qty
                        total_users_cashed += 1

                        # Add cash to user balance
                        await db.execute(
                            "UPDATE users SET cash=cash+? WHERE discord_id=? AND guild_id=?",
                            (total_value, str(user_id), str(guild_id))
                        )
                        # Record a SELL trade
                        await db.execute("""
                            INSERT INTO trades(user_id, guild_id, ticker, qty, side, price)
                            VALUES(?, ?, ?, ?, 'SELL', ?)
                        """, (str(user_id), str(guild_id), ticker, qty, price))

                        # DM users their auto-sale
                        user = ctx.guild.get_member(int(user_id))
                        if user:
                            try:
                                await user.send(
                                    f"📉 The market has been reset.\nYour {qty} shares of `{ticker}` "
                                    f"were sold automatically for ${total_value:,.2f}."
                                )
                            except discord.Forbidden:
                                await log_event("WARN", f"Failed to DM user {user.display_name} about reset", ctx)

                # Clear stocks and insert defaults from database constants
                await db.execute("DELETE FROM stocks")
                await db.executemany("INSERT INTO stocks(ticker, name, price) VALUES(?, ?, ?)", DEFAULT_STOCKS)
                await db.commit()

            # ✅ Confirmation and log moved here — both use total_stocks safely
            await dm_and_delete(ctx,
                                "✅ All stocks have been reset. Existing holdings were cashed out at current market value.")

            if total_stocks > 0:
                await log_event(
                    "INFO",
                    f"Stock market reset completed — {total_stocks} stocks reset, {total_users_cashed} users cashed out.",
                    ctx
                )

        except Exception as e:
            await ctx.send("⚠️ Market reset failed. Logged for review.")
            await log_event("ERROR", "Stock market reset failed", ctx, e)

    @commands.command(name="gift")
    @bot_admin()
    async def gift(self, ctx, user_text: str = None, arg1: str = None, arg2: str = None):
        """ADMIN ONLY: Gift a user money or stocks.
        Usage:
          !gift username 100
          !gift username TICKER QTY
        """
        from utils.database import update_balance, record_trade, get_stock_price

        if not user_text or not arg1:
            await log_event("WARN", "Gift command used without enough arguments", ctx)
            return await dm_and_delete(ctx, "❌ Usage: `!gift username 100` or `!gift username TICKER QTY`")

        try:
            member = await resolve_member(self.bot, ctx, user_text)
            if not member:
                await log_event("WARN", f"Gift target '{user_text}' not found in {ctx.guild.name}", ctx)
                return await dm_and_delete(ctx, f"❌ Could not find user `{user_text}` in this server.")

            # ------------------------------
            # CASH GIFT
            # ------------------------------
            if arg1.isdigit() and not arg2:
                amount = int(arg1)
                if amount <= 0:
                    await log_event("WARN", f"Attempted to gift non-positive amount: {amount}", ctx)
                    return await dm_and_delete(ctx, "❌ Amount must be greater than 0.")

                await update_balance(member.id, ctx.guild.id, amount)
                await dm_and_delete(ctx, f"✅ Gifted ${amount:,} to {member.display_name}.")
                await log_event("INFO", f"Gave ${amount:,} to {member.display_name}", ctx)

                # DM notification
                try:
                    embed = discord.Embed(
                        title="You Received a Gift!",
                        description=f"You’ve been gifted **${amount:,}** by an admin in **{ctx.guild.name}**!",
                        color=discord.Color.gold()
                    )
                    embed.set_footer(text="Spend it wisely on the Toilet Exchange 💸")
                    await member.send(embed=embed)
                    await log_event("INFO", f"DM sent to {member.display_name} for cash gift", ctx)
                except discord.Forbidden:
                    await log_event("WARN", f"Failed to DM {member.display_name} for cash gift", ctx)

                return

            # ------------------------------
            # STOCK GIFT
            # ------------------------------
            if arg2 and arg2.isdigit():
                ticker = arg1.upper()
                qty = int(arg2)
                if qty <= 0:
                    await log_event("WARN", f"Attempted to gift invalid quantity: {qty}", ctx)
                    return await dm_and_delete(ctx, "❌ Quantity must be greater than 0.")

                price = await get_stock_price(ticker)
                if price is None:
                    await log_event("WARN", f"Attempted to gift invalid ticker: {ticker}", ctx)
                    return await dm_and_delete(ctx, f"❌ Invalid ticker `{ticker}`.")

                await record_trade(member.id, ctx.guild.id, ticker, qty, "BUY")
                await dm_and_delete(ctx, f"✅ Gifted {qty} × {ticker} to {member.display_name}.")
                await log_event("INFO", f"Gave {qty}×{ticker} to {member.display_name}", ctx)

                # DM notification
                try:
                    embed = discord.Embed(
                        title="You Received a Gift!",
                        description=f"You’ve been gifted **{qty} × {ticker}** by an admin in **{ctx.guild.name}**!",
                        color=discord.Color.green()
                    )
                    embed.set_footer(text="Check your portfolio on the Toilet Exchange 📊")
                    await member.send(embed=embed)
                    await log_event("INFO", f"DM sent to {member.display_name} for stock gift", ctx)
                except discord.Forbidden:
                    await log_event("WARN", f"Failed to DM {member.display_name} for stock gift", ctx)

                return

            # ------------------------------
            # INVALID USAGE
            # ------------------------------
            await log_event("WARN", f"Gift command invalid usage by {ctx.author}", ctx)
            await dm_and_delete(ctx, "❌ Invalid input. Example: `!gift username GMD 2` or `!gift username 100`")

        except Exception as e:
            await ctx.send("⚠️ Gift command failed. Logged for review.")
            await log_event("ERROR", f"Gift command failed for target '{user_text}'", ctx, e)

    @commands.command(name="market_crash")
    @bot_admin()
    async def market_crash(self, ctx):
        """ADMIN ONLY: Trigger a once-per-year market crash event."""
        try:
            current_year = datetime.datetime.now().year
            crashed = []

            async with aiosqlite.connect(DB_PATH) as db:
                # Check if crash already happened this year
                cur = await db.execute(
                    "SELECT COUNT(*) FROM market_events WHERE event_type='crash' AND year=?",
                    (current_year,)
                )
                count = (await cur.fetchone())[0]

                if count > 0:
                    await log_event("WARN", f"Market crash already triggered in {current_year}", ctx)
                    return await dm_and_delete(ctx, f"Market crash already triggered in {current_year}!")

                # Apply crash: reduce prices between 40–70%
                cur = await db.execute("SELECT ticker, price FROM stocks")
                stocks = await cur.fetchall()

                if not stocks:
                    await log_event("WARN", "No stocks found to crash", ctx)
                    return await dm_and_delete(ctx, "❌ No stocks found to apply a crash to!")

                total_loss_pct = 0
                for ticker, price in stocks:
                    new_price = round(price * random.uniform(0.3, 0.6), 2)
                    loss_pct = ((new_price - price) / price) * 100
                    total_loss_pct += abs(loss_pct)
                    crashed.append((ticker, price, new_price))
                    await db.execute("UPDATE stocks SET price=? WHERE ticker=?", (new_price, ticker))

                # Record the crash event
                await db.execute(
                    "INSERT INTO market_events (event_type, year) VALUES ('crash', ?)",
                    (current_year,)
                )
                await db.commit()

            # ------------------------------
            # BUILD ANNOUNCEMENT EMBED
            # ------------------------------
            embed = discord.Embed(
                title="THE MARKET HAS CRASHED!",
                description="Panic spreads through the Toilet Exchange as values plummet!",
                color=discord.Color.red()
            )
            embed.set_footer(text=f"Triggered by {ctx.author.display_name} — {current_year}")

            for ticker, old_price, new_price in crashed:
                change_pct = ((new_price - old_price) / old_price) * 100
                embed.add_field(
                    name=f"{ticker}",
                    value=f"${old_price:.2f} → ${new_price:.2f} ({change_pct:.1f}%)",
                    inline=False
                )

            # ------------------------------
            # SEND TO CHANNEL
            # ------------------------------
            target_channel: discord.TextChannel | None

            if ctx.channel.name != "toilet-exchange":
                target_channel = discord.utils.get(ctx.guild.text_channels, name="toilet-exchange")
            else:
                target_channel = ctx.channel

            if target_channel:
                await target_channel.send(embed=embed)
                await log_event(
                    "INFO",
                    f"Market crash triggered by {ctx.author.display_name}. "
                    f"{len(crashed)} stocks affected with average {total_loss_pct / len(crashed):.1f}% loss.",
                    ctx
                )
            else:
                await log_event("WARN", "Could not find #toilet-exchange channel for crash broadcast", ctx)

            await dm_and_delete(ctx, f"✅ Market crash triggered for {current_year}. Prices have tanked!")

        except Exception as e:
            await ctx.send("⚠️ Market crash failed. Logged for review.")
            await log_event("ERROR", "Market crash execution failed", ctx, e)

    @commands.command(name="list_settings")
    @bot_admin()
    async def list_settings(self, ctx):
        """ADMIN ONLY: List all editable server settings."""
        from utils.database import get_server_settings

        try:
            settings = await get_server_settings(ctx.guild.id)
            if not settings:
                await log_event("WARN", f"No settings found for guild {ctx.guild.name}", ctx)
                return await dm_and_delete(ctx, "⚠️ No settings found for this server.")

            embed = discord.Embed(
                title=f"Server Settings — {ctx.guild.name}",
                color=discord.Color.blurple()
            )

            for key, value in settings.items():
                if key == "guild_id":
                    continue
                embed.add_field(
                    name=key.replace("_", " ").title(),
                    value=str(value),
                    inline=False
                )

            await dm_and_delete(ctx, embed=embed)
            await log_event("INFO", f"{ctx.author.display_name} viewed server settings", ctx)

        except discord.Forbidden:
            await log_event("WARN", f"Failed to DM {ctx.author.display_name} server settings (DMs closed)", ctx)
            await ctx.send("⚠️ Could not DM settings — please enable DMs temporarily.")
        except Exception as e:
            await ctx.send("⚠️ Unable to display settings. Logged for review.")
            await log_event("ERROR", "Failed to list server settings", ctx, e)

    @commands.command(name="set_setting")
    @bot_admin()
    async def set_setting(self, ctx, setting: str = None, *, value: str = None):
        """ADMIN ONLY: Change a server setting.
        Usage: !set_setting leaderboard_update_rate 5
        """
        try:
            if not setting or value is None:
                await log_event("WARN", f"Missing arguments for set_setting by {ctx.author.display_name}", ctx)
                return await dm_and_delete(ctx, "❌ Usage: `!set_setting <setting> <value>`")

            settings = await get_server_settings(ctx.guild.id)
            if not settings:
                await log_event("WARN", f"No settings found for guild {ctx.guild.name}", ctx)
                return await dm_and_delete(ctx, "⚠️ No settings found for this server.")

            if setting not in settings:
                valid = ", ".join([k for k in settings.keys() if k != "guild_id"])
                await log_event("WARN", f"Invalid setting '{setting}' attempted by {ctx.author.display_name}", ctx)
                return await dm_and_delete(ctx, f"❌ Invalid setting. Available: {valid}")

            # Try to typecast numeric values automatically
            try:
                if value.lower() == "none":
                    cast_value = None
                elif "." in value:
                    cast_value = float(value)
                elif value.isdigit():
                    cast_value = int(value)
                else:
                    cast_value = value
            except (ValueError, AttributeError):
                cast_value = value

            await update_server_setting(ctx.guild.id, setting, cast_value)
            await dm_and_delete(ctx, f"✅ Updated `{setting}` to `{cast_value}` for {ctx.guild.name}.")
            await log_event("INFO", f"{ctx.author.display_name} updated '{setting}' to '{cast_value}'", ctx)

        except discord.Forbidden:
            await log_event("WARN", f"Failed to DM {ctx.author.display_name} after updating setting", ctx)
            await ctx.send("⚠️ Could not DM you confirmation — please enable DMs temporarily.")
        except Exception as e:
            await ctx.send("⚠️ Failed to update setting. Logged for review.")
            await log_event("ERROR", f"Error updating setting '{setting}'", ctx, e)


async def setup(bot):
    await bot.add_cog(Admin(bot))
    await log_event("INFO", "Admin cog loaded and logging initialized.")