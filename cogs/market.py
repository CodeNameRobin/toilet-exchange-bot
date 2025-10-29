# market.py
import random
import aiosqlite
import matplotlib.pyplot as plt
import io
import discord
from discord.ext import commands, tasks
from utils.database import DB_PATH, get_moving_average, get_server_settings
from utils.helpers import get_price_change_range
from utils.logger import log_error


class Market(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.update_market_loop.start()  # renamed for clarity

    def cog_unload(self):
        self.update_market_loop.cancel()

    async def cog_command_error(self, ctx, error):
        from utils.errors import WrongChannel

        # Ignore wrong channel or DM errors silently
        if isinstance(error, WrongChannel):
            return

        # Log everything else
        await log_error(self.__class__.__name__, error, ctx)
        await ctx.send("An internal error occurred. The issue has been logged.")

    @tasks.loop(minutes=1)
    async def update_market_loop(self):
        """Dynamically update market prices per guild based on server settings."""
        now = discord.utils.utcnow()

        # Loop through all guilds
        for guild in self.bot.guilds:
            try:
                settings = await get_server_settings(guild.id)
                rate = int(settings.get("market_update_rate", 10))  # minutes
                last = getattr(self, "_last_market_update", {})

                # Check if enough time passed for this guild
                if guild.id not in last or (now - last[guild.id]).total_seconds() >= rate * 60:
                    await self._update_prices_for_guild(guild)
                    last[guild.id] = now
                    print(f"[Market] Prices updated for {guild.name} ({rate} min interval)")
                self._last_market_update = last

            except Exception as e:
                print(f"[Market] Error updating market for {guild.name}: {e}")

    async def _update_prices_for_guild(self, guild):
        """Update stock prices in the shared database."""
        async with aiosqlite.connect(DB_PATH) as db:
            cur = await db.execute("SELECT ticker, price FROM stocks")
            stocks = await cur.fetchall()
            cur = await db.execute("SELECT ticker, price, risk FROM stocks")
            stocks = await cur.fetchall()

            for ticker, price, risk in stocks:
                low, high = get_price_change_range(risk)
                change = price * random.uniform(low, high)
                new_price = max(price + change, 0.01)  # prevent zero
                await db.execute(
                    "UPDATE stocks SET price=? WHERE ticker=?", (new_price, ticker)
                )
                await db.execute(
                    "INSERT INTO price_history (ticker, price) VALUES (?, ?)",
                    (ticker, new_price),
                )
            await db.commit()

    @update_market_loop.before_loop
    async def before_update_market_loop(self):
        await self.bot.wait_until_ready()

    # -----------------------------
    # Manual / Info Commands
    # -----------------------------

    @commands.command()
    async def price(self, ctx, ticker: str):
        """List price of a specific stock."""
        async with aiosqlite.connect(DB_PATH) as db:
            cur = await db.execute(
                "SELECT price FROM stocks WHERE ticker=?", (ticker.upper(),)
            )
            row = await cur.fetchone()
            if not row:
                return await ctx.send("Invalid stock ticker.")
            await ctx.send(f"{ticker.upper()} price: ${row[0]:.2f}")

    @commands.command(name="stocks")
    async def list_stocks(self, ctx):
        async with aiosqlite.connect(DB_PATH) as db:
            cur = await db.execute("SELECT ticker, name, price, risk FROM stocks")
            rows = await cur.fetchall()

        if not rows:
            return await ctx.send("No stocks found.")

        embed = discord.Embed(
            title="📈 Market Overview",
            description="Recent stock movements:",
            color=discord.Color.blurple(),
        )

        for ticker, name, price, risk in rows:
            avg = await get_moving_average(ticker, window=5)
            if not avg:
                change = "🟦 No data"
            elif price > avg:
                change = f"🟢 Up ({price - avg:+.2f})"
            elif price < avg:
                change = f"🔴 Down ({price - avg:+.2f})"
            else:
                change = "⚪ Stable"

            embed.add_field(
                name=f"{name}\n*({ticker})*",
                value=f"${price:.2f} | {change}\nRisk: **{risk.title()}**",
                inline=False,
            )

        await ctx.send(embed=embed)

    @commands.command(name="trend")
    async def show_trend(self, ctx, *tickers):
        """Show a line chart of recent price changes for one or more stocks (or all)."""
        if not tickers:
            return await ctx.send("Please specify at least one ticker, or use `!trend all`.")

        tickers = [t.upper() for t in tickers]

        # Handle 'all'
        if len(tickers) == 1 and tickers[0].lower() == "all":
            async with aiosqlite.connect(DB_PATH) as db:
                cur = await db.execute("SELECT ticker FROM stocks")
                tickers = [r[0] for r in await cur.fetchall()]
            if not tickers:
                return await ctx.send("No stocks found in the market.")

        async with aiosqlite.connect(DB_PATH) as db:
            data = {}
            for ticker in tickers:
                cur = await db.execute(
                    "SELECT price FROM price_history WHERE ticker=? ORDER BY id DESC LIMIT 20",
                    (ticker,),
                )
                rows = await cur.fetchall()
                if rows:
                    data[ticker] = list(reversed([r[0] for r in rows]))

        if not data:
            return await ctx.send("No recent data found for the given tickers.")

        # Normalize for fair comparison
        normalized_data = {}
        for ticker, prices in data.items():
            base = prices[0]
            normalized_data[ticker] = [p / base * 100 for p in prices]

        # Plot the data
        plt.figure(figsize=(6, 4))
        for ticker, prices in normalized_data.items():
            plt.plot(prices, marker="o", linewidth=2, label=ticker)

        plt.title("Stock Trend Comparison (Normalized)")
        plt.xlabel("Most Recent Updates")
        plt.ylabel("Relative Price (%)")
        plt.legend()
        plt.grid(True)
        plt.tight_layout()

        buffer = io.BytesIO()
        plt.savefig(buffer, format="png")
        buffer.seek(0)
        plt.close()

        file = discord.File(buffer, filename="trend.png")
        embed = discord.Embed(
            title="📈 Stock Trend Comparison",
            description=f"Comparing {', '.join(tickers)}",
            color=discord.Color.blurple(),
        )
        embed.set_image(url="attachment://trend.png")
        await ctx.send(file=file, embed=embed)

async def setup(bot):
    await bot.add_cog(Market(bot))