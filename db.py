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
                has_weed BOOLEAN DEFAULT FALSE,
                is_dead BOOLEAN DEFAULT FALSE,
                UNIQUE(user_id, slot)
            )
        """)


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


# --- Plots ---
async def get_plots(user_id: int):
    async with pool.acquire() as conn:
        return await conn.fetch("SELECT * FROM plots WHERE user_id = $1 ORDER BY slot ASC", user_id)


async def plant_crop(user_id: int, slot: int, crop_name: str):
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE plots SET crop = $1, planted_at = NOW(), water_count = 0, "
            "has_pest = FALSE, has_weed = FALSE, is_dead = FALSE "
            "WHERE user_id = $2 AND slot = $3",
            crop_name, user_id, slot,
        )


async def clear_plot(user_id: int, slot: int):
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE plots SET crop = '', planted_at = NULL, water_count = 0, "
            "has_pest = FALSE, has_weed = FALSE, is_dead = FALSE "
            "WHERE user_id = $1 AND slot = $2",
            user_id, slot,
        )


async def water_plot(user_id: int, slot: int):
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE plots SET water_count = water_count + 1 WHERE user_id = $1 AND slot = $2",
            user_id, slot,
        )


async def set_pest(user_id: int, slot: int, val: bool):
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE plots SET has_pest = $1 WHERE user_id = $2 AND slot = $3",
            val, user_id, slot,
        )


async def set_dead(user_id: int, slot: int):
    async with pool.acquire() as conn:
        await conn.execute("UPDATE plots SET is_dead = TRUE WHERE user_id = $1 AND slot = $2", user_id, slot)


async def add_plot(user_id: int, slot: int):
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO plots (user_id, slot) VALUES ($1, $2) ON CONFLICT DO NOTHING",
            user_id, slot,
        )


# --- Leaderboard ---
async def get_top_users(limit: int = 10):
    async with pool.acquire() as conn:
        return await conn.fetch("SELECT * FROM users ORDER BY balance DESC LIMIT $1", limit)
