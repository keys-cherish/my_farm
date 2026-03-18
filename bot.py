import os
import logging
import math
import random
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

try:
    import uvloop
    uvloop.install()
except ImportError:
    pass

from dotenv import load_dotenv
from telegram import BotCommand, Update
from telegram.ext import Application, CommandHandler, ContextTypes
from log_setup import bind_context, clear_context, configure_logging, shutdown_logging

load_dotenv()

configure_logging()
logger = logging.getLogger("farm.bot")

import db
import game

TOKEN = os.getenv("BOT_TOKEN")
if not TOKEN:
    raise SystemExit("Please set BOT_TOKEN in .env")

ALLOWED_CHAT_IDS = [int(x) for x in os.getenv("ALLOWED_CHAT_IDS", "").split(",") if x.strip()]
ALLOWED_CHAT_USERNAMES = [x.strip().lower() for x in os.getenv("ALLOWED_CHAT_USERNAMES", "").split(",") if x.strip()]
ALLOWED_TOPIC_THREAD_IDS = [int(x) for x in os.getenv("ALLOWED_TOPIC_THREAD_IDS", "").split(",") if x.strip()]

# 事件通知发送到的 chat_id 和 thread_id
NOTIFY_CHAT_ID = ALLOWED_CHAT_IDS[0] if ALLOWED_CHAT_IDS else None
NOTIFY_THREAD_ID = ALLOWED_TOPIC_THREAD_IDS[0] if ALLOWED_TOPIC_THREAD_IDS else None

STEAL_DAILY_LIMIT = 5
PEST_EVENT_TYPES = [
    ("🐛", "蛀虫"),
    ("💩", "粪便"),
]
PEST_DEATH_MINUTES = 120  # 2小时不清理就枯死


SLOW_COMMAND_MS = int(os.getenv("LOG_SLOW_COMMAND_MS", "500"))
SLOW_JOB_MS = int(os.getenv("LOG_SLOW_JOB_MS", "1000"))

CommandHandlerFn = Callable[[Update, ContextTypes.DEFAULT_TYPE], Awaitable[Any]]
JobHandlerFn = Callable[[ContextTypes.DEFAULT_TYPE], Awaitable[Any]]


def _build_update_log_context(update: Update) -> dict[str, Any]:
    message = update.effective_message
    user = update.effective_user
    chat = update.effective_chat
    return {
        "user_id": user.id if user else "-",
        "chat_id": chat.id if chat else "-",
        "thread_id": getattr(message, "message_thread_id", None) if message else "-",
    }


async def _run_logged_command(
    command_name: str,
    callback: CommandHandlerFn,
    update: Update,
    ctx: ContextTypes.DEFAULT_TYPE,
):
    trace_id = uuid.uuid4().hex[:12]
    token = bind_context(command=command_name, trace_id=trace_id, **_build_update_log_context(update))
    started = time.perf_counter()
    logger.info("command.start", extra={"event": "command_start"})
    try:
        result = await callback(update, ctx)
    except Exception:
        duration_ms = (time.perf_counter() - started) * 1000
        logger.exception("command.failed", extra={"event": "command_failed", "duration_ms": duration_ms})
        raise
    else:
        duration_ms = (time.perf_counter() - started) * 1000
        if duration_ms >= SLOW_COMMAND_MS:
            logger.warning("command.slow", extra={"event": "command_slow", "duration_ms": duration_ms})
        else:
            logger.info("command.done", extra={"event": "command_done", "duration_ms": duration_ms})
        return result
    finally:
        clear_context(token)


