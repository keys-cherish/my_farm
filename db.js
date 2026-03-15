const { Pool } = require('pg');

const pool = new Pool({
  connectionString: process.env.DATABASE_URL,
});

async function init() {
  await pool.query(`
    CREATE TABLE IF NOT EXISTS users (
      user_id BIGINT PRIMARY KEY,
      username TEXT DEFAULT '',
      balance DOUBLE PRECISION DEFAULT 100,
      level INTEGER DEFAULT 1,
      exp INTEGER DEFAULT 0,
      plots INTEGER DEFAULT 4,
      last_water TIMESTAMP,
      created_at TIMESTAMP DEFAULT NOW()
    )
  `);
  await pool.query(`
    CREATE TABLE IF NOT EXISTS plots (
      id SERIAL PRIMARY KEY,
      user_id BIGINT NOT NULL REFERENCES users(user_id),
      slot INTEGER NOT NULL,
      crop TEXT DEFAULT '',
      planted_at TIMESTAMP,
      water_count INTEGER DEFAULT 0,
      has_pest BOOLEAN DEFAULT FALSE,
      has_weed BOOLEAN DEFAULT FALSE,
      is_dead BOOLEAN DEFAULT FALSE,
      UNIQUE(user_id, slot)
    )
  `);
}

// --- User ---
async function getUser(userId) {
  const { rows } = await pool.query('SELECT * FROM users WHERE user_id = $1', [userId]);
  return rows[0] || null;
}

async function createUser(userId, username) {
  await pool.query(
    `INSERT INTO users (user_id, username, balance, level, exp, plots)
     VALUES ($1, $2, 100, 1, 0, 4) ON CONFLICT (user_id) DO NOTHING`,
    [userId, username || '']
  );
  const user = await getUser(userId);
  const { rows } = await pool.query('SELECT COUNT(*)::int AS c FROM plots WHERE user_id = $1', [userId]);
  if (rows[0].c === 0) {
    for (let i = 0; i < user.plots; i++) {
      await pool.query('INSERT INTO plots (user_id, slot) VALUES ($1, $2) ON CONFLICT DO NOTHING', [userId, i]);
    }
  }
  return getUser(userId);
}

async function updateBalance(userId, amount) {
  await pool.query('UPDATE users SET balance = balance + $1 WHERE user_id = $2', [amount, userId]);
}

async function setBalance(userId, amount) {
  await pool.query('UPDATE users SET balance = $1 WHERE user_id = $2', [amount, userId]);
}

async function addExp(userId, exp) {
  await pool.query('UPDATE users SET exp = exp + $1 WHERE user_id = $2', [exp, userId]);
}

async function setLevel(userId, level, plots) {
  await pool.query('UPDATE users SET level = $1, plots = $2 WHERE user_id = $3', [level, plots, userId]);
}

async function updateUsername(userId, username) {
  await pool.query('UPDATE users SET username = $1 WHERE user_id = $2', [username || '', userId]);
}

async function setLastWater(userId, time) {
  await pool.query('UPDATE users SET last_water = $1 WHERE user_id = $2', [time, userId]);
}

// --- Plots ---
async function getPlots(userId) {
  const { rows } = await pool.query('SELECT * FROM plots WHERE user_id = $1 ORDER BY slot ASC', [userId]);
  return rows;
}

async function getPlot(userId, slot) {
  const { rows } = await pool.query('SELECT * FROM plots WHERE user_id = $1 AND slot = $2', [userId, slot]);
  return rows[0] || null;
}

async function plantCrop(userId, slot, cropName) {
  await pool.query(
    `UPDATE plots SET crop = $1, planted_at = NOW(), water_count = 0,
     has_pest = FALSE, has_weed = FALSE, is_dead = FALSE WHERE user_id = $2 AND slot = $3`,
    [cropName, userId, slot]
  );
}

async function clearPlot(userId, slot) {
  await pool.query(
    `UPDATE plots SET crop = '', planted_at = NULL, water_count = 0,
     has_pest = FALSE, has_weed = FALSE, is_dead = FALSE WHERE user_id = $1 AND slot = $2`,
    [userId, slot]
  );
}

async function waterPlot(userId, slot) {
  await pool.query('UPDATE plots SET water_count = water_count + 1 WHERE user_id = $1 AND slot = $2', [userId, slot]);
}

async function setPest(userId, slot, val) {
  await pool.query('UPDATE plots SET has_pest = $1 WHERE user_id = $2 AND slot = $3', [val, userId, slot]);
}

async function setDead(userId, slot) {
  await pool.query('UPDATE plots SET is_dead = TRUE WHERE user_id = $1 AND slot = $2', [userId, slot]);
}

async function addPlot(userId, slot) {
  await pool.query('INSERT INTO plots (user_id, slot) VALUES ($1, $2) ON CONFLICT DO NOTHING', [userId, slot]);
}

// --- Leaderboard ---
async function getTopUsers(limit = 10) {
  const { rows } = await pool.query('SELECT * FROM users ORDER BY balance DESC LIMIT $1', [limit]);
  return rows;
}

async function getAllUsers() {
  const { rows } = await pool.query('SELECT * FROM users');
  return rows;
}

module.exports = {
  pool, init, getUser, createUser, updateBalance, setBalance, addExp, setLevel,
  updateUsername, setLastWater,
  getPlots, getPlot, plantCrop, clearPlot, waterPlot, setPest, setDead, addPlot,
  getTopUsers, getAllUsers,
};
