import discord
from discord.ext import commands

async def dm_and_delete(ctx, message: str = None, embed=None):
    """DMs the user (message, embed, or both) and deletes their original command message."""
    try:
        if embed and message:
            await ctx.author.send(content=message, embed=embed)
        elif embed:
            await ctx.author.send(embed=embed)
        elif message:
            await ctx.author.send(message)
        else:
            # nothing to send — silently ignore
            return
    except discord.Forbidden:
        await ctx.send(f"{ctx.author.mention}, I couldn’t DM you. Please enable DMs.", delete_after=5)

    try:
        await ctx.message.delete()
    except discord.Forbidden:
        pass

async def resolve_member(bot, ctx, user_text: str):
    """Resolve a member by mention, ID, exact or partial name. Requires members intent."""
    # 1) Mention wins
    if ctx.message.mentions:
        return ctx.message.mentions[0]

    # 2) ID
    txt = user_text.strip().lstrip("<@!>").rstrip(">")
    if txt.isdigit():
        try:
            return await ctx.guild.fetch_member(int(txt))
        except Exception:
            pass

    # 3) Exact display_name or name (case-insensitive) from cache
    lower = user_text.lower()
    exact = discord.utils.find(
        lambda m: m.display_name.lower() == lower or m.name.lower() == lower,
        ctx.guild.members
    )
    if exact:
        return exact

    # 4) Partial match (case-insensitive)
    partials = [m for m in ctx.guild.members
                if lower in m.display_name.lower() or lower in m.name.lower()]
    if len(partials) == 1:
        return partials[0]
    if len(partials) > 1:
        # too many matches—ask to be specific
        names = ", ".join(m.display_name for m in partials[:5])
        await dm_and_delete(ctx, f"⚠️ Multiple users match `{user_text}`: {names}\nPlease be more specific.")
        return None

    # 5) As a last resort, fetch the full member list (requires Members Intent)
    try:
        async for m in ctx.guild.fetch_members(limit=None):
            if m.display_name.lower() == lower or m.name.lower() == lower:
                return m
        async for m in ctx.guild.fetch_members(limit=None):
            if lower in m.display_name.lower() or lower in m.name.lower():
                return m
    except Exception:
        pass

    return None

async def is_bot_admin(ctx):
    """Allow full admins or users with bot management permissions."""
    perms = ctx.author.guild_permissions
    return perms.administrator or perms.manage_guild or perms.manage_webhooks

def get_price_change_range(risk: str) -> tuple[float, float]:
    """Return (min, max) price movement multiplier based on risk level."""
    risk = (risk or "moderate").lower()
    if risk == "low":
        return (-0.02, 0.02)   # ±2%
    elif risk == "moderate":
        return (-0.05, 0.05)   # ±5%
    elif risk == "high":
        return (-0.15, 0.15)   # ±15%
    return (-0.05, 0.05)

def bot_admin():
    """Decorator for commands restricted to bot admins."""
    return commands.check(is_bot_admin)