import os
import math
import random
from datetime import datetime, timezone

from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

import db
import game

load_dotenv()

TOKEN = os.getenv("BOT_TOKEN")
if not TOKEN:
    raise SystemExit("Please set BOT_TOKEN in .env")


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


async def random_pest_check(user_id: int):
    plots = await db.get_plots(user_id)
    for p in plots:
        if p["crop"] and not p["is_dead"] and not p["has_pest"]:
            if random.random() < game.PEST_CHANCE:
                await db.set_pest(user_id, p["slot"], True)


async def check_dead_plots(user_id: int):
    plots = await db.get_plots(user_id)
    for p in plots:
        if p["crop"] and not p["is_dead"]:
            crop = game.get_crop(p["crop"])
            if not crop:
                continue
            since = game.get_minutes_since_maturity(p["planted_at"], crop["minutes"])
            if since > game.DEAD_OVERTIME_HOURS * 60:
                await db.set_dead(user_id, p["slot"])


# ── commands ─────────────────────────────────────────────
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = await ensure_user(update)
    await update.message.reply_text(
        f"🌾 欢迎来到农场！\n\n"
        f"你获得了 {user['balance']} MB 启动资金和 {user['plots']} 块地。\n"
        f"输入 /help 查看玩法说明。"
    )


async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🌾 农场玩法说明\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "🌱 /plant 作物名 — 种植作物\n"
        "🌾 /farm — 查看农场状态\n"
        "🥬 /crops — 查看作物列表\n"
        "📦 /harvest — 收获成熟作物\n"
        "💧 /water — 给作物浇水(加速10%)\n"
        "🧹 /clean — 清理害虫/杂草\n"
        "☠️ /cleardead — 清除枯死作物\n"
        "🏪 /shop — 商店\n"
        "⬆️ /upgrade — 升级农场\n"
        "💰 /balance — 查看余额\n"
        "🏆 /rank — 排行榜\n"
        "🔄 /refresh — 刷新农场状态\n\n"
        "💡 种植作物需要花费种子费用(MB)\n"
        "💡 作物成熟后及时收获，超过24小时会枯死\n"
        "💡 浇水可以加速10%生长\n"
        "💡 随机出现害虫需要清理，否则影响收获"
    )


