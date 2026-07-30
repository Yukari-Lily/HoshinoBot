import asyncio
import base64
import json
import os
import random
import re
import time
import traceback
from typing import Optional, Tuple, List

import aiohttp
from hoshino import Service, logger
from hoshino.typing import CQEvent

# ===================== 配置（从 hoshino/config/qiniu_image.py 读取） =====================
try:
    from hoshino.util import load_yaml_config
    _cfg = load_yaml_config('qiniu_image')
except Exception:
    _cfg = {}

API_BASE = _cfg.get("api_base", "https://api.qnaigc.com")
API_KEY = str(_cfg.get("api_key", "") or "").strip()  # 在 hoshino/config/qiniu_image.py 中配置
MODEL = _cfg.get("model", "openai/gpt-image-2")

# 重试与超时
DEFAULT_RETRIES = int(_cfg.get("retries", 3))  # 建议 >= 3
HTTP_TIMEOUT = int(_cfg.get("timeout", 240))

# 并发限制（避免同群多条消息同时生成压垮上游/触发风控）
MAX_CONCURRENCY = int(_cfg.get("concurrency", 1))
_API_SEM = asyncio.Semaphore(MAX_CONCURRENCY)

# 触发词（按长度从长到短匹配，避免“画”抢匹配“给我画”）
TRIGGERS = tuple(sorted(("七牛画", "七牛改", "给我画", "改成", "画", "p"), key=len, reverse=True))
DEFAULT_PROMPT_IF_EMPTY = "高质量、细节丰富、照片风格、高清"

DEFAULT_IMAGE_CONFIG = {
    "quality": "auto",           # low / medium / high / auto
    "size": "auto",              # 1024x1024 / 1536x1024 / 1024x1536 / 2048x2048 / auto
}

sv = Service("qiniu-image")

# ===================== CQ 解析工具 =====================
IMG_CQ_RE = re.compile(r"\[CQ:image,(?P<kv>[^\]]+)\]")
KV_RE = re.compile(r"([^,=]+)=([^,]+)")
REPLY_RE = re.compile(r"\[CQ:reply,(?:[^,\]]*,)?id=(?P<id>[^,\]]+)\]")

# ===================== 去重（解决重复投递/多实例导致的重复处理） =====================
_DEDUP_TTL = int(_cfg.get("dedup_ttl", 20))  # 秒
_recent_msg = {}  # str(message_id) -> ts

def _dedup_hit(ev: CQEvent) -> bool:
    mid = getattr(ev, "message_id", None)
    if mid is None:
        return False
    mid = str(mid)

    now = time.time()
    # 清理过期
    for k, ts in list(_recent_msg.items()):
        if now - ts > _DEDUP_TTL:
            _recent_msg.pop(k, None)

    if mid in _recent_msg:
        return True
    _recent_msg[mid] = now
    return False

# ===================== 通用工具函数 =====================
def _extract_plain_text(ev: CQEvent) -> str:
    try:
        return ev.message.extract_plain_text().strip()
    except Exception:
        return re.sub(r"\[CQ:[^\]]+\]", "", str(ev.message)).strip()

def _first_image_from_cq_string(cq_text: str) -> Tuple[Optional[str], Optional[str]]:
    """
    从 CQ 码字符串中提取第一张图片。
    返回 (image_url_or_base64, raw_file)
    """
    m = IMG_CQ_RE.search(cq_text)
    if not m:
        return None, None
    kvs = dict(KV_RE.findall(m.group("kv")))
    url = kvs.get("url")
    file_ = kvs.get("file")
    if url:
        return url, file_
    if file_:
        if file_.startswith(("http://", "https://")):
            return file_, file_
        if file_.startswith("base64://"):
            return file_, file_
    return None, file_

async def _get_reply_image_url(bot, reply_id: str) -> Optional[str]:
    """通过 OneBot 的 get_msg 查询被回复消息里的第一张图片 URL/base64。"""
    for mid in (reply_id, None):
        try:
            real_id = mid if mid is not None else int(reply_id)
            resp = await bot.get_msg(message_id=real_id)

            message = resp.get("message")
            if isinstance(message, str):
                url, _ = _first_image_from_cq_string(message)
                if url:
                    return url
            if isinstance(message, list):
                for seg in message:
                    if seg.get("type") == "image":
                        data = seg.get("data", {}) or {}
                        if data.get("url"):
                            return data["url"]
                        f = data.get("file")
                        if isinstance(f, str) and (f.startswith("http") or f.startswith("base64://")):
                            return f

            raw = resp.get("raw_message")
            if isinstance(raw, str):
                url, _ = _first_image_from_cq_string(raw)
                if url:
                    return url
        except Exception:
            continue
    return None