def _wrap_command(command_name: str, callback: CommandHandlerFn) -> CommandHandlerFn:
    async def wrapped(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        return await _run_logged_command(command_name, callback, update, ctx)

    wrapped.__name__ = f"wrapped_{callback.__name__}_{command_name}"
    return wrapped


def _wrap_job(job_name: str, callback: JobHandlerFn) -> JobHandlerFn:
    async def wrapped(ctx: ContextTypes.DEFAULT_TYPE):
        trace_id = uuid.uuid4().hex[:12]
        token = bind_context(command=job_name, trace_id=trace_id)
        started = time.perf_counter()
        logger.info("job.start", extra={"event": "job_start"})
        try:
            result = await callback(ctx)
        except Exception:
            duration_ms = (time.perf_counter() - started) * 1000
            logger.exception("job.failed", extra={"event": "job_failed", "duration_ms": duration_ms})
            raise
        else:
            duration_ms = (time.perf_counter() - started) * 1000
            if duration_ms >= SLOW_JOB_MS:
                logger.warning("job.slow", extra={"event": "job_slow", "duration_ms": duration_ms})
            else:
                logger.info("job.done", extra={"event": "job_done", "duration_ms": duration_ms})
            return result
        finally:
            clear_context(token)

    wrapped.__name__ = f"wrapped_{callback.__name__}_{job_name}"
    return wrapped


def is_allowed(update: Update) -> bool:
    chat = update.effective_chat
    msg = update.message
    if not chat:
        logger.warning("access.denied", extra={"event": "access_denied"})
        return False
    thread_id = msg.message_thread_id if msg else None
    if chat.type == "private":
        return True
    if not ALLOWED_CHAT_IDS and not ALLOWED_CHAT_USERNAMES:
        return True
    chat_ok = False
    if ALLOWED_CHAT_IDS and chat.id in ALLOWED_CHAT_IDS:
        chat_ok = True
    if ALLOWED_CHAT_USERNAMES and chat.username and chat.username.lower() in ALLOWED_CHAT_USERNAMES:
        chat_ok = True
    if not chat_ok:
        logger.info("access.denied.chat", extra={"event": "access_denied"})
        return False
    if ALLOWED_TOPIC_THREAD_IDS:
        if thread_id not in ALLOWED_TOPIC_THREAD_IDS:
            logger.info("access.denied.thread", extra={"event": "access_denied"})
            return False
    return True


async def notify(bot, text):
    """发送事件通知到群组"""
    if not NOTIFY_CHAT_ID:
        return
    try:
        kwargs = {"chat_id": NOTIFY_CHAT_ID, "text": text}
        if NOTIFY_THREAD_ID:
            kwargs["message_thread_id"] = NOTIFY_THREAD_ID
        await bot.send_message(**kwargs)
    except Exception:
        logger.exception("notify.failed", extra={"event": "notify_failed"})


# ── helpers ──────────────────────────────────────────────
async def ensure_user(update: Update):
    uid = update.effective_user.id
    uname = update.effective_user.first_name or update.effective_user.username or ""
    user = await db.get_user(uid)
    if not user:
        user = await db.create_user(uid, uname)
    else:
        await db.update_username(uid, uname)
    return user


async def check_level_up(user_id: int):
    user = await db.get_user(user_id)
    info = game.get_level_info(user["level"])
    if user["exp"] >= info["exp_next"] and user["level"] < 10:
        new_level = user["level"] + 1
        new_info = game.get_level_info(new_level)
        await db.set_level(user_id, new_level, new_info["plots"])
        for i in range(new_info["plots"]):
            await db.add_plot(user_id, i)
        return new_level
    return None


# ── scheduled jobs ───────────────────────────────────────
async def job_random_pest(ctx: ContextTypes.DEFAULT_TYPE):
    """定期随机给玩家农场生成害虫/粪便"""
    growing = await db.get_all_growing_plots()
    if not growing:
        return

    # 按用户分组
    by_user = {}
    for p in growing:
        by_user.setdefault(p["user_id"], []).append(p)

    for user_id, plots in by_user.items():
        # 每个用户 15% 概率触发一次事件
        if random.random() > 0.15:
            continue
        target = random.choice(plots)
        pest_emoji, pest_name = random.choice(PEST_EVENT_TYPES)
        await db.set_pest(user_id, target["slot"], True, f"{pest_emoji}{pest_name}")
        username = target["username"] or "Anonymous"
        await notify(
            ctx.bot,
            f"⚠️ {username} 的农场出现了 {pest_emoji}{pest_name}×1！\n"
            f"2小时内不清理作物会枯死！ /fm_clean"
        )
        logger.info(f"Pest event: {username} got {pest_name}")


async def job_check_pest_death(ctx: ContextTypes.DEFAULT_TYPE):
    """检查害虫超时，杀死作物"""
    expired = await db.get_pest_expired_plots(PEST_DEATH_MINUTES)
    if not expired:
        return

    by_user = {}
    for p in expired:
        by_user.setdefault(p["user_id"], []).append(p)

    for user_id, plots in by_user.items():
        dead_crops = []
        for p in plots:
            crop = game.get_crop(p["crop"])
            if not crop:
                continue
            await db.set_dead(user_id, p["slot"])
            pest_info = p["pest_type"] or "害虫"
            dead_crops.append(f"  💀 {crop['emoji']}{p['crop']}（{pest_info}）")

        if dead_crops:
            username = plots[0]["username"] or "Anonymous"
            text = f"☠️ {username} 的作物枯死了...\n" + "\n".join(dead_crops) + "\n用 /fm_farm 查看农场"
            await notify(ctx.bot, text)
            logger.info(f"Pest death: {username}, {len(dead_crops)} crops died")


async def job_check_mature(ctx: ContextTypes.DEFAULT_TYPE):
    """检查作物成熟，发送通知"""
    plots = await db.get_mature_unnotified()
    if not plots:
        return

    by_user = {}
    for p in plots:
        crop = game.get_crop(p["crop"])
        if not crop:
            continue
        remain = game.get_remaining_minutes(p["planted_at"], p["effective_minutes"] or crop["minutes"])
        if remain <= 0:
            by_user.setdefault(p["user_id"], []).append(p)

    for user_id, mature_plots in by_user.items():
        crop_names = []
        for p in mature_plots:
            crop = game.get_crop(p["crop"])
            if crop:
                crop_names.append(f"{crop['emoji']}{p['crop']}")
            await db.set_notified_mature(user_id, p["slot"])

        if crop_names:
            username = mature_plots[0]["username"] or "Anonymous"
            text = f"🌾 {username} 的作物成熟啦！\n{', '.join(crop_names)}\n快去 /fm_harvest 收获吧~"
            await notify(ctx.bot, text)
            logger.info(f"Mature notify: {username}, {len(crop_names)} crops")


# ── commands ─────────────────────────────────────────────
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update):
        return
    user = await ensure_user(update)
    await update.message.reply_text(
        f"🌾 欢迎来到农场！\n\n"
        f"你获得了 {user['balance']} MB 启动资金和 {user['plots']} 块地。\n"
        f"输入 /fm_help 查看玩法说明。"
    )


