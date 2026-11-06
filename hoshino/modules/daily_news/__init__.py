# -*- coding: utf-8 -*-
from hoshino import Service, priv
from hoshino.typing import CQEvent
from hoshino.util import load_yaml_config

from .core import DailyNewsManager

DEFAULT_CONFIG = {
    "push_time": "08:00",
    "send_mode": "image",
    "broadcast_interval": 1.0,
    "api_urls": [
        "https://60s.viki.moe/v2/60s",
        "https://api.guiguiya.com/api/60s?type=json",
        "https://60s-api.viki.moe/v2/60s",
    ],
}

config = DEFAULT_CONFIG.copy()
config.update(load_yaml_config("daily_news", {}))

sv = Service(
    "daily-60s",
    use_priv=priv.NORMAL,
    manage_priv=priv.ADMIN,
    visible=True,
    enable_on_default=False,
    help_="[每天 60 秒读懂世界]\n发送 news 或 新闻 获取今日简报",
    bundle="每日推送",
)

manager = DailyNewsManager(config, logger=sv.logger)

# 解析每天推送时间 (默认 08:00)
push_str = str(config.get("push_time", "08:00")).strip()
try:
    h, m = [int(x) for x in push_str.split(":")[:2]]
except Exception:
    h, m = 8, 0


# 定时每天 8 点推送
@sv.scheduled_job("cron", hour=h, minute=m)
async def daily_push_60s():
    sv.logger.info(f"开始执行每天 60 秒读懂世界定时推送 ({h:02d}:{m:02d})")
    await manager.broadcast(sv)


# 触发指令：发送 news 或 新闻
@sv.on_fullmatch(["news", "新闻"])
async def send_60s(bot, ev: CQEvent):
    msg = await manager.get_message()
    if not msg:
        await bot.send(ev, "获取今日 60 秒读懂世界失败，请稍后重试~")
        return
    await bot.send(ev, msg)
