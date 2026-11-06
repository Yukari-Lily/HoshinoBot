# -*- coding: utf-8 -*-
import asyncio
import base64
import re
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

import aiohttp
try:
    import pytz
    SHANGHAI_TZ = pytz.timezone("Asia/Shanghai")
except Exception:
    from datetime import timezone, timedelta
    SHANGHAI_TZ = timezone(timedelta(hours=8))


class DailyNewsManager:
    """每天 60 秒读懂世界 数据管理器"""

    DEFAULT_API_URLS = [
        "https://60s.viki.moe/v2/60s",
        "https://api.guiguiya.com/api/60s?type=json",
        "https://60s-api.viki.moe/v2/60s",
    ]

    # 可信的高速 GitHub/jsDelivr 镜像源前缀模板
    CDN_MIRROR_TEMPLATES = [
        "https://testingcf.jsdelivr.net/gh/{owner}/{repo}@{branch}/{path}",
        "https://fastly.jsdelivr.net/gh/{owner}/{repo}@{branch}/{path}",
        "https://cdn.jsdelivr.net/gh/{owner}/{repo}@{branch}/{path}",
        "https://gcore.jsdelivr.net/gh/{owner}/{repo}@{branch}/{path}",
    ]

    def __init__(self, config: Dict[str, Any], logger=None):
        self.config = config
        self.logger = logger
        self._cache: Optional[Dict[str, Any]] = None
        self._cache_time: float = 0.0
        self._cache_cq: Optional[str] = None

    @property
    def api_urls(self) -> List[str]:
        configured = self.config.get("api_urls")
        if configured and isinstance(configured, list):
            return configured
        return self.DEFAULT_API_URLS

    @property
    def timeout(self) -> aiohttp.ClientTimeout:
        return aiohttp.ClientTimeout(total=15, connect=6)

    @staticmethod
    def _today_str() -> str:
        return datetime.now(SHANGHAI_TZ).strftime("%Y-%m-%d")

    @classmethod
    def _normalize_data(cls, raw: Any) -> Optional[Dict[str, Any]]:
        """归一化不同 API 的数据结构为统一格式"""
        if not isinstance(raw, dict):
            return None

        # 针对 {"code": 200, "data": ...} 或 {"success": true, "data": ...}
        data = raw.get("data") if ("data" in raw and isinstance(raw["data"], dict)) else raw
        if not isinstance(data, dict):
            return None

        date = data.get("date") or cls._today_str()

        # 解析新闻列表（兼容纯文本列表与对象列表 {"title": "..."}）
        raw_news = data.get("news", [])
        if not isinstance(raw_news, list):
            return None

        news: List[str] = []
        for item in raw_news:
            if isinstance(item, str) and item.strip():
                news.append(item.strip())
            elif isinstance(item, dict) and "title" in item and item["title"]:
                news.append(str(item["title"]).strip())

        if not news:
            return None

        # 解析图片链接
        image = data.get("image") or data.get("imageUrl") or data.get("img") or ""
        if not isinstance(image, str):
            image = ""

        # 解析每日微语
        tip = data.get("tip") or data.get("weiyu") or ""
        if not isinstance(tip, str):
            tip = ""

        # 解析星期与农历
        day_of_week = data.get("day_of_week") or ""
        if not day_of_week:
            weekdays = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]
            try:
                dt = datetime.strptime(date, "%Y-%m-%d")
                day_of_week = weekdays[dt.weekday()]
            except Exception:
                day_of_week = ""

        lunar_date = data.get("lunar_date") or ""

        return {
            "date": date,
            "day_of_week": day_of_week,
            "lunar_date": lunar_date,
            "news": news,
            "tip": tip,
            "image": image,
        }

    @classmethod
    def get_candidate_image_urls(cls, image_url: str) -> List[str]:
        """为 GitHub / jsdelivr 类图片生成多镜像源候选列表"""
        candidates: List[str] = []
        if not image_url:
            return candidates

        # 匹配 /gh/{owner}/{repo}@{branch}/{path} 或 /gh/{owner}/{repo}/{path}
        match = re.search(r"/gh/([^/@]+)/([^/@]+)(?:@([^/]+))?/(.+)", image_url)
        if match:
            owner, repo, branch, path = match.group(1), match.group(2), match.group(3) or "main", match.group(4)
            for tpl in cls.CDN_MIRROR_TEMPLATES:
                mirror_url = tpl.format(owner=owner, repo=repo, branch=branch, path=path)
                if mirror_url not in candidates:
                    candidates.append(mirror_url)

        # 加入原始 URL 作为候选（若包含已知故障的 cdn.jsdmirror.com 则放在末尾备用）
        if image_url not in candidates:
            if "cdn.jsdmirror.com" in image_url:
                candidates.append(image_url)
            else:
                candidates.insert(0, image_url)

        return candidates

    async def fetch_data(self) -> Optional[Dict[str, Any]]:
        """获取 60s 数据（当天 1 小时内使用内存缓存，支持多源容灾）"""
        now = time.time()
        today = self._today_str()

        if self._cache and self._cache.get("date") == today and (now - self._cache_time < 3600):
            return self._cache

        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) HoshinoBot/daily_news"}
        async with aiohttp.ClientSession(headers=headers, timeout=self.timeout) as session:
            for url in self.api_urls:
                try:
                    async with session.get(url) as resp:
                        if resp.status == 200:
                            raw = await resp.json()
                            norm = self._normalize_data(raw)
                            if norm:
                                self._cache = norm
                                self._cache_time = now
                                self._cache_cq = None
                                return self._cache
                        else:
                            if self.logger:
                                self.logger.warning(f"请求 {url} 失败: HTTP {resp.status}")
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

        # 优雅拼接副标题（处理缺失星期或农历的情况）
        parts = []
        if day_of_week:
            parts.append(day_of_week)
        if lunar:
            parts.append(f"农历 {lunar}")
        sub_title = f"【{date} {' | '.join(parts)}】" if parts else f"【{date}】"

        header = f"📅 每天 60 秒读懂世界\n{sub_title}\n"
        items = "\n".join(f"{i}. {str(item).strip()}" for i, item in enumerate(news_list, 1))
        footer = f"\n\n💡【微语】{tip.strip()}" if tip else ""

        return header + items + footer

    async def get_image_cq(self, image_url: str) -> Optional[str]:
        """下载长图并转为 Base64 CQ 码（智能路由多 CDN 镜像源）"""
        if not image_url:
            return None
        if self._cache_cq:
            return self._cache_cq

        candidates = self.get_candidate_image_urls(image_url)
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) HoshinoBot/daily_news",
            "Referer": "https://60s.viki.moe/",
        }

        async with aiohttp.ClientSession(headers=headers, timeout=self.timeout) as session:
            for url in candidates:
                try:
                    async with session.get(url) as resp:
                        if resp.status == 200:
                            content = await resp.read()
                            if content:
                                b64 = base64.b64encode(content).decode("ascii")
                                self._cache_cq = f"[CQ:image,file=base64://{b64}]"
                                return self._cache_cq
                        else:
                            if self.logger:
                                self.logger.debug(f"尝试下载新闻图片 ({url}) 返回 HTTP {resp.status}")
                except Exception as e:
                    if self.logger:
                        self.logger.debug(f"尝试下载新闻图片 ({url}) 异常: {e}")

        if self.logger:
            self.logger.warning(f"所有候选镜像均无法下载 60s 图片 ({image_url})")

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
