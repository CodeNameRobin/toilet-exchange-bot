# database.py
import aiosqlite
import os

DB_PATH = "data/market.db"

# Ensure the folder exists
os.makedirs("data", exist_ok=True)

DEFAULT_STOCKS = [
    ("GMD", "GOMADINC", 200.28, "moderate"),
    ("BTH", "Gamer Goddess Bathwater", 150.00, "moderate"),
    ("JFP", "JUST POSTS, Fences & Posts", 0.28, "low"),
    ("BPT", "Blood Potions", 700.00, "high"),
]

async def connect():
    return await aiosqlite.connect(DB_PATH)

async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        # Create tables
        await db.execute("""
        CREATE TABLE IF NOT EXISTS stocks (
            ticker TEXT PRIMARY KEY,
            name TEXT,
            price REAL,
            risk TEXT DEFAULT 'moderate' CHECK(risk IN ('low', 'moderate', 'high'))
        )
        """)
        await db.execute("""
        CREATE TABLE IF NOT EXISTS price_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker TEXT,
            price REAL,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS leaderboard_cache (
                user_id INTEGER PRIMARY KEY,
                total_value REAL,
                last_updated DATETIME DEFAULT CURRENT_TIMESTAMP
            );
            """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS portfolios (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
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
            market_update_rate INTEGER DEFAULT 1,       -- minutes
            starting_money REAL DEFAULT 1000.0
        );
        """)



        # Check if empty
        cur = await db.execute("SELECT COUNT(*) FROM stocks")
        count = (await cur.fetchone())[0]

        # Insert starting stocks if empty
        if count == 0:
            await db.executemany(
                "INSERT INTO stocks(ticker, name, price) VALUES(?, ?, ?)",
                DEFAULT_STOCKS
            )
            print("✅ Inserted default stock data")

        else:
            print(f"ℹ️ Skipped insert; {count} stocks already exist")

        await db.commit()

async def get_user(discord_id, guild_id):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT discord_id, cash FROM users WHERE discord_id=? AND guild_id=?",
            (str(discord_id), str(guild_id))
        )
        row = await cur.fetchone()
        if row:
            return (row[0], float(row[1]))  # ensure cash is a float
        return None

async def create_user(discord_id, guild_id):
    from utils.database import get_server_settings  # or move this helper above
    settings = await get_server_settings(guild_id)
    starting_money = settings.get("starting_money", 1000.0)

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR IGNORE INTO users(discord_id, guild_id, cash) VALUES(?, ?, ?)",
            (str(discord_id), str(guild_id), float(starting_money))
        )
        await db.commit()

async def update_balance(discord_id, guild_id, delta):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE users SET cash = cash + ? WHERE discord_id=? AND guild_id=?",
            (delta, str(discord_id), str(guild_id))
        )
        await db.commit()

async def record_trade(user_id, guild_id, ticker, qty, side):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO trades(user_id, guild_id, ticker, qty, side) VALUES(?, ?, ?, ?, ?)",
            (str(user_id), str(guild_id), ticker.upper(), qty, side.upper())
        )
        await db.commit()
    return f"Recorded {side.upper()} trade for {qty} {ticker.upper()}."

async def get_stock_price(symbol):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT price FROM stocks WHERE ticker=? OR name=?",
            (symbol.upper(), symbol.title())
        )
        row = await cur.fetchone()
        return row[0] if row else None

async def update_stock_price(ticker, price):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE stocks SET price=? WHERE ticker=?", (price, ticker.upper()))
        await db.commit()

async def get_moving_average(ticker, window=5):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT price FROM price_history WHERE ticker=? ORDER BY id DESC LIMIT ?",
            (ticker.upper(), window)
        )
        prices = [row[0] for row in await cur.fetchall()]
        if not prices:
            return None
        return sum(prices) / len(prices)

async def get_leaderboard(limit=10):
    async with aiosqlite.connect("data/market.db") as db:
        query = """
        SELECT u.discord_id,
               u.cash + IFNULL(SUM(p.qty * s.price), 0) AS total_value
        FROM users u
        LEFT JOIN portfolios p ON u.id = p.user_id
        LEFT JOIN stocks s ON p.ticker = s.ticker
        GROUP BY u.id
        ORDER BY total_value DESC
        LIMIT ?;
        """
        cur = await db.execute(query, (limit,))
        rows = await cur.fetchall()
    return rows

async def update_leaderboard_cache():
    async with aiosqlite.connect("data/market.db") as db:
        await db.execute("DELETE FROM leaderboard_cache")

        # Compute total = cash + (BUY - SELL) * current price
        query = """
        INSERT INTO leaderboard_cache (user_id, total_value)
        SELECT
            u.id,
            u.cash +
            IFNULL(SUM(
                (CASE WHEN t.side = 'BUY' THEN t.qty ELSE -t.qty END) * s.price
            ), 0) AS total_value
        FROM users u
        LEFT JOIN trades t ON u.discord_id = t.user_id AND u.guild_id = t.guild_id
        LEFT JOIN stocks s ON t.ticker = s.ticker
        GROUP BY u.id;
        """
        await db.execute(query)
        await db.commit()

async def get_cached_leaderboard(limit=10):
    async with aiosqlite.connect("data/market.db") as db:
        query = """
        SELECT u.discord_id, c.total_value, c.last_updated
        FROM leaderboard_cache c
        JOIN users u ON u.id = c.user_id
        ORDER BY c.total_value DESC
        LIMIT ?;
        """
        cur = await db.execute(query, (limit,))
        rows = await cur.fetchall()
    return rows

async def get_server_settings(guild_id):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT * FROM server_settings WHERE guild_id=?", (str(guild_id),))
        row = await cur.fetchone()
        if not row:
            # Create default row if not found
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


