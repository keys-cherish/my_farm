# 作物配置: name -> (emoji, seed_cost, harvest_reward, growth_minutes)
CROPS = {
    "白菜":   {"emoji": "🥬", "seed": 3,    "reward": 8,    "minutes": 30},
    "土豆":   {"emoji": "🥔", "seed": 5,    "reward": 15,   "minutes": 60},
    "胡萝卜": {"emoji": "🥕", "seed": 10,   "reward": 28,   "minutes": 120},
    "辣椒":   {"emoji": "🌶", "seed": 15,   "reward": 40,   "minutes": 150},
    "番茄":   {"emoji": "🍅", "seed": 20,   "reward": 52,   "minutes": 180},
    "蘑菇":   {"emoji": "🍄", "seed": 30,   "reward": 75,   "minutes": 210},
    "玉米":   {"emoji": "🌽", "seed": 40,   "reward": 100,  "minutes": 240},
    "茄子":   {"emoji": "🍆", "seed": 60,   "reward": 148,  "minutes": 300},
    "草莓":   {"emoji": "🍓", "seed": 80,   "reward": 195,  "minutes": 360},
    "菠萝":   {"emoji": "🍍", "seed": 120,  "reward": 285,  "minutes": 420},
    "西瓜":   {"emoji": "🍉", "seed": 150,  "reward": 360,  "minutes": 480},
    "葡萄":   {"emoji": "🍇", "seed": 250,  "reward": 580,  "minutes": 600},
    "金葵花": {"emoji": "🌻", "seed": 500,  "reward": 1150, "minutes": 720},
    "芒果":   {"emoji": "🥭", "seed": 1000, "reward": 2200, "minutes": 960},
    "龙果":   {"emoji": "🐉", "seed": 2000, "reward": 4200, "minutes": 1440},
}

LEVELS = {
    1:  {"plots": 4,  "exp_next": 50},
    2:  {"plots": 5,  "exp_next": 120},
    3:  {"plots": 6,  "exp_next": 250},
    4:  {"plots": 9,  "exp_next": 500},
    5:  {"plots": 10, "exp_next": 800},
    6:  {"plots": 12, "exp_next": 1200},
    7:  {"plots": 14, "exp_next": 1800},
    8:  {"plots": 16, "exp_next": 2500},
    9:  {"plots": 18, "exp_next": 3500},
    10: {"plots": 20, "exp_next": 999999},
}

PEST_CHANCE = 0.08
DEAD_OVERTIME_HOURS = 24

from datetime import datetime, timezone


def get_crop(name: str) -> dict | None:
    return CROPS.get(name)


def get_level_info(level: int) -> dict:
    return LEVELS.get(level, LEVELS[10])


def format_time(minutes: float) -> str:
    if minutes <= 0:
        return "已成熟"
    h = int(minutes // 60)
    m = int(minutes % 60)
    if h > 0 and m > 0:
        return f"{h}小时{m}分"
    if h > 0:
        return f"{h}小时"
    return f"{m}分钟"


def format_time_short(minutes: float) -> str:
    if minutes <= 0:
        return "✅"
    h = int(minutes // 60)
    m = int(minutes % 60)
    return f"{h}h{m:02d}"


def get_remaining_minutes(planted_at, growth_minutes: int) -> float:
    if not planted_at:
        return -1
    if isinstance(planted_at, str):
        planted_at = datetime.fromisoformat(planted_at)
    if planted_at.tzinfo is None:
        planted_at = planted_at.replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    elapsed = (now - planted_at).total_seconds() / 60
    return max(0, growth_minutes - elapsed)


def get_minutes_since_maturity(planted_at, growth_minutes: int) -> float:
    if not planted_at:
        return -1
    if isinstance(planted_at, str):
        planted_at = datetime.fromisoformat(planted_at)
    if planted_at.tzinfo is None:
        planted_at = planted_at.replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    elapsed = (now - planted_at).total_seconds() / 60
    return elapsed - growth_minutes
