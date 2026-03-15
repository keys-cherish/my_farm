require('dotenv').config();

const TelegramBot = require('node-telegram-bot-api');
const DB = require('./db');
const G = require('./game');

const TOKEN = process.env.BOT_TOKEN;
if (!TOKEN) {
  console.error('Please set BOT_TOKEN in .env file');
  process.exit(1);
}

const bot = new TelegramBot(TOKEN, { polling: true });

// --- Helper ---
async function ensureUser(msg) {
  const uid = msg.from.id;
  const uname = msg.from.first_name || msg.from.username || '';
  let user = await DB.getUser(uid);
  if (!user) {
    user = await DB.createUser(uid, uname);
  } else {
    await DB.updateUsername(uid, uname);
  }
  return user;
}

async function checkLevelUp(userId) {
  const user = await DB.getUser(userId);
  const info = G.getLevelInfo(user.level);
  if (user.exp >= info.expNext && user.level < 10) {
    const newLevel = user.level + 1;
    const newInfo = G.getLevelInfo(newLevel);
    await DB.setLevel(userId, newLevel, newInfo.plots);
    for (let i = 0; i < newInfo.plots; i++) {
      await DB.addPlot(userId, i);
    }
    return newLevel;
  }
  return null;
}

async function randomPestCheck(userId) {
  const plots = await DB.getPlots(userId);
  for (const p of plots) {
    if (p.crop && !p.is_dead && !p.has_pest) {
      if (Math.random() < G.PEST_CHANCE) {
        await DB.setPest(userId, p.slot, true);
      }
    }
  }
}

async function checkDeadPlots(userId) {
  const plots = await DB.getPlots(userId);
  for (const p of plots) {
    if (p.crop && !p.is_dead) {
      const crop = G.getCrop(p.crop);
      if (!crop) continue;
      const since = G.getMinutesSinceMaturity(p.planted_at, crop.minutes);
      if (since > G.DEAD_OVERTIME_HOURS * 60) {
        await DB.setDead(userId, p.slot);
      }
    }
  }
}

// --- /start ---
bot.onText(/\/start/, async (msg) => {
  const user = await ensureUser(msg);
  bot.sendMessage(msg.chat.id,
    `🌾 欢迎来到农场！\n\n` +
    `你获得了 ${user.balance} MB 启动资金和 ${user.plots} 块地。\n` +
    `输入 /help 查看玩法说明。`
  );
});

// --- /help ---
bot.onText(/\/help/, (msg) => {
  bot.sendMessage(msg.chat.id,
    `🌾 农场玩法说明\n` +
    `━━━━━━━━━━━━━━━━━━━━\n\n` +
    `🌱 /plant 作物名 — 种植作物\n` +
    `🌾 /farm — 查看农场状态\n` +
    `🥬 /crops — 查看作物列表\n` +
    `📦 /harvest — 收获成熟作物\n` +
    `💧 /water — 给作物浇水(加速10%)\n` +
    `🧹 /clean — 清理害虫/杂草\n` +
    `☠️ /cleardead — 清除枯死作物\n` +
    `🏪 /shop — 商店\n` +
    `⬆️ /upgrade — 升级农场\n` +
    `💰 /balance — 查看余额\n` +
    `🏆 /rank — 排行榜\n` +
    `🔄 /refresh — 刷新农场状态\n\n` +
    `💡 种植作物需要花费种子费用(MB)\n` +
    `💡 作物成熟后及时收获，超过24小时会枯死\n` +
    `💡 浇水可以加速10%生长\n` +
    `💡 随机出现害虫需要清理，否则影响收获`
  );
});