async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update):
        return
    await update.message.reply_text(
        "🌾 农场玩法说明\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "🌱 /fm_plant 作物名 — 种植作物\n"
        "🌱 /fm_plantall 作物名 — 批量种植\n"
        "🌾 /fm_farm — 查看农场状态\n"
        "🥬 /fm_crops — 查看作物列表\n"
        "📦 /fm_harvest — 收获成熟作物\n"
        "💧 /fm_water — 给作物浇水(加速10%)\n"
        "🧹 /fm_clean — 清理害虫/粪便\n"
        "☠️ /fm_cleardead — 清除枯死作物\n"
        "🥷 /fm_steal — 偷菜(每日5次)\n"
        "🏪 /fm_shop — 商店\n"
        "⬆️ /fm_upgrade — 升级农场\n"
        "💰 /fm_balance — 查看余额\n"
        "🏆 /fm_rank — 排行榜\n"
        "🔄 /fm_refresh — 刷新农场状态\n\n"
        "💡 种植作物需要花费种子费用(MB)\n"
        "💡 出现害虫/粪便后2小时内清理，否则作物枯死\n"
        "💡 浇水可以加速10%生长\n"
        "💡 作物成熟后会收到通知"
    )


async def cmd_crops(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update):
        return
    await ensure_user(update)
    text = "🌱 请选择要种植的作物\n\n用法: /fm_plant 作物名\n\n"
    for name, c in game.CROPS.items():
        t = f"{c['minutes'] / 60}小时" if c["minutes"] >= 60 else f"{c['minutes']}分钟"
        text += f"{c['emoji']} {name} — 种子 {c['seed']} MB | 收获 {c['reward']} MB | {t}\n"
    await update.message.reply_text(text)


