// Crops: name, emoji, seedCost, harvestReward, growthMinutes
const CROPS = {
  '白菜':   { emoji: '🥬', seed: 3,    reward: 8,    minutes: 30 },
  '土豆':   { emoji: '🥔', seed: 5,    reward: 15,   minutes: 60 },
  '胡萝卜': { emoji: '🥕', seed: 10,   reward: 28,   minutes: 120 },
  '辣椒':   { emoji: '🌶', seed: 15,   reward: 40,   minutes: 150 },
  '番茄':   { emoji: '🍅', seed: 20,   reward: 52,   minutes: 180 },
  '蘑菇':   { emoji: '🍄', seed: 30,   reward: 75,   minutes: 210 },
  '玉米':   { emoji: '🌽', seed: 40,   reward: 100,  minutes: 240 },
  '茄子':   { emoji: '🍆', seed: 60,   reward: 148,  minutes: 300 },
  '草莓':   { emoji: '🍓', seed: 80,   reward: 195,  minutes: 360 },
  '菠萝':   { emoji: '🍍', seed: 120,  reward: 285,  minutes: 420 },
  '西瓜':   { emoji: '🍉', seed: 150,  reward: 360,  minutes: 480 },
  '葡萄':   { emoji: '🍇', seed: 250,  reward: 580,  minutes: 600 },
  '金葵花': { emoji: '🌻', seed: 500,  reward: 1150, minutes: 720 },
  '芒果':   { emoji: '🥭', seed: 1000, reward: 2200, minutes: 960 },
  '龙果':   { emoji: '🐉', seed: 2000, reward: 4200, minutes: 1440 },
};

// Level system: level -> { plots, expNeeded (to reach next level) }
const LEVELS = {
  1:  { plots: 4,  expNext: 50 },
  2:  { plots: 5,  expNext: 120 },
  3:  { plots: 6,  expNext: 250 },
  4:  { plots: 9,  expNext: 500 },
  5:  { plots: 10, expNext: 800 },
  6:  { plots: 12, expNext: 1200 },
  7:  { plots: 14, expNext: 1800 },
  8:  { plots: 16, expNext: 2500 },
  9:  { plots: 18, expNext: 3500 },
  10: { plots: 20, expNext: 999999 },
};

const PEST_CHANCE = 0.08;      // 8% chance per plot check
const DEAD_OVERTIME_HOURS = 24; // dies if not harvested in 24h after maturity

function getCrop(name) {
  return CROPS[name] || null;
}

function getCropByEmoji(emoji) {
  for (const [name, data] of Object.entries(CROPS)) {
    if (data.emoji === emoji) return { name, ...data };
  }
  return null;
}

function getAllCrops() {
  return CROPS;
}

function getLevelInfo(level) {
  return LEVELS[level] || LEVELS[10];
}

function formatTime(minutes) {
  if (minutes <= 0) return '已成熟';
  const h = Math.floor(minutes / 60);
  const m = Math.floor(minutes % 60);
  if (h > 0 && m > 0) return `${h}小时${m}分`;
  if (h > 0) return `${h}小时`;
  return `${m}分钟`;
}

function formatTimeShort(minutes) {
  if (minutes <= 0) return '✅';
  const h = Math.floor(minutes / 60);
  const m = Math.floor(minutes % 60);
  return `${h}h${String(m).padStart(2, '0')}`;
}

// returns remaining minutes, 0 if ready
function getRemainingMinutes(plantedAt, growthMinutes) {
  if (!plantedAt) return -1;
  const planted = new Date(plantedAt).getTime();
  const now = Date.now();
  const elapsed = (now - planted) / 60000;
  return Math.max(0, growthMinutes - elapsed);
}

// returns minutes since maturity (negative if not mature yet)
function getMinutesSinceMaturity(plantedAt, growthMinutes) {
  if (!plantedAt) return -1;
  const planted = new Date(plantedAt).getTime();
  const now = Date.now();
  const elapsed = (now - planted) / 60000;
  return elapsed - growthMinutes;
}

module.exports = {
  CROPS, LEVELS, PEST_CHANCE, DEAD_OVERTIME_HOURS,
  getCrop, getCropByEmoji, getAllCrops, getLevelInfo,
  formatTime, formatTimeShort, getRemainingMinutes, getMinutesSinceMaturity,
};
