import asyncpg
import os


pool: asyncpg.Pool | None = None


async def init():
    global pool
    pool = await asyncpg.create_pool(dsn=os.getenv("DATABASE_URL"))
    async with pool.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id BIGINT PRIMARY KEY,
                username TEXT DEFAULT '',
                balance DOUBLE PRECISION DEFAULT 100,
                level INTEGER DEFAULT 1,
                exp INTEGER DEFAULT 0,
                plots INTEGER DEFAULT 4,
                last_water TIMESTAMPTZ,
                steal_count INTEGER DEFAULT 0,
                steal_date TEXT DEFAULT '',
                created_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS plots (
                id SERIAL PRIMARY KEY,
                user_id BIGINT NOT NULL REFERENCES users(user_id),
                slot INTEGER NOT NULL,
                crop TEXT DEFAULT '',
                planted_at TIMESTAMPTZ,
                water_count INTEGER DEFAULT 0,
                has_pest BOOLEAN DEFAULT FALSE,
                pest_type TEXT DEFAULT '',
                pest_at TIMESTAMPTZ,
                is_dead BOOLEAN DEFAULT FALSE,
                notified_mature BOOLEAN DEFAULT FALSE,
                UNIQUE(user_id, slot)
            )
        """)
        # migrations for existing tables
        for col, typ, dflt in [
            ("pest_type", "TEXT", "''"),
            ("pest_at", "TIMESTAMPTZ", "NULL"),
            ("notified_mature", "BOOLEAN", "FALSE"),
            ("steal_count", "INTEGER", "0"),
            ("steal_date", "TEXT", "''"),
        ]:
            try:
                table = "plots" if col not in ("steal_count", "steal_date") else "users"
                await conn.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {col} {typ} DEFAULT {dflt}")
            except Exception:
                pass


async def close():
    global pool
    if pool:
        await pool.close()


# --- User ---
async def get_user(user_id: int):
    async with pool.acquire() as conn:
        return await conn.fetchrow("SELECT * FROM users WHERE user_id = $1", user_id)


async def create_user(user_id: int, username: str):
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO users (user_id, username, balance, level, exp, plots) "
            "VALUES ($1, $2, 100, 1, 0, 4) ON CONFLICT (user_id) DO NOTHING",
            user_id, username or "",
        )
        user = await conn.fetchrow("SELECT * FROM users WHERE user_id = $1", user_id)
        count = await conn.fetchval("SELECT COUNT(*) FROM plots WHERE user_id = $1", user_id)
        if count == 0:
            for i in range(user["plots"]):
                await conn.execute(
                    "INSERT INTO plots (user_id, slot) VALUES ($1, $2) ON CONFLICT DO NOTHING",
                    user_id, i,
                )
        return await conn.fetchrow("SELECT * FROM users WHERE user_id = $1", user_id)


async def update_balance(user_id: int, amount: float):
    async with pool.acquire() as conn:
        await conn.execute("UPDATE users SET balance = balance + $1 WHERE user_id = $2", amount, user_id)


async def add_exp(user_id: int, exp: int):
    async with pool.acquire() as conn:
        await conn.execute("UPDATE users SET exp = exp + $1 WHERE user_id = $2", exp, user_id)


async def set_level(user_id: int, level: int, plots: int):
    async with pool.acquire() as conn:
        await conn.execute("UPDATE users SET level = $1, plots = $2 WHERE user_id = $3", level, plots, user_id)


async def update_username(user_id: int, username: str):
    async with pool.acquire() as conn:
        await conn.execute("UPDATE users SET username = $1 WHERE user_id = $2", username or "", user_id)


async def set_last_water(user_id: int):
    async with pool.acquire() as conn:
        await conn.execute("UPDATE users SET last_water = NOW() WHERE user_id = $1", user_id)


async def get_steal_info(user_id: int, today: str):
    async with pool.acquire() as conn:
        user = await conn.fetchrow("SELECT steal_count, steal_date FROM users WHERE user_id = $1", user_id)
        if not user:
            return 0
        if user["steal_date"] != today:
            await conn.execute("UPDATE users SET steal_count = 0, steal_date = $1 WHERE user_id = $2", today, user_id)
            return 0
        return user["steal_count"]


async def inc_steal_count(user_id: int, today: str):
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE users SET steal_count = steal_count + 1, steal_date = $1 WHERE user_id = $2",
            today, user_id,
        )


# --- Plots ---
async def get_plots(user_id: int):
    async with pool.acquire() as conn:
        return await conn.fetch("SELECT * FROM plots WHERE user_id = $1 ORDER BY slot ASC", user_id)


async def plant_crop(user_id: int, slot: int, crop_name: str):
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE plots SET crop = $1, planted_at = NOW(), water_count = 0, "
            "has_pest = FALSE, pest_type = '', pest_at = NULL, is_dead = FALSE, notified_mature = FALSE "
            "WHERE user_id = $2 AND slot = $3",
            crop_name, user_id, slot,
        )


async def clear_plot(user_id: int, slot: int):
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE plots SET crop = '', planted_at = NULL, water_count = 0, "
            "has_pest = FALSE, pest_type = '', pest_at = NULL, is_dead = FALSE, notified_mature = FALSE "
            "WHERE user_id = $1 AND slot = $2",
            user_id, slot,
        )


async def water_plot(user_id: int, slot: int, minutes_saved: float):
    """Advance planted_at backward by minutes_saved (effectively speeds up growth)."""
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE plots SET "
            "planted_at = planted_at - make_interval(secs => $1), "
            "water_count = water_count + 1 "
            "WHERE user_id = $2 AND slot = $3",
            minutes_saved * 60, user_id, slot,
        )


async def set_pest(user_id: int, slot: int, val: bool, pest_type: str = ""):
    async with pool.acquire() as conn:
        if val:
            await conn.execute(
                "UPDATE plots SET has_pest = TRUE, pest_type = $1, pest_at = NOW() "
                "WHERE user_id = $2 AND slot = $3",
                pest_type, user_id, slot,
            )
        else:
            await conn.execute(
                "UPDATE plots SET has_pest = FALSE, pest_type = '', pest_at = NULL "
                "WHERE user_id = $1 AND slot = $2",
                user_id, slot,
            )


async def set_dead(user_id: int, slot: int):
    async with pool.acquire() as conn:
        await conn.execute("UPDATE plots SET is_dead = TRUE WHERE user_id = $1 AND slot = $2", user_id, slot)


async def set_notified_mature(user_id: int, slot: int):
    async with pool.acquire() as conn:
        await conn.execute("UPDATE plots SET notified_mature = TRUE WHERE user_id = $1 AND slot = $2", user_id, slot)


async def add_plot(user_id: int, slot: int):
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO plots (user_id, slot) VALUES ($1, $2) ON CONFLICT DO NOTHING",
            user_id, slot,
        )


# --- Batch queries for scheduled jobs ---
async def get_all_growing_plots():
    """Get all plots with active crops (not dead, for random events)"""
    async with pool.acquire() as conn:
        return await conn.fetch(
            "SELECT p.*, u.username FROM plots p JOIN users u ON p.user_id = u.user_id "
            "WHERE p.crop != '' AND p.is_dead = FALSE AND p.has_pest = FALSE"
        )


async def get_pest_expired_plots(minutes: int = 120):
    """Get plots where pest has been active for more than X minutes"""
    async with pool.acquire() as conn:
        return await conn.fetch(
            "SELECT p.*, u.username FROM plots p JOIN users u ON p.user_id = u.user_id "
            "WHERE p.has_pest = TRUE AND p.is_dead = FALSE AND p.pest_at IS NOT NULL "
            "AND p.pest_at < NOW() - make_interval(mins => $1)",
            minutes,
        )


async def get_mature_unnotified():
    """Get mature crops that haven't been notified yet"""
    async with pool.acquire() as conn:
        return await conn.fetch(
            "SELECT p.*, u.username FROM plots p JOIN users u ON p.user_id = u.user_id "
            "WHERE p.crop != '' AND p.is_dead = FALSE AND p.notified_mature = FALSE "
            "AND p.planted_at IS NOT NULL"
        )


async def get_random_harvestable_plot(exclude_user_id: int):
    """Get a random mature plot from another user (for stealing)"""
    async with pool.acquire() as conn:
        return await conn.fetchrow(
            "SELECT p.*, u.username FROM plots p JOIN users u ON p.user_id = u.user_id "
            "WHERE p.crop != '' AND p.is_dead = FALSE AND p.has_pest = FALSE "
            "AND p.user_id != $1 AND p.planted_at IS NOT NULL "
            "ORDER BY RANDOM() LIMIT 1",
            exclude_user_id,
        )


# --- Leaderboard ---
async def get_top_users(limit: int = 10):
    async with pool.acquire() as conn:
        return await conn.fetch("SELECT * FROM users ORDER BY balance DESC LIMIT $1", limit)