// --- /crops ---
bot.onText(/\/crops/, async (msg) => {
  await ensureUser(msg);
  let text = `🌱 请选择要种植的作物\n\n用法: /plant 作物名\n\n`;
  for (const [name, c] of Object.entries(G.CROPS)) {
    const hours = c.minutes >= 60 ? `${c.minutes / 60}小时` : `${c.minutes}分钟`;
    text += `${c.emoji} ${name} — 种子 ${c.seed} MB | 收获 ${c.reward} MB | ${hours}\n`;
  }
  bot.sendMessage(msg.chat.id, text);
});

// --- /farm ---
bot.onText(/\/farm|\/refresh/, async (msg) => {
  const user = await ensureUser(msg);
  await checkDeadPlots(user.user_id);
  await randomPestCheck(user.user_id);
  const plots = await DB.getPlots(user.user_id);

  const cols = 3;
  const gridLines = [];
  const detailLines = [];
  let mature = 0, growing = 0, empty = 0;

  for (let i = 0; i < plots.length; i++) {
    const p = plots[i];
    let cellEmoji, timeStr;

    if (!p.crop) {
      cellEmoji = '🟫';
      timeStr = '空地';
      empty++;
    } else if (p.is_dead) {
      cellEmoji = '☠️';
      timeStr = '枯死';
    } else {
      const crop = G.getCrop(p.crop);
      if (!crop) { cellEmoji = '❓'; timeStr = '?'; continue; }
      cellEmoji = crop.emoji;
      let remain = G.getRemainingMinutes(p.planted_at, crop.minutes);
      remain = remain * Math.pow(0.9, p.water_count);
      if (remain <= 0) {
        timeStr = '✅';
        mature++;
      } else {
        timeStr = G.formatTimeShort(remain);
        growing++;
        if (p.has_pest) cellEmoji = '🐛';
      }
    }

    gridLines.push({ emoji: cellEmoji, time: timeStr });
  }

  let grid = '';
  for (let i = 0; i < gridLines.length; i += cols) {
    let emojiRow = '';
    let timeRow = '';
    for (let j = i; j < Math.min(i + cols, gridLines.length); j++) {
      emojiRow += `[ ${gridLines[j].emoji} ]  `;
      timeRow += ` ${gridLines[j].time.padEnd(6)}  `;
    }
    grid += emojiRow.trim() + '\n' + timeRow.trim() + '\n\n';
  }

  for (const p of plots) {
    if (!p.crop) continue;
    if (p.is_dead) {
      detailLines.push(`  ☠️ ${p.crop} — 已枯死`);
      continue;
    }
    const crop = G.getCrop(p.crop);
    if (!crop) continue;
    let remain = G.getRemainingMinutes(p.planted_at, crop.minutes);
    remain = remain * Math.pow(0.9, p.water_count);
    if (remain <= 0) {
      detailLines.push(`  ${crop.emoji} ${p.crop} — ✅ 已成熟`);
    } else {
      let status = `⏳ ${G.formatTime(remain)}`;
      if (p.has_pest) status += ' 🐛害虫!';
      detailLines.push(`  ${crop.emoji} ${p.crop} — ${status}`);
    }
  }

  let text = `🌾 ${user.username} 的农场  Lv.${user.level} · ${plots.length}块地\n`;
  text += `━━━━━━━━━━━━━━━━━━━━\n\n`;
  text += grid;
  if (detailLines.length > 0) text += detailLines.join('\n') + '\n\n';
  text += `📊 成熟 ${mature} · 生长 ${growing} · 空地 ${empty}\n`;
  text += `💰 余额: ${user.balance.toFixed(1)} MB | 经验: ${user.exp}`;

  bot.sendMessage(msg.chat.id, text);
});

