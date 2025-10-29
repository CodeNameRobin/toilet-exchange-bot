# cogs/trading_p2p.py
import discord
from discord.ext import commands
import aiosqlite
from utils.database import DB_PATH, get_stock_price, get_user, update_balance, record_trade
from utils.helpers import resolve_member
from utils.logger import log_error

# In-memory trades: {initiator_id: {...data...}}
active_trades = {}

class PlayerTrading(commands.Cog):
    """Public person-to-person trading system in #toilet-exchange."""

    def __init__(self, bot):
        self.bot = bot

    async def _check_channel(self, ctx):
        return ctx.channel.name == "toilet-exchange"

    async def cog_command_error(self, ctx, error):
        from utils.errors import WrongChannel

        # Ignore wrong channel or DM errors silently
        if isinstance(error, WrongChannel):
            return

        # Log everything else
        await log_error(self.__class__.__name__, error, ctx)
        await ctx.send("An internal error occurred. The issue has been logged.")

    # --------------------------
    # start_trade / cancel
    # --------------------------
    @commands.command(name="start_trade")
    async def start_trade(self, ctx, target: str = None):
        """Start a trade or cancel one.
        - !start_trade all      → open to anyone
        - !start_trade @user    → targeted
        - !start_trade username → targeted (partial or full)
        - !start_trade cancel   → cancel your trade
        """
        if not await self._check_channel(ctx):
            return await ctx.send("❌ Trades must happen in #toilet-exchange")

        user_id = ctx.author.id

        # Cancel trade
        if target and target.lower() == "cancel":
            if user_id not in active_trades:
                return await ctx.send("❌ You have no active trade to cancel.")
            partner_id = active_trades[user_id].get("partner_id")
            msg = active_trades[user_id].get("message")
            if msg:
                await msg.edit(embed=self._trade_embed("❌ Trade cancelled.", color=discord.Color.red()))
            for uid in [user_id, partner_id]:
                active_trades.pop(uid, None)
            return await ctx.send("🟥 Trade cancelled.")

        # Already trading
        if user_id in active_trades:
            return await ctx.send("⚠️ You already have an active trade.")

        partner = None
        # Resolve the target (mention or name)
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

        # Send message
        embed = self._trade_embed(
            f"🟢 Trade started by **{ctx.author.display_name}**\n"
            f"{'Waiting for ' + partner.display_name if partner else 'Open to anyone — type `!trade_accept` to accept this trade.'}\n\n"
            "Once both users have joined:\n"
            "• Use `!trade 100` to offer cash\n"
            "• Use `!trade TICKER #` to offer stocks\n"
            "• When both are satisfied, use `!accept` or use '!deny'\n"
            "• To cancel, use `!start_trade cancel`"
        )
        msg = await ctx.send(embed=embed)
        trade_data["message"] = msg
        active_trades[user_id] = trade_data
        await ctx.send("✅ Trade created!")

    # --------------------------
    # trade_start (accept)
    # --------------------------
    @commands.command(name="trade_accept")
    async def trade_start(self, ctx):
        """Join someone’s open or targeted trade."""
        if not await self._check_channel(ctx):
            return await ctx.send("❌ Trades must happen in #toilet-exchange")

        user_id = ctx.author.id
        target_trade = None

        for uid, trade in active_trades.items():
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
        trade = active_trades[initiator_id]
        if initiator_id == user_id:
            return await ctx.send("❌ You can’t accept your own trade.")

        trade["partner_id"] = user_id
        trade["status"] = "active"
        active_trades[user_id] = trade

        initiator = ctx.guild.get_member(initiator_id)
        partner = ctx.author
        embed = self._trade_embed(f"🤝 Trade started between {initiator.display_name} and {partner.display_name}")
        await trade["message"].edit(embed=embed)
        await ctx.send("✅ Trade started!")

    # --------------------------
    # trade (offer)
    # --------------------------
    @commands.command(name="trade")
    async def trade(self, ctx, *args):
        """Offer something:
        - !trade 100 → offer $100
        - !trade GMD 2 → offer 2 GMD stocks
        """
        if not await self._check_channel(ctx):
            return await ctx.send("❌ Trades must happen in #toilet-exchange")

        user_id = ctx.author.id
        if user_id not in active_trades:
            return await ctx.send("❌ You’re not in a trade.")

        trade = active_trades[user_id]
        partner_id = trade["partner_id"]
        if not partner_id:
            return await ctx.send("⚠️ No partner yet. Wait for someone to join.")

        # Figure out which side this user is
        trade_key = "initiator_offer" if list(active_trades.keys())[0] == user_id else "partner_offer"

        if len(args) == 1 and args[0].isdigit():
            cash = int(args[0])
            trade[trade_key]["cash"] = cash
            msg = f"💵 {ctx.author.display_name} now offers ${cash}."
        elif len(args) == 2:
            ticker, qty_str = args
            if not qty_str.isdigit():
                return await ctx.send("❌ Quantity must be a number.")
            qty = int(qty_str)
            trade[trade_key]["stocks"][ticker.upper()] = qty
            msg = f"📊 {ctx.author.display_name} now offers {qty} × {ticker.upper()}."
        else:
            return await ctx.send("❌ Usage: `!trade 100` or `!trade GMD 2`")

        embed = await self._build_trade_embed(ctx.guild, trade)
        await trade["message"].edit(embed=embed)
        await ctx.send(msg)

    # --------------------------
    # accept / deny
    # --------------------------
    @commands.command(name="accept")
    async def accept(self, ctx):
        """Accept current trade."""
        if not await self._check_channel(ctx):
            return await ctx.send("❌ Trades must happen in #toilet-exchange")

        user_id = ctx.author.id
        if user_id not in active_trades:
            return await ctx.send("❌ You’re not in a trade.")
        trade = active_trades[user_id]
        trade.setdefault("accepts", set()).add(user_id)
        partner_id = trade["partner_id"]

        if len(trade["accepts"]) == 2:
            await self._finalize_trade(ctx.guild, trade)
            for uid in [user_id, partner_id]:
                active_trades.pop(uid, None)
        else:
            embed = self._trade_embed(f"✅ {ctx.author.display_name} accepted the trade.\nWaiting for other party...")
            await trade["message"].edit(embed=embed)

    @commands.command(name="deny")
    async def deny(self, ctx):
        """Deny trade."""
        if not await self._check_channel(ctx):
            return await ctx.send("❌ Trades must happen in #toilet-exchange")

        user_id = ctx.author.id
        if user_id not in active_trades:
            return await ctx.send("❌ You’re not in a trade.")
        trade = active_trades[user_id]
        partner_id = trade.get("partner_id")
        embed = self._trade_embed("❌ Trade denied.", color=discord.Color.red())
        await trade["message"].edit(embed=embed)
        for uid in [user_id, partner_id]:
            active_trades.pop(uid, None)
        await ctx.send("Trade cancelled.")

    # --------------------------
    # Helpers
    # --------------------------
    def _trade_embed(self, text, color=discord.Color.blurple()):
        return discord.Embed(description=text, color=color)

    async def _build_trade_embed(self, guild, trade):
        initiator = guild.get_member([k for k in active_trades.keys() if active_trades[k] == trade][0])
        partner = guild.get_member(trade["partner_id"])
        io = trade["initiator_offer"]
        po = trade["partner_offer"]
        embed = discord.Embed(title="💱 Active Trade", color=discord.Color.gold())
        embed.add_field(name=f"{initiator.display_name}'s Offer", value=self._format_offer(io), inline=True)
        embed.add_field(name=f"{partner.display_name}'s Offer", value=self._format_offer(po), inline=True)
        embed.set_footer(text="Use !accept or !deny to finish.")
        return embed

    def _format_offer(self, offer):
        out = []
        if offer["cash"]:
            out.append(f"${offer['cash']}")
        for t, q in offer["stocks"].items():
            out.append(f"{q} × {t}")
        return "\n".join(out) if out else "Nothing"

    async def _finalize_trade(self, guild, trade):
        """Finalize trade by verifying assets and updating both users' portfolios and balances."""
        initiator = guild.get_member([k for k in active_trades.keys() if active_trades[k] == trade][0])
        partner = guild.get_member(trade["partner_id"])

        io = trade["initiator_offer"]
        po = trade["partner_offer"]

        if not initiator or not partner:
            return

        # ----------------------------------------------------------
        # 🧾 Helper functions
        # ----------------------------------------------------------
        async def user_owns_stock(user_id, guild_id, ticker, qty):
            async with aiosqlite.connect(DB_PATH) as db:
                cur = await db.execute("""
                    SELECT SUM(CASE WHEN side='BUY' THEN qty ELSE -qty END)
                    FROM trades
                    WHERE user_id=? AND guild_id=? AND ticker=?
                """, (str(user_id), str(guild_id), ticker))
                row = await cur.fetchone()
                owned = row[0] or 0
                return owned >= qty

        async def get_cash_balance(user_id, guild_id):
            user = await get_user(user_id, guild_id)
            return float(user[1]) if user else 0.0

        # ----------------------------------------------------------
        # 🔍 Verify stock ownership
        # ----------------------------------------------------------
        for ticker, qty in io["stocks"].items():
            if qty > 0:
                owns = await user_owns_stock(initiator.id, guild.id, ticker, qty)
                if not owns:
                    await self._trade_fail(trade, f"{initiator.display_name} tried to trade {qty}×{ticker}, but doesn’t own enough.")
                    return

        for ticker, qty in po["stocks"].items():
            if qty > 0:
                owns = await user_owns_stock(partner.id, guild.id, ticker, qty)
                if not owns:
                    await self._trade_fail(trade, f"{partner.display_name} tried to trade {qty}×{ticker}, but doesn’t own enough.")
                    return

        # ----------------------------------------------------------
        # 💰 Verify cash balance
        # ----------------------------------------------------------
        initiator_balance = await get_cash_balance(initiator.id, guild.id)
        partner_balance = await get_cash_balance(partner.id, guild.id)

        if io["cash"] > initiator_balance:
            await self._trade_fail(trade, f"{initiator.display_name} doesn’t have enough cash (${initiator_balance:.2f}).")
            return

        if po["cash"] > partner_balance:
            await self._trade_fail(trade, f"{partner.display_name} doesn’t have enough cash (${partner_balance:.2f}).")
            return

        # ----------------------------------------------------------
        # 1️⃣ CASH TRANSFERS
        # ----------------------------------------------------------
        net_cash = io["cash"] - po["cash"]  # positive = initiator pays partner

        if net_cash != 0:
            await update_balance(initiator.id, guild.id, -net_cash)
            await update_balance(partner.id, guild.id, net_cash)

        # ----------------------------------------------------------
        # 2️⃣ STOCK TRANSFERS
        # ----------------------------------------------------------
        async with aiosqlite.connect(DB_PATH) as db:
            for ticker, qty in io["stocks"].items():
                if qty > 0:
                    await record_trade(initiator.id, guild.id, ticker, qty, "SELL")
                    await record_trade(partner.id, guild.id, ticker, qty, "BUY")

            for ticker, qty in po["stocks"].items():
                if qty > 0:
                    await record_trade(partner.id, guild.id, ticker, qty, "SELL")
                    await record_trade(initiator.id, guild.id, ticker, qty, "BUY")

        # ----------------------------------------------------------
        # 3️⃣ Update final embed
        # ----------------------------------------------------------
        embed = discord.Embed(
            title="✅ Trade Complete!",
            description=f"{initiator.display_name} and {partner.display_name} successfully completed a trade!",
            color=discord.Color.green()
        )

        summary = []
        if io["cash"]:
            summary.append(f"💵 {initiator.display_name} sent ${io['cash']:,}")
        if po["cash"]:
            summary.append(f"💵 {partner.display_name} sent ${po['cash']:,}")

        for ticker, qty in io["stocks"].items():
            if qty > 0:
                summary.append(f"📈 {initiator.display_name} gave {qty} × {ticker}")
        for ticker, qty in po["stocks"].items():
            if qty > 0:
                summary.append(f"📈 {partner.display_name} gave {qty} × {ticker}")

        embed.add_field(name="Trade Summary", value="\n".join(summary) or "No items exchanged", inline=False)
        await trade["message"].edit(embed=embed)

        # ----------------------------------------------------------
        # 4️⃣ Announce trade completion publicly
        # ----------------------------------------------------------
        channel = discord.utils.get(guild.text_channels, name="toilet-exchange")
        if channel:
            await channel.send(
                f"💹 **Trade Completed!** {initiator.mention} and {partner.mention} have finalized their trade!\n"
                f"✅ Check the updated trade summary above."
            )

        # ----------------------------------------------------------
        # 5️⃣ Clean up
        # ----------------------------------------------------------
        active_trades.pop(initiator.id, None)
        active_trades.pop(partner.id, None)


    async def _trade_fail(self, trade, reason):
        """Helper for when a trade fails (insufficient funds or stocks)."""
        embed = discord.Embed(
            title="❌ Trade Failed",
            description=reason,
            color=discord.Color.red()
        )
        await trade["message"].edit(embed=embed)
        initiator_id = [k for k in active_trades.keys() if active_trades[k] == trade][0]
        partner_id = trade.get("partner_id")
        active_trades.pop(initiator_id, None)
        if partner_id:
            active_trades.pop(partner_id, None)


async def setup(bot):
    await bot.add_cog(PlayerTrading(bot))