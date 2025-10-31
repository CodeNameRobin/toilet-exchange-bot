# cogs/trading_p2p.py
import discord
from discord.ext import commands
import aiosqlite
from utils.database import DB_PATH, get_user, update_balance, record_trade
from utils.helpers import resolve_member
from utils.logger import log_error

# Active trades tracked per guild to prevent cross-server mixups
# Structure: {guild_id: {initiator_id: trade_data}}
active_trades = {}


class PlayerTrading(commands.Cog):
    """Person-to-person trading system (guild-specific)."""

    def __init__(self, bot):
        self.bot = bot

    # --------------------------
    # Error handling
    # --------------------------
    async def cog_command_error(self, ctx, error):
        from utils.errors import WrongChannel
        if isinstance(error, WrongChannel):
            return
        await log_error(self.__class__.__name__, error, ctx)
        await ctx.send("⚠️ An internal error occurred. The issue has been logged.")

    # --------------------------
    # Channel check
    # --------------------------
    async def _check_channel(self, ctx):
        return ctx.channel.name == "toilet-exchange"

    # --------------------------
    # start_trade / cancel
    # --------------------------
    @commands.command(name="start_trade")
    async def start_trade(self, ctx, target: str = None):
        """Start a trade or cancel one."""
        if not await self._check_channel(ctx):
            return await ctx.send("❌ Trades must happen in #toilet-exchange")

        guild_id = ctx.guild.id
        user_id = ctx.author.id
        guild_trades = active_trades.setdefault(guild_id, {})

        # Cancel trade
        if target and target.lower() == "cancel":
            if user_id not in guild_trades:
                return await ctx.send("❌ You have no active trade to cancel.")
            trade = guild_trades.pop(user_id)
            partner_id = trade.get("partner_id")
            if partner_id:
                guild_trades.pop(partner_id, None)
            msg = trade.get("message")
            if msg:
                await msg.edit(embed=self._trade_embed("❌ Trade cancelled.", color=discord.Color.red()))
            return await ctx.send("🟥 Trade cancelled.")

        # Prevent duplicate
        if user_id in guild_trades:
            return await ctx.send("⚠️ You already have an active trade.")

        # Resolve target
        partner = None
        if target and target.lower() not in ("all", "cancel"):
            partner = await resolve_member(self.bot, ctx, target)
            if not partner:
                return await ctx.send(f"❌ Could not find user `{target}` in this server.")
            if partner.id == user_id:
                return await ctx.send("❌ You can’t trade with yourself.")

        # Create new trade
        trade_data = {
            "partner_id": partner.id if partner else None,
            "mode": "targeted" if partner else "open",
            "status": "pending",
            "initiator_offer": {"cash": 0, "stocks": {}},
            "partner_offer": {"cash": 0, "stocks": {}},
            "message": None,
        }

        embed = self._trade_embed(
            f"🟢 Trade started by **{ctx.author.display_name}**\n"
            f"{'Waiting for ' + partner.display_name if partner else 'Open to anyone — type `!trade_accept` to join.'}\n\n"
            "Once both users have joined:\n"
            "• `!trade 100` to offer cash\n"
            "• `!trade TICKER #` to offer stocks\n"
            "• `!accept` to finalize or `!deny` to cancel."
        )
        msg = await ctx.send(embed=embed)
        trade_data["message"] = msg
        guild_trades[user_id] = trade_data
        await ctx.send("✅ Trade created!")

    # --------------------------
    # Accept trade
    # --------------------------
    @commands.command(name="trade_accept")
    async def trade_accept(self, ctx):
        """Join someone’s open or targeted trade."""
        if not await self._check_channel(ctx):
            return await ctx.send("❌ Trades must happen in #toilet-exchange")

        guild_id = ctx.guild.id
        user_id = ctx.author.id
        guild_trades = active_trades.get(guild_id, {})

        # Find a pending trade for this guild
        target_trade = None
        for uid, trade in guild_trades.items():
            if trade["status"] == "pending":
                if trade["mode"] == "open" and trade["partner_id"] is None:
                    target_trade = uid
                    break
                elif trade["mode"] == "targeted" and trade["partner_id"] == user_id:
                    target_trade = uid
                    break

        if not target_trade:
            return await ctx.send("❌ No open trade found for you to join.")

        initiator_id = target_trade
        trade = guild_trades[initiator_id]
        if initiator_id == user_id:
            return await ctx.send("❌ You can’t accept your own trade.")

        trade["partner_id"] = user_id
        trade["status"] = "active"
        guild_trades[user_id] = trade

        initiator = ctx.guild.get_member(initiator_id)
        partner = ctx.author
        embed = self._trade_embed(f"🤝 Trade started between {initiator.display_name} and {partner.display_name}")
        await trade["message"].edit(embed=embed)
        await ctx.send("✅ Trade started!")

    # --------------------------
    # Offer
    # --------------------------
    @commands.command(name="trade")
    async def trade(self, ctx, *args):
        """Offer money or stocks in an active trade."""
        if not await self._check_channel(ctx):
            return await ctx.send("❌ Trades must happen in #toilet-exchange")

        guild_id = ctx.guild.id
        user_id = ctx.author.id
        guild_trades = active_trades.get(guild_id, {})

        if user_id not in guild_trades:
            return await ctx.send("❌ You’re not in a trade.")
        trade = guild_trades[user_id]

        initiator_id = [k for k, v in guild_trades.items() if v == trade][0]
        trade_key = "initiator_offer" if initiator_id == user_id else "partner_offer"

        if len(args) == 1 and args[0].isdigit():
            cash = int(args[0])
            trade[trade_key]["cash"] = cash
            msg = f"💵 {ctx.author.display_name} now offers ${cash}."
        elif len(args) == 2:
            ticker, qty_str = args
            if not qty_str.isdigit():
                return await ctx.send("❌ Quantity must be a number.")
            trade[trade_key]["stocks"][ticker.upper()] = int(qty_str)
            msg = f"📊 {ctx.author.display_name} now offers {qty_str} × {ticker.upper()}."
        else:
            return await ctx.send("❌ Usage: `!trade 100` or `!trade GMD 2`")

        embed = await self._build_trade_embed(ctx.guild, guild_trades, trade)
        await trade["message"].edit(embed=embed)
        await ctx.send(msg)

    # --------------------------
    # Accept / Deny
    # --------------------------
    @commands.command(name="accept")
    async def accept(self, ctx):
        """Accept a trade once both offers are ready."""
        if not await self._check_channel(ctx):
            return await ctx.send("❌ Trades must happen in #toilet-exchange")

        guild_id = ctx.guild.id
        user_id = ctx.author.id
        guild_trades = active_trades.get(guild_id, {})
        if user_id not in guild_trades:
            return await ctx.send("❌ You’re not in a trade.")

        trade = guild_trades[user_id]
        trade.setdefault("accepts", set()).add(user_id)
        partner_id = trade["partner_id"]

        if len(trade["accepts"]) == 2:
            await self._finalize_trade(ctx.guild, trade)
            for uid in [user_id, partner_id]:
                guild_trades.pop(uid, None)
        else:
            embed = self._trade_embed(f"✅ {ctx.author.display_name} accepted the trade.\nWaiting for the other party...")
            await trade["message"].edit(embed=embed)

    @commands.command(name="deny")
    async def deny(self, ctx):
        """Deny or cancel a trade."""
        if not await self._check_channel(ctx):
            return await ctx.send("❌ Trades must happen in #toilet-exchange")

        guild_id = ctx.guild.id
        user_id = ctx.author.id
        guild_trades = active_trades.get(guild_id, {})

        if user_id not in guild_trades:
            return await ctx.send("❌ You’re not in a trade.")
        trade = guild_trades.pop(user_id)
        partner_id = trade.get("partner_id")
        if partner_id:
            guild_trades.pop(partner_id, None)

        embed = self._trade_embed("❌ Trade denied.", color=discord.Color.red())
        await trade["message"].edit(embed=embed)
        await ctx.send("Trade cancelled.")

    # --------------------------
    # Helpers
    # --------------------------
    def _trade_embed(self, text, color=discord.Color.blurple()):
        return discord.Embed(description=text, color=color)

    async def _build_trade_embed(self, guild, guild_trades, trade):
        initiator_id = [k for k, v in guild_trades.items() if v == trade][0]
        initiator = guild.get_member(initiator_id)
        partner = guild.get_member(trade["partner_id"])
        io, po = trade["initiator_offer"], trade["partner_offer"]

        embed = discord.Embed(title="💱 Active Trade", color=discord.Color.gold())
        embed.add_field(name=f"{initiator.display_name}'s Offer", value=self._format_offer(io), inline=True)
        embed.add_field(name=f"{partner.display_name}'s Offer", value=self._format_offer(po), inline=True)
        embed.set_footer(text="Use !accept or !deny to finish.")
        return embed

    def _format_offer(self, offer):
        items = []
        if offer["cash"]:
            items.append(f"${offer['cash']:,}")
        for t, q in offer["stocks"].items():
            items.append(f"{q} × {t}")
        return "\n".join(items) if items else "Nothing"

    async def _finalize_trade(self, guild, trade):
        """Finalize trade safely per guild."""
        initiator = guild.get_member([k for k, v in active_trades[guild.id].items() if v == trade][0])
        partner = guild.get_member(trade["partner_id"])
        if not initiator or not partner:
            return

        io, po = trade["initiator_offer"], trade["partner_offer"]
        guild_id = guild.id

        # Validate cash and stock ownership
        async def owns_stock(uid, t, q):
            async with aiosqlite.connect(DB_PATH) as db:
                cur = await db.execute("""
                    SELECT SUM(CASE WHEN side='BUY' THEN qty ELSE -qty END)
                    FROM trades
                    WHERE user_id=? AND guild_id=? AND ticker=?
                """, (str(uid), str(guild_id), t))
                row = await cur.fetchone()
                return (row[0] or 0) >= q

        for ticker, qty in io["stocks"].items():
            if not await owns_stock(initiator.id, ticker, qty):
                return await self._trade_fail(trade, f"{initiator.display_name} doesn’t own {qty}×{ticker}.")
        for ticker, qty in po["stocks"].items():
            if not await owns_stock(partner.id, ticker, qty):
                return await self._trade_fail(trade, f"{partner.display_name} doesn’t own {qty}×{ticker}.")

        # Handle cash
        initiator_user = await get_user(initiator.id, guild_id)
        partner_user = await get_user(partner.id, guild_id)
        if not initiator_user or not partner_user:
            return await self._trade_fail(trade, "One or both traders are not registered.")

        if io["cash"] > initiator_user[1]:
            return await self._trade_fail(trade, f"{initiator.display_name} lacks funds.")
        if po["cash"] > partner_user[1]:
            return await self._trade_fail(trade, f"{partner.display_name} lacks funds.")

        # Exchange cash
        net_cash = io["cash"] - po["cash"]
        if net_cash != 0:
            await update_balance(initiator.id, guild_id, -net_cash)
            await update_balance(partner.id, guild_id, net_cash)

        # Exchange stocks
        async with aiosqlite.connect(DB_PATH) as db:
            for ticker, qty in io["stocks"].items():
                if qty > 0:
                    await record_trade(initiator.id, guild_id, ticker, qty, "SELL")
                    await record_trade(partner.id, guild_id, ticker, qty, "BUY")
            for ticker, qty in po["stocks"].items():
                if qty > 0:
                    await record_trade(partner.id, guild_id, ticker, qty, "SELL")
                    await record_trade(initiator.id, guild_id, ticker, qty, "BUY")

        # Update message
        summary = []
        if io["cash"]:
            summary.append(f"💵 {initiator.display_name} sent ${io['cash']:,}")
        if po["cash"]:
            summary.append(f"💵 {partner.display_name} sent ${po['cash']:,}")
        for t, q in io["stocks"].items():
            summary.append(f"📈 {initiator.display_name} gave {q} × {t}")
        for t, q in po["stocks"].items():
            summary.append(f"📈 {partner.display_name} gave {q} × {t}")

        embed = discord.Embed(
            title="✅ Trade Complete!",
            description=f"{initiator.display_name} and {partner.display_name} successfully completed a trade!",
            color=discord.Color.green()
        )
        embed.add_field(name="Trade Summary", value="\n".join(summary) or "No items exchanged", inline=False)
        await trade["message"].edit(embed=embed)

        channel = discord.utils.get(guild.text_channels, name="toilet-exchange")
        if channel:
            await channel.send(
                f"💹 **Trade Completed!** {initiator.mention} and {partner.mention} have finalized their trade!"
            )

        # Cleanup
        active_trades[guild_id].pop(initiator.id, None)
        active_trades[guild_id].pop(partner.id, None)

    async def _trade_fail(self, trade, reason):
        embed = discord.Embed(title="❌ Trade Failed", description=reason, color=discord.Color.red())
        await trade["message"].edit(embed=embed)


async def setup(bot):
    await bot.add_cog(PlayerTrading(bot))