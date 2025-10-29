from discord.ext import commands

class WrongChannel(commands.CheckFailure):
    """Raised when a command is used in the wrong channel."""
    pass