// --- /plant ---
bot.onText(/\/plant(?:@\w+)?\s*(.*)/, async (msg, match) => {
  const user = await ensureUser(msg);
  const cropName = (match[1] || '').trim();

  if (!cropName) {
    let text = `🌱 请选择要种植的作物\n\n用法: /plant 作物名\n\n`;
    for (const [name, c] of Object.entries(G.CROPS)) {
      const hours = c.minutes >= 60 ? `${c.minutes / 60}小时` : `${c.minutes}分钟`;
      text += `${c.emoji} ${name} — 种子 ${c.seed} MB | 收获 ${c.reward} MB | ${hours}\n`;
    }
    return bot.sendMessage(msg.chat.id, text);
  }

  const crop = G.getCrop(cropName);
  if (!crop) {
    return bot.sendMessage(msg.chat.id, `❌ 未知作物: ${cropName}\n输入 /crops 查看可种植的作物`);
  }

  const plots = await DB.getPlots(user.user_id);
  const emptyPlot = plots.find(p => !p.crop);

  if (!emptyPlot) {
    return bot.sendMessage(msg.chat.id, `❌ 没有空地了！先收获或清理再种植。`);
  }

  const freshUser = await DB.getUser(user.user_id);
  if (freshUser.balance < crop.seed) {
    return bot.sendMessage(msg.chat.id, `❌ 余额不足！种植${cropName}需要 ${crop.seed} MB，你只有 ${freshUser.balance.toFixed(1)} MB`);
  }

  await DB.updateBalance(user.user_id, -crop.seed);
  await DB.plantCrop(user.user_id, emptyPlot.slot, cropName);

  const hours = crop.minutes >= 60 ? `${crop.minutes / 60}小时` : `${crop.minutes}分钟`;
  bot.sendMessage(msg.chat.id,
    `${crop.emoji} 成功种植 ${cropName}！\n` +
    `💰 花费 ${crop.seed} MB | 预计 ${hours} 后成熟\n` +
    `💰 余额: ${(freshUser.balance - crop.seed).toFixed(1)} MB`
  );
});

// --- /plantall ---
bot.onText(/\/plantall(?:@\w+)?\s*(.*)/, async (msg, match) => {
  const user = await ensureUser(msg);
  const cropName = (match[1] || '').trim();

  if (!cropName) {
    return bot.sendMessage(msg.chat.id, `用法: /plantall 作物名 — 在所有空地种植`);
  }

  const crop = G.getCrop(cropName);
  if (!crop) {
    return bot.sendMessage(msg.chat.id, `❌ 未知作物: ${cropName}`);
  }

  const plots = await DB.getPlots(user.user_id);
  const emptyPlots = plots.filter(p => !p.crop);

  if (emptyPlots.length === 0) {
    return bot.sendMessage(msg.chat.id, `❌ 没有空地了！`);
  }

  let planted = 0;
  for (const ep of emptyPlots) {
    const freshUser = await DB.getUser(user.user_id);
    if (freshUser.balance < crop.seed) break;
    await DB.updateBalance(user.user_id, -crop.seed);
    await DB.plantCrop(user.user_id, ep.slot, cropName);
    planted++;
  }

  const finalUser = await DB.getUser(user.user_id);
  bot.sendMessage(msg.chat.id,
    `${crop.emoji} 批量种植 ${cropName} x${planted}！\n` +
    `💰 共花费 ${crop.seed * planted} MB\n` +
    `💰 余额: ${finalUser.balance.toFixed(1)} MB`
  );
});

