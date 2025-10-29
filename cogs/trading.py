# trading.py
import aiosqlite
import discord
from discord.ext import commands
from utils.database import get_user, create_user, update_balance, record_trade, get_stock_price, DB_PATH
from utils.helpers import dm_and_delete
from utils.logger import log_error


class Trading(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def cog_command_error(self, ctx, error):
        from utils.errors import WrongChannel

        # Ignore wrong channel or DM errors silently
        if isinstance(error, WrongChannel):
            return

        # Log everything else
        await log_error(self.__class__.__name__, error, ctx)
        await ctx.send("An internal error occurred. The issue has been logged.")

    @commands.command()
    async def register(self, ctx):
        """Register to participate in the TOILET EXCHANGE"""
        user = await get_user(ctx.author.id, ctx.guild.id)
        if user:
            return await ctx.send("You already have an account!")

        # Fetch server settings
        from utils.database import get_server_settings
        settings = await get_server_settings(ctx.guild.id)
        starting_money = settings.get("starting_money", 1000.0)

        await create_user(ctx.author.id, ctx.guild.id)

        # Welcome message
        embed = discord.Embed(
            title="Welcome to the Toilet Exchange!",
            description=(
                f"Your account has been created — you start with **${starting_money:,.2f}**.\n\n"
                "Begin trading by using `!buy`, `!sell`, and `!portfolio`.\n"
                "For a full list of commands, type `!info` or visit:\n"
                "<https://toilet-exchange.neocities.org/>"
            ),
            color=discord.Color.green()
        )
        embed.set_footer(text="Happy trading!")

        await ctx.send(embed=embed)

    @commands.command()
    async def balance(self, ctx):
        """View your current balance"""
        user = await get_user(ctx.author.id, ctx.guild.id)
        if not user:
            return await ctx.send("No account found. Use !register.")
        await dm_and_delete(f"Balance: ${user[1]:.2f}")  # user[1] is cash

    @commands.command()
    async def buy(self, ctx, arg1: str = None, arg2: str = None):
        """Buy stock - works with !buy GMD 2 or !buy 2 GOMADINC"""
        # Validate input
        if not arg1 or not arg2:
            return await dm_and_delete(ctx, "❌ Incorrect usage. Example: `!buy GMD 2`")

        # Detect which argument is quantity
        if arg1.isdigit():
            qty = int(arg1)
            ticker = arg2
        elif arg2.isdigit():
            qty = int(arg2)
            ticker = arg1
        else:
            return await dm_and_delete(ctx, "❌ Quantity must be a number. Example: `!buy GMD 2`")

        if qty <= 0:
            return await dm_and_delete(ctx, "❌ Quantity must be greater than 0.")

        # Check registration
        user = await get_user(ctx.author.id, ctx.guild.id)
        if not user:
            # This one stays public so the user knows to register
            return await ctx.send("No account found. Use !register.")

        # Check ticker and balance
        price = await get_stock_price(ticker)
        if price is None:
            return await dm_and_delete(ctx, "❌ Invalid stock ticker.")

        total_cost = price * qty
        if float(user[1]) < total_cost:
            return await dm_and_delete(ctx, "❌ Not enough cash for this purchase.")

        # Perform transaction
        await update_balance(ctx.author.id, ctx.guild.id, -total_cost)
        result = await record_trade(ctx.author.id, ctx.guild.id, ticker.upper(), qty, side="BUY")

        # DM confirmation privately
        await dm_and_delete(ctx, f"✅ Bought {qty} {ticker.upper()} for ${total_cost:.2f}.\n{result}")

    @commands.command()
    async def sell(self, ctx, arg1: str = None, arg2: str = None):
        """Sell a stock -- usage: !sell GMD 2 or !sell 2 GOMADINC"""
        # Handle invalid usage
        if not arg1 or not arg2:
            return await dm_and_delete(ctx, "❌ Incorrect usage. Example: `!sell GMD 2`")

        # Detect which arg is the quantity
        if arg1.isdigit():
            qty = int(arg1)
            ticker = arg2
        elif arg2.isdigit():
            qty = int(arg2)
            ticker = arg1
        else:
            return await dm_and_delete(ctx, "❌ Quantity must be a number. Example: `!sell GMD 2`")

        if qty <= 0:
            return await dm_and_delete(ctx, "❌ Quantity must be greater than 0.")

        user = await get_user(ctx.author.id, ctx.guild.id)
        if not user:
            # This one stays visible in the channel
            return await ctx.send("No account found. Use !register.")

        price = await get_stock_price(ticker)
        if price is None:
            return await dm_and_delete(ctx, "❌ Invalid stock ticker.")

        ticker = ticker.upper()

        # Check how many shares the user owns
        async with aiosqlite.connect(DB_PATH) as db:
            cur = await db.execute("""
                SELECT SUM(CASE WHEN side='BUY' THEN qty ELSE -qty END)
                FROM trades
                WHERE user_id=? AND guild_id=? AND ticker=?
            """, (str(ctx.author.id), str(ctx.guild.id), ticker))
            result = await cur.fetchone()
            owned_qty = result[0] or 0

        if owned_qty < qty:
            return await dm_and_delete(ctx, f"❌ You only own {owned_qty} shares of {ticker}, not {qty}.")

        total_gain = price * qty
        await update_balance(ctx.author.id, ctx.guild.id, total_gain)
        result = await record_trade(ctx.author.id, ctx.guild.id, ticker, qty, side="SELL")

        # DM the success message, delete the command from the server
        await dm_and_delete(ctx, f"✅ Sold {qty} {ticker} for ${total_gain:.2f}.\n{result}")

    @commands.command(name="portfolio")
    async def portfolio(self, ctx):
        """Show your current portfolio, including stock names, tickers, and cash."""
        async with aiosqlite.connect(DB_PATH) as db:
            # Get user cash
            cur = await db.execute(
                "SELECT cash FROM users WHERE discord_id=? AND guild_id=?",
                (str(ctx.author.id), str(ctx.guild.id))
            )
            user_row = await cur.fetchone()
            if not user_row:
                return await ctx.send("You don’t have an account yet. Use !register.")
            cash = user_row[0]

            # Holdings with stock names and tickers
            cur = await db.execute(
                """
                SELECT t.ticker, s.name, SUM(CASE WHEN t.side='BUY' THEN t.qty ELSE -t.qty END)
                FROM trades t
                LEFT JOIN stocks s ON t.ticker = s.ticker
                WHERE t.user_id=? AND t.guild_id=?
                GROUP BY t.ticker, s.name
                """,
                (str(ctx.author.id), str(ctx.guild.id))
            )
            holdings = await cur.fetchall()

        if not holdings:
            return await ctx.author.send(f"You have ${cash:.2f} in cash and no stocks.")

        embed = discord.Embed(
            title=f"{ctx.author.display_name}'s Portfolio",
            color=discord.Color.purple()
        )
        total_value = cash
        embed.add_field(name="💰 Cash", value=f"${cash:.2f}", inline=False)

        for ticker, name, qty in holdings:
            if qty <= 0:
                continue
            price = await get_stock_price(ticker)
            if price is None:
                price = 0
            value = price * qty
            total_value += value
            embed.add_field(
                name=f"**{name}**\n*({ticker})*",
                value=f"{qty} × ${price:.2f} = ${value:.2f}",
                inline=False
            )

        embed.set_footer(text=f"Total Portfolio Value: ${total_value:.2f}")
        await dm_and_delete(ctx, embed=embed)

    @commands.command(name="delete_account")
    async def delete_account(self, ctx):
        """Delete your own account and all related data."""
        user_id = str(ctx.author.id)
        guild_id = str(ctx.guild.id)

        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("DELETE FROM trades WHERE user_id=? AND guild_id=?", (user_id, guild_id))
            await db.execute(
                "DELETE FROM portfolios WHERE user_id=(SELECT id FROM users WHERE discord_id=? AND guild_id=?)",
                (user_id, guild_id))
            await db.execute("DELETE FROM users WHERE discord_id=? AND guild_id=?", (user_id, guild_id))
            await db.commit()

        await ctx.author.send("🗑️ Your Toilet Exchange account and all related data have been deleted.")
        await ctx.message.delete()

async def setup(bot):
    from utils.database import init_db
    await init_db()  # Ensure DB and tables exist
    await bot.add_cog(Trading(bot))
    print("✅ Trading cog loaded")