async def cmd_farm(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update):
        return
    user = await ensure_user(update)
    uid = user["user_id"]
    plots = await db.get_plots(uid)

    cols = 3
    grid_lines = []
    detail_lines = []
    mature = growing = empty = 0

    for p in plots:
        if not p["crop"]:
            grid_lines.append(("🟫", "空地"))
            empty += 1
        elif p["is_dead"]:
            grid_lines.append(("☠️", "枯死"))
        else:
            crop = game.get_crop(p["crop"])
            if not crop:
                continue
            remain = game.get_remaining_minutes(p["planted_at"], p["effective_minutes"] or crop["minutes"])
            if remain <= 0:
                grid_lines.append((crop["emoji"], "✅"))
                mature += 1
            else:
                emoji = crop["emoji"]
                if p["has_pest"]:
                    emoji = "🐛" if "蛀虫" in (p.get("pest_type") or "") else "💩"
                grid_lines.append((emoji, game.format_time_short(remain)))
                growing += 1

    for p in plots:
        if not p["crop"]:
            continue
        if p["is_dead"]:
            detail_lines.append(f"  ☠️ {p['crop']} — 已枯死")
            continue
        crop = game.get_crop(p["crop"])
        if not crop:
            continue
        remain = game.get_remaining_minutes(p["planted_at"], p["effective_minutes"] or crop["minutes"])
        if remain <= 0:
            detail_lines.append(f"  {crop['emoji']} {p['crop']} — ✅ 已成熟")
        else:
            status = f"⏳ {game.format_time(remain)}"
            if p["has_pest"]:
                status += f" {p.get('pest_type') or '🐛害虫'}!"
            detail_lines.append(f"  {crop['emoji']} {p['crop']} — {status}")

    grid = ""
    for i in range(0, len(grid_lines), cols):
        row = grid_lines[i : i + cols]
        grid += "  ".join(f"[ {e} ]" for e, _ in row) + "\n"
        grid += "  ".join(f" {t:<6}" for _, t in row) + "\n\n"

    text = f"🌾 {user['username']} 的农场  Lv.{user['level']} · {len(plots)}块地\n"
    text += "━━━━━━━━━━━━━━━━━━━━\n\n"
    text += grid
    if detail_lines:
        text += "\n".join(detail_lines) + "\n\n"
    text += f"📊 成熟 {mature} · 生长 {growing} · 空地 {empty}\n"
    text += f"💰 余额: {user['balance']:.1f} MB | 经验: {user['exp']}"
    await update.message.reply_text(text)


async def cmd_plant(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update):
        return
    user = await ensure_user(update)
    crop_name = " ".join(ctx.args) if ctx.args else ""

    if not crop_name:
        text = "🌱 请选择要种植的作物\n\n用法: /fm_plant 作物名\n\n"
        for name, c in game.CROPS.items():
            t = f"{c['minutes'] / 60}小时" if c["minutes"] >= 60 else f"{c['minutes']}分钟"
            text += f"{c['emoji']} {name} — 种子 {c['seed']} MB | 收获 {c['reward']} MB | {t}\n"
        return await update.message.reply_text(text)

    crop = game.get_crop(crop_name)
    if not crop:
        return await update.message.reply_text(f"❌ 未知作物: {crop_name}\n输入 /fm_crops 查看可种植的作物")

    plots = await db.get_plots(user["user_id"])
    empty_plot = next((p for p in plots if not p["crop"]), None)
    if not empty_plot:
        return await update.message.reply_text("❌ 没有空地了！先收获或清理再种植。")

    fresh = await db.get_user(user["user_id"])
    if fresh["balance"] < crop["seed"]:
        return await update.message.reply_text(
            f"❌ 余额不足！种植{crop_name}需要 {crop['seed']} MB，你只有 {fresh['balance']:.1f} MB"
        )

    await db.update_balance(user["user_id"], -crop["seed"])
    await db.plant_crop(user["user_id"], empty_plot["slot"], crop_name, crop["minutes"])

    t = f"{crop['minutes'] / 60}小时" if crop["minutes"] >= 60 else f"{crop['minutes']}分钟"
    await update.message.reply_text(
        f"{crop['emoji']} 成功种植 {crop_name}！\n"
        f"💰 花费 {crop['seed']} MB | 预计 {t} 后成熟\n"
        f"💰 余额: {fresh['balance'] - crop['seed']:.1f} MB"
    )


