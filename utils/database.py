# database.py
import aiosqlite
import os

DB_PATH = "data/market.db"

# Ensure the folder exists
os.makedirs("data", exist_ok=True)

# Default stocks that will be added to each new guild
DEFAULT_STOCKS = [
    ("GMD", "GOMADINC", 200.28, "moderate"),
    ("BTH", "Gamer Goddess Bathwater", 150.00, "moderate"),
    ("JFP", "JUST POSTS, Fences & Posts", 0.28, "low"),
    ("BPT", "Blood Potions", 700.00, "high"),
]


async def connect():
    return await aiosqlite.connect(DB_PATH)


async def init_db():
    """Initialize all database tables."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
        CREATE TABLE IF NOT EXISTS stocks (
            ticker TEXT,
            name TEXT,
            price REAL,
            risk TEXT DEFAULT 'moderate' CHECK(risk IN ('low', 'moderate', 'high')),
            guild_id TEXT,
            PRIMARY KEY (ticker, guild_id)
        );
        """)

        await db.execute("""
        CREATE TABLE IF NOT EXISTS price_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker TEXT,
            guild_id TEXT,
            price REAL,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        );
        """)

        await db.execute("""
        CREATE TABLE IF NOT EXISTS admins (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id TEXT NOT NULL,
            discord_id TEXT NOT NULL,
            added_by TEXT,
            added_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(guild_id, discord_id)
        );
        """)

        await db.execute("""
        CREATE TABLE IF NOT EXISTS leaderboard_cache (
            guild_id TEXT,
            user_id TEXT,
            total_value REAL,
            last_updated DATETIME DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (guild_id, user_id)
        );
        """)

        await db.execute("""
        CREATE TABLE IF NOT EXISTS portfolios (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            guild_id TEXT NOT NULL,
            ticker TEXT NOT NULL,
            qty INTEGER NOT NULL,
            avg_price REAL NOT NULL
        );
        """)

        await db.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            discord_id TEXT,
            guild_id TEXT,
            cash REAL DEFAULT 1000,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(discord_id, guild_id)
        );
        """)

        await db.execute("""
        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            guild_id TEXT NOT NULL,
            ticker TEXT NOT NULL,
            qty INTEGER NOT NULL,
            side TEXT NOT NULL CHECK(side IN ('BUY', 'SELL')),
            price REAL DEFAULT 0,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        );
        """)

        await db.execute("""
        CREATE TABLE IF NOT EXISTS market_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id TEXT,
            event_type TEXT,
            year INTEGER,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        );
        """)

        await db.execute("""
        CREATE TABLE IF NOT EXISTS server_settings (
            guild_id TEXT PRIMARY KEY,
            leaderboard_post_time TEXT DEFAULT '23:00',  -- UTC
            leaderboard_update_rate INTEGER DEFAULT 10,  -- minutes
            market_update_rate INTEGER DEFAULT 1,        -- minutes
            starting_money REAL DEFAULT 1000.0,
            secret_profiles INTEGER DEFAULT 1,  -- 1 = on (DM + delete), 0 = off (public)
            market_bias REAL DEFAULT 0.0008,
            target_price REAL DEFAULT 100.0
        );
        """)

        await db.commit()


# --------------------------
# Guild stock initialization
# --------------------------
async def ensure_guild_market(guild_id):
    """Ensure the guild has default stock data."""
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT COUNT(*) FROM stocks WHERE guild_id=?", (str(guild_id),))
        count = (await cur.fetchone())[0]

        if count == 0:
            await db.executemany(
                "INSERT INTO stocks(ticker, name, price, risk, guild_id) VALUES (?, ?, ?, ?, ?)",
                [(t, n, p, r, str(guild_id)) for (t, n, p, r) in DEFAULT_STOCKS]
            )
            await db.commit()
            print(f"✅ Inserted default stock data for guild {guild_id}")
        else:
            print(f"ℹ️ Skipped defaults; {count} stocks already exist for guild {guild_id}")


# --------------------------
# User management
# --------------------------
async def get_user(discord_id, guild_id):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT discord_id, cash FROM users WHERE discord_id=? AND guild_id=?",
            (str(discord_id), str(guild_id))
        )
        row = await cur.fetchone()
        if row:
            return (row[0], float(row[1]))
        return None


async def create_user(discord_id, guild_id):
    from utils.database import get_server_settings
    settings = await get_server_settings(guild_id)
    starting_money = settings.get("starting_money", 1000.0)

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR IGNORE INTO users(discord_id, guild_id, cash) VALUES(?, ?, ?)",
            (str(discord_id), str(guild_id), float(starting_money))
        )
        await db.commit()


async def update_balance(discord_id, guild_id, delta):
    # Ensure SQLite gets a float, not Decimal
    if not isinstance(delta, (int, float)):
        delta = float(delta)

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE users SET cash = cash + ? WHERE discord_id=? AND guild_id=?",
            (delta, str(discord_id), str(guild_id))
        )
        await db.commit()


# --------------------------
# Trading and stocks
# --------------------------
async def record_trade(user_id, guild_id, ticker, qty, side):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO trades(user_id, guild_id, ticker, qty, side) VALUES(?, ?, ?, ?, ?)",
            (str(user_id), str(guild_id), ticker.upper(), qty, side.upper())
        )
        await db.commit()
    return f"Recorded {side.upper()} trade for {qty} {ticker.upper()}."


async def get_stock_price(symbol, guild_id):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT price FROM stocks WHERE (ticker=? OR name=?) AND guild_id=?",
            (symbol.upper(), symbol.title(), str(guild_id))
        )
        row = await cur.fetchone()
        return row[0] if row else None


async def update_stock_price(ticker, price, guild_id):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE stocks SET price=? WHERE ticker=? AND guild_id=?",
            (price, ticker.upper(), str(guild_id))
        )
        await db.commit()


async def get_moving_average(ticker, guild_id, window=5):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT price FROM price_history WHERE ticker=? AND guild_id=? ORDER BY id DESC LIMIT ?",
            (ticker.upper(), str(guild_id), window)
        )
        prices = [row[0] for row in await cur.fetchall()]
        if not prices:
            return None
        return sum(prices) / len(prices)

# --------------------------
# Admin management (server-specific)
# --------------------------
async def is_admin(discord_id, guild_id):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT 1 FROM admins WHERE discord_id=? AND guild_id=?",
            (str(discord_id), str(guild_id)),
        )
        return bool(await cur.fetchone())

async def add_admin(discord_id, guild_id, added_by=None):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR IGNORE INTO admins (discord_id, guild_id, added_by) VALUES (?, ?, ?)",
            (str(discord_id), str(guild_id), str(added_by) if added_by else None),
        )
        await db.commit()

async def remove_admin(discord_id, guild_id):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "DELETE FROM admins WHERE discord_id=? AND guild_id=?",
            (str(discord_id), str(guild_id)),
        )
        await db.commit()

async def list_admins(guild_id):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT discord_id, added_at FROM admins WHERE guild_id=?",
            (str(guild_id),),
        )
        return await cur.fetchall()


# --------------------------
# Leaderboards
# --------------------------
async def get_leaderboard(guild_id, limit=10):
    async with aiosqlite.connect(DB_PATH) as db:
        query = """
        SELECT u.discord_id,
               u.cash + IFNULL(SUM(p.qty * s.price), 0) AS total_value
        FROM users u
        LEFT JOIN portfolios p ON u.id = p.user_id AND p.guild_id = u.guild_id
        LEFT JOIN stocks s ON p.ticker = s.ticker AND s.guild_id = u.guild_id
        WHERE u.guild_id=?
        GROUP BY u.id
        ORDER BY total_value DESC
        LIMIT ?;
        """
        cur = await db.execute(query, (str(guild_id), limit))
        rows = await cur.fetchall()
    return rows

async def update_leaderboard_cache(guild_id):
    """Rebuild leaderboard cache for a specific guild."""
    async with aiosqlite.connect(DB_PATH) as db:
        # Clear old entries for this guild
        await db.execute("DELETE FROM leaderboard_cache WHERE guild_id=?", (str(guild_id),))

        # Recompute leaderboard values
        await db.execute("""
        INSERT INTO leaderboard_cache (guild_id, user_id, total_value)
        SELECT
            u.guild_id,
            u.discord_id,
            u.cash + IFNULL(SUM(
                (CASE WHEN t.side='BUY' THEN t.qty ELSE -t.qty END) * s.price
            ), 0) AS total_value
        FROM users u
        LEFT JOIN trades t ON u.discord_id = t.user_id AND u.guild_id = t.guild_id
        LEFT JOIN stocks s ON t.ticker = s.ticker AND s.guild_id = u.guild_id
        WHERE u.guild_id=?
        GROUP BY u.discord_id;
        """, (str(guild_id),))

        await db.commit()

async def get_cached_leaderboard(guild_id, limit=10):
    """Retrieve cached leaderboard entries for a guild."""
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("""
            SELECT user_id, total_value, last_updated
            FROM leaderboard_cache
            WHERE guild_id=?
            ORDER BY total_value DESC
            LIMIT ?;
        """, (str(guild_id), limit))
        return await cur.fetchall()


# --------------------------
# Server settings
# --------------------------
async def get_server_settings(guild_id):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT * FROM server_settings WHERE guild_id=?", (str(guild_id),))
        row = await cur.fetchone()
        if not row:
            await db.execute("INSERT INTO server_settings(guild_id) VALUES(?)", (str(guild_id),))
            await db.commit()
            cur = await db.execute("SELECT * FROM server_settings WHERE guild_id=?", (str(guild_id),))
            row = await cur.fetchone()
        columns = [col[0] for col in cur.description]
        return dict(zip(columns, row))


async def update_server_setting(guild_id, setting, value):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            f"UPDATE server_settings SET {setting}=? WHERE guild_id=?",
            (value, str(guild_id))
        )
        await db.commit()