def _as_data_uri(image: str) -> Optional[str]:
    """
    将 napcat 的 image 表达（url 或 base64://xxx）转换为上游可接受字段：
    - url: 原样返回
    - base64://xxxx: 转成 data:image/png;base64,xxxx（多数服务接受；若上游要求更严格可改 MIME）
    """
    if not image:
        return None
    if image.startswith(("http://", "https://")):
        return image
    if image.startswith("base64://"):
        b64 = image[len("base64://"):]
        return f"data:image/png;base64,{b64}"
    return None

def _imgs_to_cq(img_b64_list: List[str]) -> str:
    """将 b64 列表打包为 QQ 图片 CQ 码串。"""
    return "".join(f"[CQ:image,file=base64://{b64}]" for b64 in img_b64_list)

async def _download_to_b64(session: aiohttp.ClientSession, url: str) -> str:
    async with session.get(url, timeout=HTTP_TIMEOUT) as resp:
        resp.raise_for_status()
        content = await resp.read()
    return base64.b64encode(content).decode("utf-8")

class QiniuApiError(RuntimeError):
    """4xx 等不可重试的 API 错误。"""
    def __init__(self, status: int, text: str):
        self.status = status
        self.text = text
        super().__init__(f"HTTP {status}: {text[:400]}")


class QiniuSafetyError(QiniuApiError):
    """安全系统拒绝（涉黄/违规内容），不可重试，需向用户返回专门提示。"""


async def _post_json_with_retry(
    session: aiohttp.ClientSession,
    path: str,
    payload: dict,
    retries: int = DEFAULT_RETRIES,
) -> dict:
    url = f"{API_BASE}{path}"
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json",
    }

    last_err: Optional[Exception] = None
    last_text: str = ""

    for attempt in range(1, max(1, retries) + 1):
        try:
            async with session.post(url, json=payload, headers=headers, timeout=HTTP_TIMEOUT) as resp:
                text = await resp.text()
                last_text = text

                # 429/5xx 视为可重试（上游抖动/限流/拥塞）
                if resp.status in (429, 500, 502, 503, 504):
                    raise RuntimeError(f"HTTP {resp.status} (retryable): {text[:400]}")

                if resp.status // 100 != 2:
                    # 4xx 等永久性错误：不重试。安全系统拒绝单独标记。
                    if "safety" in text.lower():
                        raise QiniuSafetyError(resp.status, text)
                    raise QiniuApiError(resp.status, text)

                try:
                    return json.loads(text)
                except Exception:
                    raise RuntimeError(f"JSON 解析失败，响应前 400 字符：{text[:400]}")

        except QiniuApiError:
            # 4xx 永久性错误（含安全拒绝）：立即抛出，不重试
            raise
        except Exception as e:
            # 网络错误 / 429 / 5xx：可重试
            last_err = e
            if attempt < retries:
                # 退避：基础等待 + 少量抖动，避免群里并发一起撞上游
                sleep_s = min(6.0, 0.8 * attempt) + random.random() * 0.3
                await asyncio.sleep(sleep_s)
                continue

    # 把最后一次响应片段带上，方便定位
    raise RuntimeError(f"API 请求失败：{last_err} | last_resp={last_text[:200]}")

# ===================== 七牛 API 封装 =====================
async def qiniu_text_to_image(prompt: str) -> List[str]:
    payload = {
        "model": MODEL,
        "prompt": prompt or DEFAULT_PROMPT_IF_EMPTY,
        **DEFAULT_IMAGE_CONFIG,
    }

    async with aiohttp.ClientSession() as session:
        data = await _post_json_with_retry(session, "/v1/images/generations", payload)

        imgs: List[str] = []
        for it in (data.get("data") or []):
            if it.get("b64_json"):
                imgs.append(it["b64_json"])
            elif it.get("url"):
                imgs.append(await _download_to_b64(session, it["url"]))

    if not imgs:
        raise RuntimeError("生成成功但未返回图片数据")
    return imgs