async def cmd_plantall(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update):
        return
    user = await ensure_user(update)
    crop_name = " ".join(ctx.args) if ctx.args else ""
    if not crop_name:
        return await update.message.reply_text("用法: /fm_plantall 作物名 — 在所有空地种植")

    crop = game.get_crop(crop_name)
    if not crop:
        return await update.message.reply_text(f"❌ 未知作物: {crop_name}")

    plots = await db.get_plots(user["user_id"])
    empty_plots = [p for p in plots if not p["crop"]]
    if not empty_plots:
        return await update.message.reply_text("❌ 没有空地了！")

    planted = 0
    for ep in empty_plots:
        fresh = await db.get_user(user["user_id"])
        if fresh["balance"] < crop["seed"]:
            break
        await db.update_balance(user["user_id"], -crop["seed"])
        await db.plant_crop(user["user_id"], ep["slot"], crop_name, crop["minutes"])
        planted += 1

    final = await db.get_user(user["user_id"])
    await update.message.reply_text(
        f"{crop['emoji']} 批量种植 {crop_name} x{planted}！\n"
        f"💰 共花费 {crop['seed'] * planted} MB\n"
        f"💰 余额: {final['balance']:.1f} MB"
    )


async def cmd_harvest(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update):
        return
    user = await ensure_user(update)
    uid = user["user_id"]
    plots = await db.get_plots(uid)

    total_reward = 0
    total_exp = 0
    harvested = []

    for p in plots:
        if not p["crop"] or p["is_dead"]:
            continue
        crop = game.get_crop(p["crop"])
        if not crop:
            continue
        remain = game.get_remaining_minutes(p["planted_at"], p["effective_minutes"] or crop["minutes"])
        if remain <= 0 and not p["has_pest"]:
            reward = crop["reward"]
            total_reward += reward
            total_exp += math.ceil(crop["seed"] / 2)
            harvested.append({"name": p["crop"], "emoji": crop["emoji"], "reward": reward})
            await db.clear_plot(uid, p["slot"])

    if not harvested:
        hint = ""
        if any(p["crop"] and p["has_pest"] for p in plots):
            hint = "\n💡 有些作物有害虫，先 /fm_clean 清理"
        return await update.message.reply_text(f"🌱 没有成熟的作物可以收获~{hint}\n\n使用 /fm_farm 查看农场状态")

    await db.update_balance(uid, total_reward)
    await db.add_exp(uid, total_exp)
    new_level = await check_level_up(uid)
    fresh = await db.get_user(uid)

    text = "🌾 收获成功！\n\n"
    for h in harvested:
        text += f"  {h['emoji']} {h['name']} — +{h['reward']:.1f} MB\n"
    text += f"\n💰 共获得 {total_reward:.1f} MB 流量\n"
    text += f"💰 余额: {fresh['balance']:.1f} MB | 经验 +{total_exp}"

    # 提示被害虫阻止的成熟作物
    pest_blocked = []
    fresh_plots = await db.get_plots(uid)
    for p in fresh_plots:
        if p["crop"] and not p["is_dead"] and p["has_pest"]:
            crop = game.get_crop(p["crop"])
            if crop:
                remain = game.get_remaining_minutes(p["planted_at"], p["effective_minutes"] or crop["minutes"])
                if remain <= 0:
                    pest_blocked.append(f"{crop['emoji']}{p['crop']}")
    if pest_blocked:
        text += f"\n\n⚠️ 还有 {len(pest_blocked)} 个成熟作物被害虫阻止收获：{', '.join(pest_blocked)}\n用 /fm_clean 清理后再收获"

    if new_level:
        text += f"\n\n🎉 恭喜升级到 Lv.{new_level}！农场扩展到 {fresh['plots']} 块地！"
    await update.message.reply_text(text)


async def cmd_water(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update):
        return
    user = await ensure_user(update)
    now = datetime.now(timezone.utc)
    last = user["last_water"]
    cooldown = 30 * 60

    if last:
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        diff = (now - last).total_seconds()
        if diff < cooldown:
            remain = math.ceil((cooldown - diff) / 60)
            return await update.message.reply_text(f"💧 浇水冷却中，还需等待 {remain} 分钟")

    plots = await db.get_plots(user["user_id"])
    watered = 0
    for p in plots:
        if p["crop"] and not p["is_dead"]:
            crop = game.get_crop(p["crop"])
            if not crop:
                continue
            eff = p["effective_minutes"] or crop["minutes"]
            remain = game.get_remaining_minutes(p["planted_at"], eff)
            if remain > 0:
                await db.water_plot(user["user_id"], p["slot"], eff * 0.1)
                watered += 1

    if watered == 0:
        return await update.message.reply_text("💧 没有需要浇水的作物~")

    await db.set_last_water(user["user_id"])
    await update.message.reply_text(f"💧 浇水成功！为 {watered} 块地浇了水，生长加速10%")


