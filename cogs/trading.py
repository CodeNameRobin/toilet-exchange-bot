# cogs/trading.py
import aiosqlite
import discord
from discord.ext import commands
from utils.database import (
    get_user,
    create_user,
    update_balance,
    record_trade,
    get_stock_price,
    get_server_settings,
    DB_PATH,
    ensure_guild_market,
)
from utils.helpers import dm_and_delete
from utils.logger import log_error


class Trading(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def cog_command_error(self, ctx, error):
        from utils.errors import WrongChannel
        if isinstance(error, WrongChannel):
            return
        await log_error(self.__class__.__name__, error, ctx)
        await ctx.send("⚠️ An internal error occurred. The issue has been logged.")

    # ----------------------------
    # Register
    # ----------------------------
    @commands.command()
    async def register(self, ctx):
        """Register to participate in the Toilet Exchange"""
        await ensure_guild_market(ctx.guild.id)
        user = await get_user(ctx.author.id, ctx.guild.id)
        if user:
            return await ctx.send("You already have an account!")

        settings = await get_server_settings(ctx.guild.id)
        starting_money = settings.get("starting_money", 1000.0)

        await create_user(ctx.author.id, ctx.guild.id)

        embed = discord.Embed(
            title="Welcome to the Toilet Exchange!",
            description=(
                f"You’ve been registered and start with **${starting_money:,.2f}**.\n\n"
                "Use `!buy`, `!sell`, or `!portfolio` to begin trading.\n"
                "See the website for details:\n"
                "<https://toilet-exchange.neocities.org/>"
            ),
            color=discord.Color.green(),
        )
        embed.set_footer(text="Happy trading!")
        await ctx.send(embed=embed)

    # ----------------------------
    # Balance
    # ----------------------------
    @commands.command()
    async def balance(self, ctx):
        """View your current balance"""
        user = await get_user(ctx.author.id, ctx.guild.id)
        if not user:
            return await ctx.send("You don’t have an account. Use !register first.")
        await dm_and_delete(ctx, f"💰 Balance: ${user[1]:.2f}")

    # ----------------------------
    # Buy
    # ----------------------------
    @commands.command()
    async def buy(self, ctx, arg1: str = None, arg2: str = None):
        """Buy stock — usage: !buy GMD 2"""
        if not arg1 or not arg2:
            return await dm_and_delete(ctx, "❌ Usage: `!buy GMD 2`")

        if arg1.isdigit():
            qty, ticker = int(arg1), arg2
        elif arg2.isdigit():
            qty, ticker = int(arg2), arg1
        else:
            return await dm_and_delete(ctx, "❌ Quantity must be a number.")

        if qty <= 0:
            return await dm_and_delete(ctx, "❌ Quantity must be greater than 0.")

        user = await get_user(ctx.author.id, ctx.guild.id)
        if not user:
            return await ctx.send("No account found. Use !register.")

        price = await get_stock_price(ticker, ctx.guild.id)
        if price is None:
            return await dm_and_delete(ctx, "❌ Invalid stock ticker for this server.")

        total_cost = price * qty
        if float(user[1]) < total_cost:
            return await dm_and_delete(ctx, "❌ Insufficient funds for this purchase.")

        await update_balance(ctx.author.id, ctx.guild.id, -total_cost)
        result = await record_trade(ctx.author.id, ctx.guild.id, ticker.upper(), qty, "BUY")
        await dm_and_delete(
            ctx, f"✅ Bought {qty} × {ticker.upper()} for ${total_cost:.2f}.\n{result}"
        )

    # ----------------------------
    # Sell
    # ----------------------------
    @commands.command()
    async def sell(self, ctx, arg1: str = None, arg2: str = None):
        """Sell stock — usage: !sell GMD 2"""
        if not arg1 or not arg2:
            return await dm_and_delete(ctx, "❌ Usage: `!sell GMD 2`")

        if arg1.isdigit():
            qty, ticker = int(arg1), arg2
        elif arg2.isdigit():
            qty, ticker = int(arg2), arg1
        else:
            return await dm_and_delete(ctx, "❌ Quantity must be a number.")

        if qty <= 0:
            return await dm_and_delete(ctx, "❌ Quantity must be greater than 0.")

        user = await get_user(ctx.author.id, ctx.guild.id)
        if not user:
            return await ctx.send("No account found. Use !register.")

        price = await get_stock_price(ticker, ctx.guild.id)
        if price is None:
            return await dm_and_delete(ctx, "❌ Invalid stock ticker for this server.")

        ticker = ticker.upper()
        async with aiosqlite.connect(DB_PATH) as db:
            cur = await db.execute("""
                SELECT SUM(CASE WHEN side='BUY' THEN qty ELSE -qty END)
                FROM trades
                WHERE user_id=? AND guild_id=? AND ticker=?
            """, (str(ctx.author.id), str(ctx.guild.id), ticker))
            owned = (await cur.fetchone())[0] or 0

        if owned < qty:
            return await dm_and_delete(ctx, f"❌ You only own {owned} shares of {ticker}.")

        total_gain = price * qty
        await update_balance(ctx.author.id, ctx.guild.id, total_gain)
        result = await record_trade(ctx.author.id, ctx.guild.id, ticker, qty, "SELL")
        await dm_and_delete(
            ctx, f"✅ Sold {qty} × {ticker} for ${total_gain:.2f}.\n{result}"
        )

    # ----------------------------
    # Portfolio
    # ----------------------------
    @commands.command()
    async def portfolio(self, ctx):
        """Show your current portfolio"""
        async with aiosqlite.connect(DB_PATH) as db:
            cur = await db.execute(
                "SELECT cash FROM users WHERE discord_id=? AND guild_id=?",
                (str(ctx.author.id), str(ctx.guild.id)),
            )
            user_row = await cur.fetchone()
            if not user_row:
                return await ctx.send("You don’t have an account. Use !register.")
            cash = user_row[0]

            cur = await db.execute("""
                SELECT t.ticker, s.name, SUM(CASE WHEN t.side='BUY' THEN t.qty ELSE -t.qty END)
                FROM trades t
                LEFT JOIN stocks s ON t.ticker = s.ticker AND s.guild_id = t.guild_id
                WHERE t.user_id=? AND t.guild_id=?
                GROUP BY t.ticker, s.name
            """, (str(ctx.author.id), str(ctx.guild.id)))
            holdings = await cur.fetchall()

        embed = discord.Embed(
            title=f"{ctx.author.display_name}'s Portfolio",
            color=discord.Color.purple(),
        )
        total_value = cash
        embed.add_field(name="💰 Cash", value=f"${cash:.2f}", inline=False)

        if not holdings:
            embed.set_footer(text=f"Total Value: ${cash:.2f}")
            return await dm_and_delete(ctx, embed=embed)

        for ticker, name, qty in holdings:
            if qty <= 0:
                continue
            price = await get_stock_price(ticker, ctx.guild.id) or 0
            value = price * qty
            total_value += value
            embed.add_field(
                name=f"{name or 'Unknown'} ({ticker})",
                value=f"{qty} × ${price:.2f} = ${value:.2f}",
                inline=False,
            )

        embed.set_footer(text=f"Total Portfolio Value: ${total_value:.2f}")
        await dm_and_delete(ctx, embed=embed)

    # ----------------------------
    # Delete Account
    # ----------------------------
    @commands.command()
    async def delete_account(self, ctx):
        """Delete your account and all data for this server"""
        user_id, guild_id = str(ctx.author.id), str(ctx.guild.id)
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("DELETE FROM trades WHERE user_id=? AND guild_id=?", (user_id, guild_id))
            await db.execute(
                "DELETE FROM portfolios WHERE user_id=(SELECT id FROM users WHERE discord_id=? AND guild_id=?)",
                (user_id, guild_id),
            )
            await db.execute("DELETE FROM users WHERE discord_id=? AND guild_id=?", (user_id, guild_id))
            await db.commit()

        await ctx.author.send("🗑️ Your Toilet Exchange account for this server has been deleted.")
        await ctx.message.delete()


async def setup(bot):
    from utils.database import init_db
    await init_db()
    await bot.add_cog(Trading(bot))
    print("✅ Trading cog loaded and per-server mode active")