async def qiniu_image_to_image(image: str, prompt: str) -> List[str]:
    image_field = _as_data_uri(image)
    if not image_field:
        raise RuntimeError("未能识别输入图片（仅支持 url 或 base64://）")

    payload = {
        "model": MODEL,
        "image": [image_field],
        "prompt": prompt or DEFAULT_PROMPT_IF_EMPTY,
        "quality": DEFAULT_IMAGE_CONFIG["quality"],
    }

    async with aiohttp.ClientSession() as session:
        data = await _post_json_with_retry(session, "/v1/images/edits", payload)

        imgs: List[str] = []
        for it in (data.get("data") or []):
            if it.get("b64_json"):
                imgs.append(it["b64_json"])
            elif it.get("url"):
                imgs.append(await _download_to_b64(session, it["url"]))

    if not imgs:
        raise RuntimeError("图生图成功但未返回图片数据")
    return imgs

# ===================== 指令入口 =====================
@sv.on_message()
async def handle_qiniu_image(bot, ev: CQEvent):
    # 0) 没配置 KEY 直接报错（避免无意义请求）
    if not API_KEY:
        await bot.send(ev, "生成失败喵（未配置 QINIU_API_KEY）")
        return

    # 1) 忽略自身消息（防止回环；不同实现字段可能不同，尽力处理）
    try:
        if str(getattr(ev, "user_id", "")) == str(getattr(bot, "self_id", "")):
            return
    except Exception:
        pass

    # 2) 去重：同一 message_id 短时间只处理一次（关键修复）
    if _dedup_hit(ev):
        return

    # 3) 触发词匹配
    plain = _extract_plain_text(ev)
    trigger = None
    for t in TRIGGERS:
        if plain.startswith(t):
            trigger = t
            break
    if not trigger:
        return

    prompt = plain[len(trigger):].strip()

    # 4) 当前消息段里找图片 / reply
    img_url: Optional[str] = None
    reply_id: Optional[str] = None
    try:
        for seg in ev.message:
            st = getattr(seg, "type", None)
            data = getattr(seg, "data", {}) or {}
            if st == "image" and not img_url:
                u = data.get("url") or data.get("file")
                if isinstance(u, str) and (u.startswith("http") or u.startswith("base64://")):
                    img_url = u
            elif st == "reply" and not reply_id:
                reply_id = data.get("id") or data.get("message_id") or data.get("reply_seq")
    except Exception:
        pass

    # 5) 兜底：从 CQ 文本再扒一遍图片 / reply
    if not img_url:
        url, _ = _first_image_from_cq_string(str(ev.message))
        if url:
            img_url = url
    if not reply_id:
        m = REPLY_RE.search(str(ev.message))
        if m:
            reply_id = m.group("id")

    # 6) 如果是回复消息：拉取被回复消息中的第一张图片
    if not img_url and reply_id:
        img_url = await _get_reply_image_url(bot, str(reply_id))

    # 7) 调用上游（加并发限制，降低 500/429）
    try:
        async with _API_SEM:
            if img_url:
                images = await qiniu_image_to_image(img_url, prompt)
            else:
                images = await qiniu_text_to_image(prompt or DEFAULT_PROMPT_IF_EMPTY)

        await bot.send(ev, _imgs_to_cq(images[:1]))

    except QiniuSafetyError as e:
        # 安全系统拒绝（涉黄/违规）：不重试，给用户专门提示
        logger.warning(
            "qiniu-image rejected by safety | pid=%s mid=%s gid=%s uid=%s model=%s status=%s",
            os.getpid(),
            getattr(ev, "message_id", None),
            getattr(ev, "group_id", None),
            getattr(ev, "user_id", None),
            MODEL,
            e.status,
        )
        await bot.send(ev, "生成失败喵，太色了喵")
    except Exception as e:
        # 关键：把异常打到日志里，否则你只会看到"生成失败喵"
        logger.error(
            "qiniu-image failed | pid=%s mid=%s gid=%s uid=%s model=%s err=%r\n%s",
            os.getpid(),
            getattr(ev, "message_id", None),
            getattr(ev, "group_id", None),
            getattr(ev, "user_id", None),
            MODEL,
            e,
            traceback.format_exc(),
        )
        await bot.send(ev, "生成失败喵")