// --- /harvest ---
bot.onText(/\/harvest/, async (msg) => {
  const user = await ensureUser(msg);
  await checkDeadPlots(user.user_id);
  const plots = await DB.getPlots(user.user_id);

  let totalReward = 0;
  let totalExp = 0;
  const harvested = [];

  for (const p of plots) {
    if (!p.crop || p.is_dead) continue;
    const crop = G.getCrop(p.crop);
    if (!crop) continue;

    let remain = G.getRemainingMinutes(p.planted_at, crop.minutes);
    remain = remain * Math.pow(0.9, p.water_count);

    if (remain <= 0 && !p.has_pest) {
      totalReward += crop.reward;
      totalExp += Math.ceil(crop.seed / 2);
      harvested.push({ name: p.crop, emoji: crop.emoji, reward: crop.reward });
      await DB.clearPlot(user.user_id, p.slot);
    }
  }

  if (harvested.length === 0) {
    let hint = '';
    const hasPest = plots.some(p => p.crop && p.has_pest);
    if (hasPest) hint = '\n💡 有些作物有害虫，先 /clean 清理';
    return bot.sendMessage(msg.chat.id, `📦 没有可收获的作物~${hint}`);
  }

  await DB.updateBalance(user.user_id, totalReward);
  await DB.addExp(user.user_id, totalExp);

  const newLevel = await checkLevelUp(user.user_id);
  const freshUser = await DB.getUser(user.user_id);

  let text = `📦 收获成功！\n\n`;
  const grouped = {};
  for (const h of harvested) {
    if (!grouped[h.name]) grouped[h.name] = { emoji: h.emoji, count: 0, reward: 0 };
    grouped[h.name].count++;
    grouped[h.name].reward += h.reward;
  }
  for (const [name, info] of Object.entries(grouped)) {
    text += `${info.emoji} ${name} x${info.count} → +${info.reward} MB\n`;
  }
  text += `\n💰 总收入: +${totalReward} MB | 经验 +${totalExp}\n`;
  text += `💰 余额: ${freshUser.balance.toFixed(1)} MB`;

  if (newLevel) {
    text += `\n\n🎉 恭喜升级到 Lv.${newLevel}！农场扩展到 ${freshUser.plots} 块地！`;
  }

  bot.sendMessage(msg.chat.id, text);
});

// --- /water ---
bot.onText(/\/water/, async (msg) => {
  const user = await ensureUser(msg);
  const now = Date.now();
  const lastWater = user.last_water ? new Date(user.last_water).getTime() : 0;
  const cooldown = 30 * 60 * 1000;

  if (now - lastWater < cooldown) {
    const remain = Math.ceil((cooldown - (now - lastWater)) / 60000);
    return bot.sendMessage(msg.chat.id, `💧 浇水冷却中，还需等待 ${remain} 分钟`);
  }

  const plots = await DB.getPlots(user.user_id);
  let watered = 0;

  for (const p of plots) {
    if (p.crop && !p.is_dead) {
      const crop = G.getCrop(p.crop);
      if (!crop) continue;
      let remain = G.getRemainingMinutes(p.planted_at, crop.minutes);
      remain = remain * Math.pow(0.9, p.water_count);
      if (remain > 0) {
        await DB.waterPlot(user.user_id, p.slot);
        watered++;
      }
    }
  }

  if (watered === 0) {
    return bot.sendMessage(msg.chat.id, `💧 没有需要浇水的作物~`);
  }

  await DB.setLastWater(user.user_id, new Date());
  bot.sendMessage(msg.chat.id, `💧 浇水成功！为 ${watered} 块地浇了水，生长加速10%`);
});

// --- /clean ---
bot.onText(/\/clean/, async (msg) => {
  const user = await ensureUser(msg);
  const plots = await DB.getPlots(user.user_id);
  let cleaned = 0;

  for (const p of plots) {
    if (p.has_pest) {
      await DB.setPest(user.user_id, p.slot, false);
      cleaned++;
    }
  }

  if (cleaned === 0) {
    return bot.sendMessage(msg.chat.id, `🧹 农场很干净，没有需要清理的~`);
  }

  bot.sendMessage(msg.chat.id, `🧹 清理成功！共清理了 ${cleaned} 块地的害虫/粪便~`);
});

// --- /cleardead ---
bot.onText(/\/cleardead/, async (msg) => {
  const user = await ensureUser(msg);
  const plots = await DB.getPlots(user.user_id);
  let cleared = 0;

  for (const p of plots) {
    if (p.is_dead) {
      await DB.clearPlot(user.user_id, p.slot);
      cleared++;
    }
  }

  if (cleared === 0) {
    return bot.sendMessage(msg.chat.id, `🌿 没有枯死的作物~`);
  }

  bot.sendMessage(msg.chat.id, `☠️ 清除了 ${cleared} 块枯死的作物，土地已恢复`);
});