async def cmd_crops(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await ensure_user(update)
    text = "🌱 请选择要种植的作物\n\n用法: /plant 作物名\n\n"
    for name, c in game.CROPS.items():
        t = f"{c['minutes'] / 60}小时" if c["minutes"] >= 60 else f"{c['minutes']}分钟"
        text += f"{c['emoji']} {name} — 种子 {c['seed']} MB | 收获 {c['reward']} MB | {t}\n"
    await update.message.reply_text(text)


async def cmd_farm(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = await ensure_user(update)
    uid = user["user_id"]
    await check_dead_plots(uid)
    await random_pest_check(uid)
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
            remain = game.get_remaining_minutes(p["planted_at"], crop["minutes"])
            remain *= 0.9 ** p["water_count"]
            if remain <= 0:
                grid_lines.append((crop["emoji"], "✅"))
                mature += 1
            else:
                emoji = "🐛" if p["has_pest"] else crop["emoji"]
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
        remain = game.get_remaining_minutes(p["planted_at"], crop["minutes"])
        remain *= 0.9 ** p["water_count"]
        if remain <= 0:
            detail_lines.append(f"  {crop['emoji']} {p['crop']} — ✅ 已成熟")
        else:
            status = f"⏳ {game.format_time(remain)}"
            if p["has_pest"]:
                status += " 🐛害虫!"
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
    user = await ensure_user(update)
    crop_name = " ".join(ctx.args) if ctx.args else ""

    if not crop_name:
        text = "🌱 请选择要种植的作物\n\n用法: /plant 作物名\n\n"
        for name, c in game.CROPS.items():
            t = f"{c['minutes'] / 60}小时" if c["minutes"] >= 60 else f"{c['minutes']}分钟"
            text += f"{c['emoji']} {name} — 种子 {c['seed']} MB | 收获 {c['reward']} MB | {t}\n"
        return await update.message.reply_text(text)

    crop = game.get_crop(crop_name)
    if not crop:
        return await update.message.reply_text(f"❌ 未知作物: {crop_name}\n输入 /crops 查看可种植的作物")

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
    await db.plant_crop(user["user_id"], empty_plot["slot"], crop_name)

    t = f"{crop['minutes'] / 60}小时" if crop["minutes"] >= 60 else f"{crop['minutes']}分钟"
    await update.message.reply_text(
        f"{crop['emoji']} 成功种植 {crop_name}！\n"
        f"💰 花费 {crop['seed']} MB | 预计 {t} 后成熟\n"
        f"💰 余额: {fresh['balance'] - crop['seed']:.1f} MB"
    )


async def cmd_plantall(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = await ensure_user(update)
    crop_name = " ".join(ctx.args) if ctx.args else ""
    if not crop_name:
        return await update.message.reply_text("用法: /plantall 作物名 — 在所有空地种植")

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
        await db.plant_crop(user["user_id"], ep["slot"], crop_name)
        planted += 1

    final = await db.get_user(user["user_id"])
    await update.message.reply_text(
        f"{crop['emoji']} 批量种植 {crop_name} x{planted}！\n"
        f"💰 共花费 {crop['seed'] * planted} MB\n"
        f"💰 余额: {final['balance']:.1f} MB"
    )


async def cmd_harvest(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = await ensure_user(update)
    uid = user["user_id"]
    await check_dead_plots(uid)
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
        remain = game.get_remaining_minutes(p["planted_at"], crop["minutes"])
        remain *= 0.9 ** p["water_count"]
        if remain <= 0 and not p["has_pest"]:
            total_reward += crop["reward"]
            total_exp += math.ceil(crop["seed"] / 2)
            harvested.append({"name": p["crop"], "emoji": crop["emoji"], "reward": crop["reward"]})
            await db.clear_plot(uid, p["slot"])

    if not harvested:
        hint = ""
        if any(p["crop"] and p["has_pest"] for p in plots):
            hint = "\n💡 有些作物有害虫，先 /clean 清理"
        return await update.message.reply_text(f"📦 没有可收获的作物~{hint}")

    await db.update_balance(uid, total_reward)
    await db.add_exp(uid, total_exp)
    new_level = await check_level_up(uid)
    fresh = await db.get_user(uid)

    text = "📦 收获成功！\n\n"
    grouped = {}
    for h in harvested:
        g = grouped.setdefault(h["name"], {"emoji": h["emoji"], "count": 0, "reward": 0})
        g["count"] += 1
        g["reward"] += h["reward"]
    for name, info in grouped.items():
        text += f"{info['emoji']} {name} x{info['count']} → +{info['reward']} MB\n"
    text += f"\n💰 总收入: +{total_reward} MB | 经验 +{total_exp}\n"
    text += f"💰 余额: {fresh['balance']:.1f} MB"
    if new_level:
        text += f"\n\n🎉 恭喜升级到 Lv.{new_level}！农场扩展到 {fresh['plots']} 块地！"
    await update.message.reply_text(text)


async def cmd_water(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = await ensure_user(update)
    now = datetime.now(timezone.utc)
    last = user["last_water"]
    cooldown = 30 * 60  # seconds

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
            remain = game.get_remaining_minutes(p["planted_at"], crop["minutes"])
            remain *= 0.9 ** p["water_count"]
            if remain > 0:
                await db.water_plot(user["user_id"], p["slot"])
                watered += 1

    if watered == 0:
        return await update.message.reply_text("💧 没有需要浇水的作物~")

    await db.set_last_water(user["user_id"])
    await update.message.reply_text(f"💧 浇水成功！为 {watered} 块地浇了水，生长加速10%")


async def cmd_clean(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
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


async def cmd_balance(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
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
    await ensure_user(update)
    await update.message.reply_text(
        "🏪 农场商店\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "💧 /water — 浇水(免费, 30分钟冷却)\n"
        "🧹 /clean — 清理害虫(免费)\n"
        "⬆️ /upgrade — 升级农场\n\n"
        "💡 通过种植收获作物获得经验来升级\n"
        "💡 升级后自动获得更多农田"
    )


async def cmd_upgrade(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
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
    await ensure_user(update)
    top = await db.get_top_users(10)
    text = "🏆 农场排行榜\n━━━━━━━━━━━━━━━━━━━━\n\n"
    medals = ["🥇", "🥈", "🥉"]
    for i, u in enumerate(top):
        medal = medals[i] if i < 3 else f"{i + 1}."
        text += f"{medal} {u['username'] or 'Anonymous'} — Lv.{u['level']} | {u['balance']:.1f} MB\n"
    await update.message.reply_text(text)


async def cmd_steal(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = await ensure_user(update)
    if random.random() > 0.3:
        fine = random.randint(5, 15)
        await db.update_balance(user["user_id"], -fine)
        return await update.message.reply_text(f"🚔 偷菜失败！被农场主抓住了，罚款 {fine} MB")
    stolen = random.randint(5, 25)
    await db.update_balance(user["user_id"], stolen)
    await update.message.reply_text(f"🥷 偷菜成功！获得 {stolen} MB")


# ── app lifecycle ────────────────────────────────────────
async def post_init(app: Application):
    await db.init()
    print("Database initialized.")


async def post_shutdown(app: Application):
    await db.close()


def main():
    app = Application.builder().token(TOKEN).post_init(post_init).post_shutdown(post_shutdown).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("crops", cmd_crops))
    app.add_handler(CommandHandler("farm", cmd_farm))
    app.add_handler(CommandHandler("refresh", cmd_farm))
    app.add_handler(CommandHandler("plant", cmd_plant))
    app.add_handler(CommandHandler("plantall", cmd_plantall))
    app.add_handler(CommandHandler("harvest", cmd_harvest))
    app.add_handler(CommandHandler("water", cmd_water))
    app.add_handler(CommandHandler("clean", cmd_clean))
    app.add_handler(CommandHandler("cleardead", cmd_cleardead))
    app.add_handler(CommandHandler("balance", cmd_balance))
    app.add_handler(CommandHandler("shop", cmd_shop))
    app.add_handler(CommandHandler("upgrade", cmd_upgrade))
    app.add_handler(CommandHandler("rank", cmd_rank))
    app.add_handler(CommandHandler("steal", cmd_steal))

    print("Farm bot starting...")
    app.run_polling()


if __name__ == "__main__":
    main()
