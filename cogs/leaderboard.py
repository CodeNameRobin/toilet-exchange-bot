# cogs/leaderboard.py
import datetime
import discord
from discord.ext import commands, tasks
from utils.database import (
    get_leaderboard,
    update_leaderboard_cache,
    get_server_settings,
    get_cached_leaderboard,  # ✅ Added import
)
from utils.logger import log_error


class Leaderboard(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self._last_update = {}        # guild_id -> datetime of last cache update
        self._last_posted_day = {}    # guild_id -> yyyy-mm-dd string (to avoid double-posting in same day)
        self.update_cache_loop.start()
        self.daily_post_loop.start()

    def cog_unload(self):
        self.update_cache_loop.cancel()
        self.daily_post_loop.cancel()

    async def cog_command_error(self, ctx, error):
        from utils.errors import WrongChannel
        if isinstance(error, WrongChannel):
            return
        await log_error(self.__class__.__name__, error, ctx)
        await ctx.send("An internal error occurred. The issue has been logged.")

    # ------------------------------
    # A) Cache updater (per guild)
    # ------------------------------
    @tasks.loop(minutes=1)
    async def update_cache_loop(self):
        """Update leaderboard cache per guild based on each guild's leaderboard_update_rate."""
        now = datetime.datetime.utcnow()
        for guild in self.bot.guilds:
            try:
                settings = await get_server_settings(guild.id)
                rate_min = int(settings.get("leaderboard_update_rate", 10))
                last = self._last_update.get(guild.id)

                # ✅ Pass guild.id to update_leaderboard_cache
                if last is None or (now - last).total_seconds() >= rate_min * 60:
                    await update_leaderboard_cache(guild.id)
                    self._last_update[guild.id] = now
                    # print(f"[Leaderboard] Cache updated for {guild.name} at {now} (every {rate_min}m)")
            except Exception as e:
                print(f"[Leaderboard] Error updating cache for {guild.name}: {e}")

    @update_cache_loop.before_loop
    async def _wait_ready_cache(self):
        await self.bot.wait_until_ready()

    # ------------------------------
    # B) Daily poster (per guild)
    # ------------------------------
    @tasks.loop(minutes=1)
    async def daily_post_loop(self):
        """Post daily leaderboard at configured UTC time per guild."""
        now_utc = datetime.datetime.now(datetime.timezone.utc)
        today_key = now_utc.date().isoformat()

        for guild in self.bot.guilds:
            try:
                settings = await get_server_settings(guild.id)
                post_time_str = (settings.get("leaderboard_post_time") or "23:00").strip()

                # allow disabling via "none"
                if not post_time_str or post_time_str.lower() == "none":
                    continue

                try:
                    target_hour, target_minute = map(int, post_time_str.split(":"))
                except ValueError:
                    # bad setting; skip
                    continue

                # ✅ Only post if time matches and hasn't posted today
                if (
                    now_utc.hour == target_hour
                    and now_utc.minute == target_minute
                    and self._last_posted_day.get(guild.id) != today_key
                ):
                    await update_leaderboard_cache(guild.id)
                    await self._post_leaderboard(guild)
                    self._last_posted_day[guild.id] = today_key

            except Exception as e:
                print(f"[Leaderboard] Error in daily_post_loop for {guild.name}: {e}")

    @daily_post_loop.before_loop
    async def _wait_ready_daily(self):
        await self.bot.wait_until_ready()

    # ------------------------------
    # C) Manual command (on demand)
    # ------------------------------
    @commands.command(name="leaderboard")
    async def leaderboard_cmd(self, ctx):
        """Manually post the current leaderboard."""
        await update_leaderboard_cache(ctx.guild.id)
        await self._post_leaderboard(ctx.guild)

    # ------------------------------
    # Helpers
    # ------------------------------
    async def _resolve_member(self, guild: discord.Guild, uid_text: str):
        """Safely get user display name and mention from a user ID."""
        try:
            uid = int(uid_text)
        except ValueError:
            return (uid_text, uid_text)

        member = guild.get_member(uid)
        if member:
            return (member.display_name, member.mention)

        try:
            user = await self.bot.fetch_user(uid)
            return (user.name, f"<@{uid}>")
        except Exception:
            return (f"User {uid}", f"<@{uid}>")

    async def _post_leaderboard(self, guild: discord.Guild):
        """Build and post the leaderboard embed for one guild, using cached data."""
        rows = await get_cached_leaderboard(guild.id, limit=10)
        embed = discord.Embed(
            title="📊 Stock Market Leaderboard",
            description="Daily update from the Toilet Exchange",
            color=discord.Color.gold(),
        )

        if not rows:
            embed.add_field(
                name="No traders yet!",
                value="Be the first to register and trade!",
                inline=False,
            )
        else:
            for i, (discord_id, total_value, last_updated) in enumerate(rows, start=1):
                display_name, mention = await self._resolve_member(guild, str(discord_id))
                rank_emoji = "🥇" if i == 1 else "🥈" if i == 2 else "🥉" if i == 3 else f"#{i}"
                title = f"{rank_emoji} {display_name}"
                embed.add_field(
                    name=title,
                    value=f"💰 ${float(total_value):,.2f}",
                    inline=False,
                )

            if rows and rows[0][2]:
                embed.set_footer(text=f"Cache last updated: {rows[0][2]} UTC")

        channel = discord.utils.get(guild.text_channels, name="toilet-exchange")
        if channel:
            try:
                await channel.send(embed=embed)
            except discord.Forbidden:
                print(f"⚠️ No permission to post leaderboard in {guild.name}")

async def setup(bot):
    await bot.add_cog(Leaderboard(bot))