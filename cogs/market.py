# cogs/market.py
import random
import statistics
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
        self._momentum = {}  # guild_id -> {ticker: recent momentum}
        self.update_market_loop.start()

    async def on_ready(self):
        """Runs after bot startup: sends patch notes and checks for missed updates."""
        print("[Market] Checking for missed updates and sending patch notes...")

        # Try to load patch notes from VERSION.txt
        try:
            with open("VERSION.txt", "r", encoding="utf-8") as f:
                patch_message = f.read().strip()
        except FileNotFoundError:
            patch_message = "🧻 Toilet Exchange has started! (Patch notes unavailable.)"

        now = discord.utils.utcnow()

        for guild in self.bot.guilds:
            settings = await get_server_settings(guild.id)
            rate = int(settings.get("market_update_rate", 1))  # hours

            # Check last update time
            async with aiosqlite.connect(DB_PATH) as db:
                cur = await db.execute(
                    "SELECT MAX(timestamp) FROM price_history WHERE guild_id=?",
                    (str(guild.id),)
                )
                last_update = await cur.fetchone()
                last_time = None
                if last_update and last_update[0]:
                    try:
                        last_time = discord.utils.parse_time(last_update[0])
                    except Exception:
                        pass

            missed_update = False
            if last_time:
                elapsed = (now - last_time).total_seconds() / 3600
                if elapsed >= rate:
                    missed_update = True

            # Send patch notes to channel
            channel = discord.utils.get(guild.text_channels, name="toilet-exchange")
            if channel:
                try:
                    await channel.send(patch_message)
                except Exception as e:
                    print(f"[Market] Could not send startup message in {guild.name}: {e}")

            # Perform catch-up update only if missed
            if missed_update:
                print(f"[Market] Missed update detected for {guild.name}, catching up now...")
                await self._update_prices_for_guild(guild, settings)
                self._last_market_update[guild.id] = now

    def cog_unload(self):
        self.update_market_loop.cancel()

    async def cog_command_error(self, ctx, error):
        from utils.errors import WrongChannel
        if isinstance(error, WrongChannel):
            return
        await log_error(self.__class__.__name__, error, ctx)
        await ctx.send("⚠️ An internal error occurred. The issue has been logged.")

    # -------------------------------------------------
    # MARKET LOOP — checks every minute, updates hourly
    # -------------------------------------------------
    @tasks.loop(minutes=1)
    async def update_market_loop(self):
        now = discord.utils.utcnow()
        for guild in self.bot.guilds:
            try:
                settings = await get_server_settings(guild.id)
                rate = int(settings.get("market_update_rate", 1))  # in hours
                last = self._last_market_update.get(guild.id)

                if not last or (now - last).total_seconds() >= rate * 3600:
                    await self._update_prices_for_guild(guild, settings)
                    self._last_market_update[guild.id] = now
                    print(f"[Market] Updated prices for {guild.name} (every {rate}h)")
            except Exception as e:
                print(f"[Market] Error updating {guild.name}: {e}")

    @update_market_loop.before_loop
    async def before_update_market_loop(self):
        await self.bot.wait_until_ready()

    # -------------------------------------------------
    # PRICE UPDATE PER GUILD (enhanced)
    # -------------------------------------------------
    async def _update_prices_for_guild(self, guild, settings):
        """Update stock prices for a specific guild using adaptive dynamic median targets."""
        guild_id = str(guild.id)
        market_sentiment = random.uniform(-0.002, 0.002)  # mild global mood swing

        async with aiosqlite.connect(DB_PATH) as db:
            cur = await db.execute("SELECT ticker, price, risk FROM stocks WHERE guild_id=?", (guild_id,))
            stocks = await cur.fetchall()

            if not stocks:
                return

            # 🧭 Always use dynamic median-based target
            prices = [p for _, p, _ in stocks]
            target_price = statistics.median(prices)

            mean_bias = float(settings.get("market_bias", 0.0008))
            if guild.id not in self._momentum:
                self._momentum[guild.id] = {}

            for ticker, price, risk in stocks:
                # --- Volatility based on risk ---
                low, high = get_price_change_range(risk)
                raw_change = random.uniform(low, high) + market_sentiment

                # --- Local drift using stock-relative bias ---
                base_target = price * random.uniform(0.95, 1.05)
                drift_bias = mean_bias * ((base_target - price) / base_target)

                # --- Recovery boost for low-value stocks ---
                recovery_boost = 1.5 + (1.0 - price) if price < 1.0 else 1.0

                # --- Momentum persistence (keeps trends alive briefly) ---
                prev_mom = self._momentum[guild.id].get(ticker, 0)
                momentum = 0.7 * prev_mom + 0.3 * (raw_change * price)
                self._momentum[guild.id][ticker] = momentum

                # --- Volatility scaling by price ---
                volatility_scale = 1 + (price / 500)

                # --- Combine all forces ---
                final_change = (
                    price * (raw_change + drift_bias) * recovery_boost * volatility_scale
                    + 0.05 * momentum
                )

                new_price = max(price + final_change, 0.01)

                await db.execute(
                    "UPDATE stocks SET price=? WHERE ticker=? AND guild_id=?",
                    (round(new_price, 6), ticker, guild_id),
                )
                await db.execute(
                    "INSERT INTO price_history (ticker, guild_id, price) VALUES (?, ?, ?)",
                    (ticker, guild_id, new_price),
                )

            await db.commit()

        # ✅ Market update announcement
        channel = discord.utils.get(guild.text_channels, name="toilet-exchange")
        if channel:
            try:
                await channel.send("📈 The market has been updated! Check new prices with `!stocks`.")
            except discord.Forbidden:
                print(f"[Market] Missing permission to send message in {guild.name}.")
            except Exception as e:
                print(f"[Market] Error sending update message in {guild.name}: {e}")

            await db.commit()

        # --- announce market update ---
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
    cog = Market(bot)
    await bot.add_cog(cog)
    bot.add_listener(cog.on_ready, "on_ready")
    print("[Market] Cog loaded and startup listener attached.")