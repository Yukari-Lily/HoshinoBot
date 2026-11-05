# -*- coding: utf-8 -*-
import asyncio
import base64
import time
from datetime import datetime
from typing import Any, Dict, Optional, Tuple

import aiohttp
try:
    import pytz
    SHANGHAI_TZ = pytz.timezone("Asia/Shanghai")
except Exception:
    from datetime import timezone, timedelta
    SHANGHAI_TZ = timezone(timedelta(hours=8))


class DailyNewsManager:
    """每天 60 秒读懂世界 数据管理器"""

    API_URLS = [
        "https://60s.viki.moe/v2/60s",
        "https://60s-api.viki.moe/v2/60s",
    ]

    def __init__(self, config: Dict[str, Any], logger=None):
        self.config = config
        self.logger = logger
        self._cache: Optional[Dict[str, Any]] = None
        self._cache_time: float = 0.0
        self._cache_cq: Optional[str] = None

    @property
    def timeout(self) -> aiohttp.ClientTimeout:
        return aiohttp.ClientTimeout(total=15, connect=8)

    @staticmethod
    def _today_str() -> str:
        return datetime.now(SHANGHAI_TZ).strftime("%Y-%m-%d")

    async def fetch_data(self) -> Optional[Dict[str, Any]]:
        """获取 60s 数据（当天 1 小时内使用内存缓存）"""
        now = time.time()
        today = self._today_str()

        if self._cache and self._cache.get("date") == today and (now - self._cache_time < 3600):
            return self._cache

        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) HoshinoBot/daily_news"}
        async with aiohttp.ClientSession(headers=headers, timeout=self.timeout) as session:
            for url in self.API_URLS:
                try:
                    async with session.get(url) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            if data.get("code") == 200 and "data" in data:
                                self._cache = data["data"]
                                self._cache_time = now
                                self._cache_cq = None
                                return self._cache
                except Exception as e:
                    if self.logger:
                        self.logger.warning(f"请求 {url} 失败: {e}")

        return self._cache

    def format_text(self, data: Dict[str, Any]) -> str:
        """格式化为纯文本简报"""
        date = data.get("date", "")
        day_of_week = data.get("day_of_week", "")
        lunar = data.get("lunar_date", "")
        news_list = data.get("news", [])
        tip = data.get("tip", "")

        header = f"📅 每天 60 秒读懂世界\n【{date} {day_of_week} | 农历 {lunar}】\n"
        items = "\n".join(f"{i}. {item.strip()}" for i, item in enumerate(news_list, 1))
        footer = f"\n\n💡【微语】{tip.strip()}" if tip else ""

        return header + items + footer

    async def get_image_cq(self, image_url: str) -> Optional[str]:
        """下载长图并转为 Base64 CQ 码（避免 QQ 客户端外链拉取失败）"""
        if not image_url:
            return None
        if self._cache_cq:
            return self._cache_cq

        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) HoshinoBot/daily_news",
            "Referer": "https://60s.viki.moe/",
        }
        try:
            async with aiohttp.ClientSession(headers=headers, timeout=self.timeout) as session:
                async with session.get(image_url) as resp:
                    if resp.status == 200:
                        content = await resp.read()
                        b64 = base64.b64encode(content).decode("ascii")
                        self._cache_cq = f"[CQ:image,file=base64://{b64}]"
                        return self._cache_cq
        except Exception as e:
            if self.logger:
                self.logger.error(f"下载新闻图片失败 ({image_url}): {e}")

        return None

    async def get_message(self) -> Optional[str]:
        """获取准备发送的消息（根据配置返回图片或文本，图片失败自动降级纯文本）"""
        data = await self.fetch_data()
        if not data:
            return None

        send_mode = self.config.get("send_mode", "image")
        text_msg = self.format_text(data)

        if send_mode == "text":
            return text_msg

        # image 模式
        image_url = data.get("image")
        if image_url:
            image_cq = await self.get_image_cq(image_url)
            if image_cq:
                return image_cq

        # 图片获取失败时自动降级文本，确保消息正常发送
        if self.logger:
            self.logger.warning("60s 图片获取失败，已自动降级为纯文本发送")
        return text_msg

    async def broadcast(self, sv) -> bool:
        """广播推送到所有开启本服务的群"""
        msg = None
        for attempt in range(1, 4):
            msg = await self.get_message()
            if msg:
                break
            await asyncio.sleep(5)

        if not msg:
            if self.logger:
                self.logger.error("多次重试仍未能获取 60s 新闻，取消本次推送")
            return False

        interval = self.config.get("broadcast_interval", 1.0)
        await sv.broadcast(msg, TAG="每天60秒", interval_time=interval)
        return True