async def cmd_clean(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update):
        return
    user = await ensure_user(update)
    plots = await db.get_plots(user["user_id"])
    cleaned = 0
    for p in plots:
        if p["has_pest"]:
            await db.set_pest(user["user_id"], p["slot"], False)
            cleaned += 1

    if cleaned == 0:
        return await update.message.reply_text("🧹 农场很干净，没有需要清理的~")
    await update.message.reply_text(f"🧹 清理成功！共清理了 {cleaned} 块地的害虫/粪便~")


async def cmd_cleardead(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update):
        return
    user = await ensure_user(update)
    plots = await db.get_plots(user["user_id"])
    cleared = 0
    for p in plots:
        if p["is_dead"]:
            await db.clear_plot(user["user_id"], p["slot"])
            cleared += 1

    if cleared == 0:
        return await update.message.reply_text("🌿 没有枯死的作物~")
    await update.message.reply_text(f"☠️ 清除了 {cleared} 块枯死的作物，土地已恢复")


async def cmd_steal(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update):
        return
    user = await ensure_user(update)
    uid = user["user_id"]
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    steal_count = await db.get_steal_info(uid, today)
    if steal_count >= STEAL_DAILY_LIMIT:
        return await update.message.reply_text(f"🥷 今日偷菜次数已用完 ({STEAL_DAILY_LIMIT}/{STEAL_DAILY_LIMIT})")

    target_plot = await db.get_random_harvestable_plot(uid)
    if not target_plot:
        return await update.message.reply_text("❌ 没有可以偷的成熟作物")

    crop = game.get_crop(target_plot["crop"])
    if not crop:
        return await update.message.reply_text("❌ 没有可以偷的成熟作物")

    remain = game.get_remaining_minutes(target_plot["planted_at"], target_plot["effective_minutes"] or crop["minutes"])
    if remain > 0:
        return await update.message.reply_text("❌ 没有可以偷的成熟作物")

    await db.inc_steal_count(uid, today)
    target_name = target_plot["username"] or "Anonymous"
    new_count = steal_count + 1

    # 30% success
    if random.random() > 0.3:
        fine = round(crop["reward"] * random.uniform(0.1, 0.3), 1)
        await db.update_balance(uid, -fine)
        await update.message.reply_text(
            f"🚨 偷菜失败！被发现了！\n\n"
            f"试图偷 {target_name} 的 {crop['emoji']} {target_plot['crop']}\n"
            f"💸 罚款 {fine:.1f} MB\n"
            f"📊 今日偷菜 {new_count}/{STEAL_DAILY_LIMIT}"
        )
        return

    # success: steal 20% of reward
    stolen = round(crop["reward"] * random.uniform(0.15, 0.25), 1)
    await db.update_balance(uid, stolen)
    await update.message.reply_text(
        f"🥷 偷菜成功！\n\n"
        f"从 {target_name} 的农场偷到了 {crop['emoji']} {target_plot['crop']}\n"
        f"💰 获得 {stolen:.1f} MB\n"
        f"📊 今日偷菜 {new_count}/{STEAL_DAILY_LIMIT}"
    )


async def cmd_balance(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update):
        return
    user = await ensure_user(update)
    info = game.get_level_info(user["level"])
    await update.message.reply_text(
        f"💰 {user['username']} 的资产\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"💵 余额: {user['balance']:.1f} MB\n"
        f"📊 等级: Lv.{user['level']}\n"
        f"⭐ 经验: {user['exp']} / {info['exp_next']}\n"
        f"🌾 农田: {user['plots']} 块"
    )


async def cmd_shop(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update):
        return
    await ensure_user(update)
    await update.message.reply_text(
        "🏪 农场商店\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "💧 /fm_water — 浇水(免费, 30分钟冷却)\n"
        "🧹 /fm_clean — 清理害虫(免费)\n"
        "⬆️ /fm_upgrade — 升级农场\n\n"
        "💡 通过种植收获作物获得经验来升级\n"
        "💡 升级后自动获得更多农田"
    )


