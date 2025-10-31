# cogs/market.py
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
        self._last_market_update = {}
        self.update_market_loop.start()

    def cog_unload(self):
        self.update_market_loop.cancel()

    async def cog_command_error(self, ctx, error):
        from utils.errors import WrongChannel
        if isinstance(error, WrongChannel):
            return
        await log_error(self.__class__.__name__, error, ctx)
        await ctx.send("⚠️ An internal error occurred. The issue has been logged.")

    # -------------------------------------------------
    # MARKET LOOP — runs once per minute to *check* hourly updates
    # -------------------------------------------------
    @tasks.loop(minutes=1)
    async def update_market_loop(self):
        """Update prices for each guild independently (hour-based schedule)."""
        now = discord.utils.utcnow()
        for guild in self.bot.guilds:
            try:
                settings = await get_server_settings(guild.id)
                rate = int(settings.get("market_update_rate", 1))  # now represents hours
                last = self._last_market_update.get(guild.id)

                if not last or (now - last).total_seconds() >= rate * 3600:
                    await self._update_prices_for_guild(guild, settings)
                    self._last_market_update[guild.id] = now
                    print(f"[Market] Updated prices for {guild.name} (every {rate} hour{'s' if rate != 1 else ''})")
            except Exception as e:
                print(f"[Market] Error updating {guild.name}: {e}")

    @update_market_loop.before_loop
    async def before_update_market_loop(self):
        await self.bot.wait_until_ready()

    # -------------------------------------------------
    # PRICE UPDATE PER GUILD
    # -------------------------------------------------
    async def _update_prices_for_guild(self, guild, settings):
        """Update stock prices for a specific guild and notify the #toilet-exchange channel."""
        guild_id = str(guild.id)

        async with aiosqlite.connect(DB_PATH) as db:
            cur = await db.execute(
                "SELECT ticker, price, risk FROM stocks WHERE guild_id=?", (guild_id,)
            )
            stocks = await cur.fetchall()

            for ticker, price, risk in stocks:
                low, high = get_price_change_range(risk)
                raw_change = random.uniform(low, high)
                mean_bias = float(settings.get("market_bias", 0.0008))
                target_price = float(settings.get("target_price", 100.0))
                recovery_boost = 1.5 + (1.0 - price) if price < 1.0 else 1.0
                bias_adjusted = raw_change + mean_bias * ((target_price - price) / target_price)
                final_change = price * bias_adjusted * recovery_boost
                new_price = max(price + final_change, 0.01)

                await db.execute(
                    "UPDATE stocks SET price=? WHERE ticker=? AND guild_id=?",
                    (new_price, ticker, guild_id),
                )
                await db.execute(
                    "INSERT INTO price_history (ticker, guild_id, price) VALUES (?, ?, ?)",
                    (ticker, guild_id, new_price),
                )

            await db.commit()

        # ✅ Send a simple notice once per update
        channel = discord.utils.get(guild.text_channels, name="toilet-exchange")
        if channel:
            try:
                await channel.send("📈 The market has been updated! Check new prices with `!stocks`.")
            except discord.Forbidden:
                print(f"[Market] Missing permission to send message in {guild.name}.")
            except Exception as e:
                print(f"[Market] Error sending update message in {guild.name}: {e}")

    # -------------------------------------------------
    # COMMANDS
    # -------------------------------------------------
    @commands.command()
    async def price(self, ctx, ticker: str):
        """Get price for a stock."""
        async with aiosqlite.connect(DB_PATH) as db:
            cur = await db.execute(
                "SELECT price FROM stocks WHERE ticker=? AND guild_id=?",
                (ticker.upper(), str(ctx.guild.id)),
            )
            row = await cur.fetchone()
        if not row:
            return await ctx.send("Invalid stock ticker.")
        await ctx.send(f"{ticker.upper()} price: ${row[0]:.2f}")

    @commands.command(name="stocks")
    async def list_stocks(self, ctx):
        """Show all active stocks for this guild."""
        async with aiosqlite.connect(DB_PATH) as db:
            cur = await db.execute(
                "SELECT ticker, name, price, risk FROM stocks WHERE guild_id=?",
                (str(ctx.guild.id),),
            )
            rows = await cur.fetchall()

        if not rows:
            return await ctx.send("No stocks found in this market.")

        embed = discord.Embed(
            title=f"📈 {ctx.guild.name} Market Overview",
            color=discord.Color.blurple(),
        )

        for ticker, name, price, risk in rows:
            avg = await get_moving_average(ticker, ctx.guild.id, window=5)
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
        """Show line chart of recent prices (per server)."""
        if not tickers:
            return await ctx.send("Please specify at least one ticker or use `!trend all`.")

        tickers = [t.upper() for t in tickers]
        guild_id = str(ctx.guild.id)

        if len(tickers) == 1 and tickers[0].lower() == "all":
            async with aiosqlite.connect(DB_PATH) as db:
                cur = await db.execute("SELECT ticker FROM stocks WHERE guild_id=?", (guild_id,))
                tickers = [r[0] for r in await cur.fetchall()]
            if not tickers:
                return await ctx.send("No stocks found in this market.")

        data = {}
        async with aiosqlite.connect(DB_PATH) as db:
            for ticker in tickers:
                cur = await db.execute(
                    "SELECT price FROM price_history WHERE ticker=? AND guild_id=? ORDER BY id DESC LIMIT 20",
                    (ticker, guild_id),
                )
                rows = await cur.fetchall()
                if rows:
                    data[ticker] = list(reversed([r[0] for r in rows]))

        if not data:
            return await ctx.send("No recent data found for the given tickers.")

        plt.figure(figsize=(6, 4))
        for ticker, prices in data.items():
            base = prices[0]
            norm = [p / base * 100 for p in prices]
            plt.plot(norm, marker="o", linewidth=2, label=ticker)

        plt.title(f"{ctx.guild.name} Market Trend (Normalized)")
        plt.xlabel("Most Recent Updates")
        plt.ylabel("Relative Price (%)")
        plt.legend()
        plt.grid(True)
        plt.tight_layout()

        buf = io.BytesIO()
        plt.savefig(buf, format="png")
        buf.seek(0)
        plt.close()

        file = discord.File(buf, filename="trend.png")
        embed = discord.Embed(title="📈 Market Trends", color=discord.Color.blurple())
        embed.set_image(url="attachment://trend.png")
        await ctx.send(file=file, embed=embed)


async def setup(bot):
    await bot.add_cog(Market(bot))