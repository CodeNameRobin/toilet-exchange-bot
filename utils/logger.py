# utils/logger.py
import errno
import os
import glob
import aiofiles
import datetime
import traceback
from datetime import datetime, UTC

LOG_DIR = "logs"
LOG_PREFIX = "error_log_"
LOG_EXT = ".txt"

def _week_key(dt: datetime):
    iso = dt.isocalendar()
    return iso.year, iso.week

def _current_log_path():
    now = datetime.now(UTC)  # use timezone-aware UTC datetime
    year, week = _week_key(now)
    filename = f"{LOG_PREFIX}{year}-W{week:02d}{LOG_EXT}"
    os.makedirs(LOG_DIR, exist_ok=True)
    return os.path.join(LOG_DIR, filename)

def _list_log_files():
    return sorted(glob.glob(os.path.join(LOG_DIR, f"{LOG_PREFIX}*{LOG_EXT}")))

def _prune_old_logs(keep: int = 2):
    files = _list_log_files()
    if len(files) > keep:
        for old in files[:-keep]:
            try:
                os.remove(old)
            except OSError as e:
                if e.errno not in (errno.ENOENT, errno.EPERM):
                    raise
                else:
                    pass

async def log_error(source: str, error: Exception, ctx=None):
    """
    Log errors or exceptions (no info logs).
    """
    timestamp = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")
    ctx_info = ""
    if ctx:
        user = getattr(ctx, "author", None)
        guild = getattr(ctx, "guild", None)
        ctx_info = f" | User: {user} ({getattr(user, 'id', 'N/A')}) | Guild: {getattr(guild, 'name', 'DM')}"
    entry = (
        f"[{timestamp}] [ERROR] [{source}]{ctx_info}\n"
        f"{error}\n"
        f"{''.join(traceback.format_exception(type(error), error, error.__traceback__))}\n"
    )

    path = _current_log_path()
    async with aiofiles.open(path, "a", encoding="utf-8") as f:
        await f.write(entry)

    _prune_old_logs(keep=2)