async def cmd_upgrade(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update):
        return
    user = await ensure_user(update)
    info = game.get_level_info(user["level"])
    if user["level"] >= 10:
        return await update.message.reply_text("⬆️ 你已经达到最高等级 Lv.10！")

    next_info = game.get_level_info(user["level"] + 1)
    ratio = min(10, int(user["exp"] / info["exp_next"] * 10))
    bar = "▓" * ratio + "░" * (10 - ratio)
    await update.message.reply_text(
        f"⬆️ 农场升级\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
        f"当前: Lv.{user['level']} ({user['plots']}块地)\n"
        f"下一级: Lv.{user['level'] + 1} ({next_info['plots']}块地)\n\n"
        f"经验: {user['exp']} / {info['exp_next']}\n"
        f"{bar}\n\n"
        f"💡 通过收获作物获得经验来升级"
    )


async def cmd_rank(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update):
        return
    await ensure_user(update)
    top = await db.get_top_users(10)
    text = "🏆 农场排行榜\n━━━━━━━━━━━━━━━━━━━━\n\n"
    medals = ["🥇", "🥈", "🥉"]
    for i, u in enumerate(top):
        medal = medals[i] if i < 3 else f"{i + 1}."
        text += f"{medal} {u['username'] or 'Anonymous'} — Lv.{u['level']} | {u['balance']:.1f} MB\n"
    await update.message.reply_text(text)


# ── error handler ────────────────────────────────────────
async def error_handler(update: object, ctx: ContextTypes.DEFAULT_TYPE):
    extra = {"event": "update_error"}
    if isinstance(update, Update):
        extra.update(_build_update_log_context(update))
    logger.error("update.error", exc_info=ctx.error, extra=extra)
    if isinstance(update, Update) and update.message:
        try:
            await update.message.reply_text("❌ 处理命令时出错，请稍后再试")
        except Exception:
            pass


async def post_init(app: Application):
    await db.init()
    await app.bot.set_my_commands([
        BotCommand("fm_start", "开始游戏"),
        BotCommand("fm_farm", "查看农场"),
        BotCommand("fm_crops", "作物列表"),
        BotCommand("fm_plant", "种植作物"),
        BotCommand("fm_plantall", "批量种植"),
        BotCommand("fm_harvest", "收获作物"),
        BotCommand("fm_water", "浇水加速"),
        BotCommand("fm_clean", "清理害虫"),
        BotCommand("fm_cleardead", "清除枯死"),
        BotCommand("fm_steal", "偷菜"),
        BotCommand("fm_balance", "查看余额"),
        BotCommand("fm_shop", "商店"),
        BotCommand("fm_upgrade", "升级农场"),
        BotCommand("fm_rank", "排行榜"),
        BotCommand("fm_refresh", "刷新农场"),
        BotCommand("fm_help", "玩法说明"),
    ])

    jq = app.job_queue
    jq.run_repeating(_wrap_job("job_random_pest", job_random_pest), interval=600, first=60)
    jq.run_repeating(_wrap_job("job_check_pest_death", job_check_pest_death), interval=300, first=120)
    jq.run_repeating(_wrap_job("job_check_mature", job_check_mature), interval=120, first=30)
    logger.info("app.ready", extra={"event": "app_ready"})


async def post_shutdown(app: Application):
    await db.close()
    shutdown_logging()


def main():
    app = Application.builder().token(TOKEN).post_init(post_init).post_shutdown(post_shutdown).build()

    command_map: list[tuple[str, CommandHandlerFn]] = [
        ("fm_start", cmd_start),
        ("fm_help", cmd_help),
        ("fm_crops", cmd_crops),
        ("fm_farm", cmd_farm),
        ("fm_refresh", cmd_farm),
        ("fm_plant", cmd_plant),
        ("fm_plantall", cmd_plantall),
        ("fm_harvest", cmd_harvest),
        ("fm_water", cmd_water),
        ("fm_clean", cmd_clean),
        ("fm_cleardead", cmd_cleardead),
        ("fm_steal", cmd_steal),
        ("fm_balance", cmd_balance),
        ("fm_shop", cmd_shop),
        ("fm_upgrade", cmd_upgrade),
        ("fm_rank", cmd_rank),
    ]
    for command_name, callback in command_map:
        app.add_handler(CommandHandler(command_name, _wrap_command(command_name, callback)))
    app.add_error_handler(error_handler)

    logger.info("app.starting", extra={"event": "app_starting"})
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