// --- /balance ---
bot.onText(/\/balance/, async (msg) => {
  const user = await ensureUser(msg);
  bot.sendMessage(msg.chat.id,
    `💰 ${user.username} 的资产\n` +
    `━━━━━━━━━━━━━━━━━━━━\n` +
    `💵 余额: ${user.balance.toFixed(1)} MB\n` +
    `📊 等级: Lv.${user.level}\n` +
    `⭐ 经验: ${user.exp} / ${G.getLevelInfo(user.level).expNext}\n` +
    `🌾 农田: ${user.plots} 块`
  );
});

// --- /shop ---
bot.onText(/\/shop/, async (msg) => {
  await ensureUser(msg);
  bot.sendMessage(msg.chat.id,
    `🏪 农场商店\n` +
    `━━━━━━━━━━━━━━━━━━━━\n\n` +
    `💧 /water — 浇水(免费, 30分钟冷却)\n` +
    `🧹 /clean — 清理害虫(免费)\n` +
    `⬆️ /upgrade — 升级农场\n\n` +
    `💡 通过种植收获作物获得经验来升级\n` +
    `💡 升级后自动获得更多农田`
  );
});

// --- /upgrade ---
bot.onText(/\/upgrade/, async (msg) => {
  const user = await ensureUser(msg);
  const info = G.getLevelInfo(user.level);

  if (user.level >= 10) {
    return bot.sendMessage(msg.chat.id, `⬆️ 你已经达到最高等级 Lv.10！`);
  }

  const nextInfo = G.getLevelInfo(user.level + 1);
  bot.sendMessage(msg.chat.id,
    `⬆️ 农场升级\n` +
    `━━━━━━━━━━━━━━━━━━━━\n\n` +
    `当前: Lv.${user.level} (${user.plots}块地)\n` +
    `下一级: Lv.${user.level + 1} (${nextInfo.plots}块地)\n\n` +
    `经验: ${user.exp} / ${info.expNext}\n` +
    `${'▓'.repeat(Math.min(10, Math.floor(user.exp / info.expNext * 10)))}${'░'.repeat(Math.max(0, 10 - Math.floor(user.exp / info.expNext * 10)))}\n\n` +
    `💡 通过收获作物获得经验来升级`
  );
});

// --- /rank ---
bot.onText(/\/rank/, async (msg) => {
  await ensureUser(msg);
  const top = await DB.getTopUsers(10);

  let text = `🏆 农场排行榜\n━━━━━━━━━━━━━━━━━━━━\n\n`;
  const medals = ['🥇', '🥈', '🥉'];

  top.forEach((u, i) => {
    const medal = medals[i] || `${i + 1}.`;
    text += `${medal} ${u.username || 'Anonymous'} — Lv.${u.level} | ${u.balance.toFixed(1)} MB\n`;
  });

  bot.sendMessage(msg.chat.id, text);
});

// --- /steal ---
bot.onText(/\/steal(?:@\w+)?\s*(.*)/, async (msg) => {
  const user = await ensureUser(msg);

  if (Math.random() > 0.3) {
    const fine = Math.floor(Math.random() * 10) + 5;
    await DB.updateBalance(user.user_id, -fine);
    return bot.sendMessage(msg.chat.id, `🚔 偷菜失败！被农场主抓住了，罚款 ${fine} MB`);
  }

  const stolen = Math.floor(Math.random() * 20) + 5;
  await DB.updateBalance(user.user_id, stolen);
  bot.sendMessage(msg.chat.id, `🥷 偷菜成功！获得 ${stolen} MB`);
});

// --- Startup ---
(async () => {
  await DB.init();
  console.log('Database initialized.');
  console.log('Farm bot started! All commands registered.');
})();
