# cogs/misc.py
import discord
from discord.ext import commands
from utils.logger import log_error
import os
from dotenv import load_dotenv

load_dotenv()

FEEDBACK_GUILD_ID = int(os.getenv("FEEDBACK_GUILD_ID", 0))
FEEDBACK_CHANNEL_ID = int(os.getenv("FEEDBACK_CHANNEL_ID", 0))


class Misc(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def cog_command_error(self, ctx, error):
        from utils.errors import WrongChannel

        # Skip feedback (already handled in its own error handler)
        if ctx.command and ctx.command.name == "feedback":
            return

        # Ignore wrong channel or DM errors silently
        if isinstance(error, WrongChannel):
            return

        await log_error(self.__class__.__name__, error, ctx)
        await ctx.send("An internal error occurred. The issue has been logged.")

    @commands.command(name="info")
    async def help_command(self, ctx):
        """Show a list of all available commands, grouped by category."""
        general_cmds = []
        p2p_cmds = []
        admin_cmds = []

        for command in self.bot.commands:
            if command.hidden:
                continue

            # --- Detect Admin Commands ---
            is_admin = False

            for check in command.checks:
                try:
                    if hasattr(check, "__closure__") and check.__closure__:
                        for cell in check.__closure__:
                            if cell and hasattr(cell, "cell_contents"):
                                val = cell.cell_contents
                                if isinstance(val, dict) and val.get("administrator"):
                                    is_admin = True
                                    break
                except Exception:
                    pass
                if is_admin:
                    break

            if "ADMIN" in (command.help or "").upper():
                is_admin = True

            # --- Categorize Commands ---
            if is_admin or command.name.lower().startswith(
                    ("admin", "reset", "remove", "delete", "gift", "set_setting", "list_settings")):
                admin_cmds.append(command)
            elif command.cog_name and command.cog_name.lower() in ["playertrading", "trading_p2p"]:
                p2p_cmds.append(command)
            elif command.name.lower() in ["start_trade", "trade_start", "trade", "accept", "deny"]:
                p2p_cmds.append(command)
            else:
                general_cmds.append(command)

        # --- Helper to Send Embed Groups ---
        async def send_group(title, description, commands_list, color):
            for i in range(0, len(commands_list), 25):
                embed = discord.Embed(
                    title=title if i == 0 else None,
                    description=description if i == 0 else None,
                    color=color
                )
                for command in commands_list[i:i + 25]:
                    embed.add_field(
                        name=f"!{command.name}",
                        value=command.help or "No description provided.",
                        inline=False
                    )
                await ctx.send(embed=embed)

        # --- Send in Order ---
        if admin_cmds:
            await send_group(
                "Admin Commands",
                "Commands restricted to admins for managing users, stocks, and game settings.",
                admin_cmds,
                discord.Color.red()
            )

        if p2p_cmds:
            await send_group(
                "Person-to-Person Trading Commands",
                "Commands used for direct trades between users in #toilet-exchange.",
                p2p_cmds,
                discord.Color.orange()
            )

        if general_cmds:
            await send_group(
                "General Commands",
                "Common player commands used for trading, checking portfolios, prices, and general info.",
                general_cmds,
                discord.Color.blue()
            )


        # --- Add final section with website ---
        footer_embed = discord.Embed(
            title="More Information",
            description=(
                "You can also visit the website for full guides, glossary, and FAQs*:\n"
                "<https://toilet-exchange.neocities.org/>"
            ),
            color=discord.Color.blurple()
        )
        footer_embed.set_footer(text="The Toilet Exchange")
        await ctx.send(embed=footer_embed)

    # -------------------------------
    # FEEDBACK COMMAND (DM ONLY)
    # -------------------------------
    @commands.command(name="feedback")
    @commands.cooldown(1, 300, commands.BucketType.user)  # once per 5 minutes
    async def feedback(self, ctx, *, message: str = None):
        """Submit feedback or feature ideas to the developers.
        This is the ONLY command usable in DMs!
        """
        # Only allow in DMs
        if not isinstance(ctx.channel, discord.DMChannel):
            return await ctx.send(
                "Please DM me directly to use this command.\n"
                "Example: **Send me a private message** and type:\n"
                "`!feedback I think there should be daily market updates!`"
            )

        if not message:
            return await ctx.send(
                "❌ Please include your feedback message.\n"
                "Example: `!feedback add risk tracking to market!`"
            )

        if not FEEDBACK_GUILD_ID or not FEEDBACK_CHANNEL_ID:
            return await ctx.send("Feedback system not configured. Please notify the bot owner.")

        try:
            # --- Your designated server + channel IDs ---
            guild = self.bot.get_guild(FEEDBACK_GUILD_ID)
            if guild is None:
                raise RuntimeError(f"Feedback guild {FEEDBACK_GUILD_ID} not found or bot not in guild.")

            feedback_channel = guild.get_channel(FEEDBACK_CHANNEL_ID)
            if feedback_channel is None:
                raise RuntimeError(f"Feedback channel {FEEDBACK_CHANNEL_ID} not found in guild {guild.name}.")

            # --- Build and send embed ---
            embed = discord.Embed(
                title="New Feedback Received",
                description=message,
                color=discord.Color.blurple()
            )
            embed.set_footer(text=f"From {ctx.author} ({ctx.author.id})")

            await feedback_channel.send(embed=embed)
            await ctx.send("✅ Thank you! Your feedback has been sent to the dev team.")
            #await log_error("INFO", f"Feedback received from {ctx.author}: {message}")

        except Exception as e:
            # Log what actually failed — guild, channel, or permissions
            print(f"[FEEDBACK DEBUG] {type(e).__name__}: {e}")
            await log_error("Feedback command failed", e, ctx)
            await ctx.send("⚠️ Something went wrong sending your feedback. The issue has been logged.")

    @feedback.error
    async def feedback_error(self, ctx, error):
        """Handle cooldowns or expected errors gracefully for !feedback."""
        from utils.errors import WrongChannel

        # Ignore channel restriction errors silently
        if isinstance(error, WrongChannel):
            return

        # Handle rate limits cleanly
        if isinstance(error, commands.CommandOnCooldown):
            return await ctx.send(f"⏳ Please wait {error.retry_after:.0f}s before sending feedback again.")

        # Log only genuine exceptions
        await log_error("Feedback command failed", error, ctx)



async def setup(bot):
    await bot.add_cog(Misc(bot))