import asyncio
import base64
import binascii
import json
import os
import random
import re
import time
import traceback
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlsplit

import aiohttp
from hoshino import Service, logger
from hoshino.typing import CQEvent
from hoshino.util import load_yaml_config


# ===================== 配置（从 hoshino/config/qiniu_image.yaml 读取） =====================
try:
    _cfg = load_yaml_config("qiniu_image")
except Exception:
    raise RuntimeError("无法读取 hoshino/config/qiniu_image.yaml，请检查 YAML 格式") from None

if not isinstance(_cfg, dict):
    raise ValueError("hoshino/config/qiniu_image.yaml 的顶层必须是对象")


def _config_string(name: str, default: str, allow_empty: bool = False) -> str:
    value = _cfg.get(name, default)
    if not isinstance(value, str):
        raise ValueError(f"qiniu_image.yaml: {name} 必须是字符串")
    value = value.strip()
    if not value and not allow_empty:
        raise ValueError(f"qiniu_image.yaml: {name} 不能为空")
    return value


def _config_positive_int(name: str, default: int) -> int:
    value = _cfg.get(name, default)
    if isinstance(value, bool):
        raise ValueError(f"qiniu_image.yaml: {name} 必须是正整数")
    try:
        value = int(value)
    except (TypeError, ValueError):
        raise ValueError(f"qiniu_image.yaml: {name} 必须是正整数") from None
    if value <= 0:
        raise ValueError(f"qiniu_image.yaml: {name} 必须大于 0")
    return value


API_BASE = _config_string("api_base", "https://api.qnaigc.com").rstrip("/")
API_KEY = _config_string("api_key", "", allow_empty=True)
MODEL = _config_string("model", "openai/gpt-image-2")

_api_base_parts = urlsplit(API_BASE)
if _api_base_parts.scheme not in ("http", "https") or not _api_base_parts.netloc:
    raise ValueError("qiniu_image.yaml: api_base 必须是有效的 HTTP(S) 地址")

# retries 表示一次指令允许的总请求次数，而非首次请求之外的追加次数。
DEFAULT_RETRIES = _config_positive_int("retries", 3)
HTTP_TIMEOUT = _config_positive_int("timeout", 240)
CONNECT_TIMEOUT = _config_positive_int("connect_timeout", 15)
MAX_CONCURRENCY = _config_positive_int("concurrency", 1)
MAX_DOWNLOAD_BYTES = _config_positive_int("max_download_bytes", 40 * 1024 * 1024)
_DEDUP_TTL = _config_positive_int("dedup_ttl", 20)

IMAGE_CONFIG = {
    "quality": _config_string("quality", "auto"),
    "size": _config_string("size", "auto"),
    "output_format": _config_string("output_format", "png"),
}

GENERATIONS_PATH = "/bypass/openai/v1/images/generations"
EDITS_PATH = "/bypass/openai/v1/images/edits"
DEFAULT_PROMPT_IF_EMPTY = "高质量、细节丰富、照片风格、高清"
REQUEST_TIMEOUT = aiohttp.ClientTimeout(
    total=HTTP_TIMEOUT,
    connect=CONNECT_TIMEOUT,
    sock_connect=CONNECT_TIMEOUT,
)

_API_SEM = asyncio.Semaphore(MAX_CONCURRENCY)

# 触发词按长度从长到短匹配，避免“画”抢匹配“给我画”。
TRIGGERS = tuple(sorted(("七牛画", "七牛改", "给我画", "改成", "画", "p"), key=len, reverse=True))

sv = Service("qiniu-image")

# ===================== CQ 解析工具 =====================
IMG_CQ_RE = re.compile(r"\[CQ:image,(?P<kv>[^\]]+)\]")
KV_RE = re.compile(r"([^,=]+)=([^,]+)")
REPLY_RE = re.compile(r"\[CQ:reply,(?:[^,\]]*,)?id=(?P<id>[^,\]]+)\]")

# ===================== 单进程消息去重 =====================
_recent_msg: Dict[str, float] = {}


def _dedup_hit(ev: CQEvent) -> bool:
    mid = getattr(ev, "message_id", None)
    if mid is None:
        return False
    mid = str(mid)

    now = time.monotonic()
    for key, timestamp in list(_recent_msg.items()):
        if now - timestamp > _DEDUP_TTL:
            _recent_msg.pop(key, None)

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
    """从 CQ 码字符串中提取第一张图片，返回 (可用地址, 原始 file)。"""
    match = IMG_CQ_RE.search(cq_text)
    if not match:
        return None, None
    values = dict(KV_RE.findall(match.group("kv")))
    url = values.get("url")
    file_ = values.get("file")
    if url:
        return url, file_
    if file_ and file_.startswith(("http://", "https://", "base64://")):
        return file_, file_
    return None, file_


async def _get_reply_image_url(bot, reply_id: str) -> Optional[str]:
    """通过 OneBot get_msg 查询被回复消息里的第一张图片 URL/base64。"""
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
                        file_ = data.get("file")
                        if isinstance(file_, str) and file_.startswith(("http", "base64://")):
                            return file_

            raw = resp.get("raw_message")
            if isinstance(raw, str):
                url, _ = _first_image_from_cq_string(raw)
                if url:
                    return url
        except Exception:
            continue
    return None


def _validate_http_url(value: str, field_name: str) -> str:
    parts = urlsplit(value)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise ValueError(f"{field_name} 必须是有效的 HTTP(S) 地址")
    return value


def _decode_base64_image(value: str) -> bytes:
    compact = re.sub(r"\s+", "", value)
    # Base64 编码后最多约为原始数据的 4/3；先限制文本长度，避免无界解码。
    max_encoded_length = ((MAX_DOWNLOAD_BYTES + 2) // 3) * 4
    if not compact or len(compact) > max_encoded_length:
        raise ValueError("图片 Base64 为空或超过大小限制")
    try:
        raw = base64.b64decode(compact, validate=True)
    except (binascii.Error, ValueError):
        raise ValueError("图片 Base64 格式无效") from None
    if not raw or len(raw) > MAX_DOWNLOAD_BYTES:
        raise ValueError("图片为空或超过大小限制")
    return raw


def _image_mime(raw: bytes) -> Optional[str]:
    if raw.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if raw.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if len(raw) >= 12 and raw.startswith(b"RIFF") and raw[8:12] == b"WEBP":
        return "image/webp"
    return None


def _as_image_reference(image: str) -> str:
    """将 OneBot 图片表示转换为 OpenAI Images 的 image_url 值。"""
    if not isinstance(image, str) or not image:
        raise QiniuInputError("未能识别输入图片")
    if image.startswith(("http://", "https://")):
        try:
            return _validate_http_url(image, "输入图片 URL")
        except ValueError as exc:
            raise QiniuInputError(str(exc)) from None
    if image.startswith("base64://"):
        encoded = image[len("base64://"):]
        try:
            raw = _decode_base64_image(encoded)
        except ValueError as exc:
            raise QiniuInputError(str(exc)) from None
        mime = _image_mime(raw)
        if not mime:
            raise QiniuInputError("输入图片格式无效，仅支持 PNG、JPEG 或 WebP")
        compact = re.sub(r"\s+", "", encoded)
        return f"data:{mime};base64,{compact}"
    raise QiniuInputError("未能识别输入图片（仅支持 URL 或 base64://）")


def _imgs_to_cq(img_b64_list: List[str]) -> str:
    """将 Base64 图片列表打包为 QQ 图片 CQ 码串。"""
    return "".join(f"[CQ:image,file=base64://{image}]" for image in img_b64_list)


def _safe_excerpt(value: Any, limit: int = 200) -> str:
    text = str(value or "")
    text = re.sub(r"(?i)bearer\s+\S+", "Bearer ***", text)
    text = re.sub(r"(?i)data:image/[^;]+;base64,[a-z0-9+/=\s]+", "<image data>", text)
    text = re.sub(r"(?i)base64://[a-z0-9+/=\s]+", "base64://<image data>", text)
    text = re.sub(r"https?://[^\s\"'<>]+", "<url>", text)
    text = " ".join(text.split())
    return text[:limit]


def _error_details(text: str) -> Tuple[Optional[str], str]:
    try:
        body = json.loads(text)
    except (TypeError, ValueError):
        return None, "上游返回了非 JSON 错误"

    error = body.get("error", body) if isinstance(body, dict) else body
    if isinstance(error, dict):
        code = error.get("code") or error.get("type")
        message = error.get("message") or error.get("detail") or "上游拒绝了请求"
        return _safe_excerpt(code) or None, _safe_excerpt(message)
    return None, _safe_excerpt(error) or "上游拒绝了请求"


def _looks_like_safety_error(code: Optional[str], message: str) -> bool:
    haystack = f"{code or ''} {message}".lower()
    markers = (
        "safety",
        "moderation",
        "content_policy",
        "policy_violation",
        "content violation",
        "内容审核",
        "违规内容",
    )
    return any(marker in haystack for marker in markers)


def _retry_after_seconds(headers: Any) -> Optional[float]:
    value = headers.get("Retry-After") if headers else None
    if not value:
        return None
    try:
        delay = float(value)
    except (TypeError, ValueError):
        try:
            retry_at = parsedate_to_datetime(value)
            if retry_at.tzinfo is None:
                retry_at = retry_at.replace(tzinfo=timezone.utc)
            delay = (retry_at - datetime.now(timezone.utc)).total_seconds()
        except (TypeError, ValueError, OverflowError):
            return None
    return max(0.0, min(delay, 30.0))


def _backoff_seconds(attempt: int, retry_after: Optional[float] = None) -> float:
    if retry_after is not None:
        return retry_after
    return min(6.0, 0.8 * attempt) + random.random() * 0.3


class QiniuApiError(RuntimeError):
    """上游返回的确定性 API 错误。"""

    def __init__(self, status: int, message: str, code: Optional[str] = None):
        self.status = status
        self.code = code
        self.detail = _safe_excerpt(message)
        suffix = f" code={code}" if code else ""
        super().__init__(f"HTTP {status}{suffix}: {self.detail}")


class QiniuAuthError(QiniuApiError):
    """API Key 无效或没有调用权限。"""


class QiniuRateLimitError(QiniuApiError):
    """上游限流。"""

    def __init__(
        self,
        status: int,
        message: str,
        code: Optional[str] = None,
        retry_after: Optional[float] = None,
    ):
        self.retry_after = retry_after
        super().__init__(status, message, code)


class QiniuSafetyError(QiniuApiError):
    """安全系统拒绝。"""


class QiniuTransientApiError(QiniuApiError):
    """上游暂时性服务错误，可重试。"""

    def __init__(
        self,
        status: int,
        message: str,
        code: Optional[str] = None,
        retry_after: Optional[float] = None,
    ):
        self.retry_after = retry_after
        super().__init__(status, message, code)


class QiniuResponseError(RuntimeError):
    """上游成功响应不符合预期结构。"""


class QiniuRequestUncertainError(RuntimeError):
    """上游请求在多次尝试后仍未完成。"""


class QiniuImageDownloadError(RuntimeError):
    """下载上游返回图片时失败。"""


class QiniuInputError(ValueError):
    """用户提供的输入图片无法转换为上游支持的格式。"""


class _RetryableImageDownloadError(QiniuImageDownloadError):
    """下载图片时发生可安全重试的错误。"""

    def __init__(self, message: str, retry_after: Optional[float] = None):
        self.retry_after = retry_after
        super().__init__(message)


async def _post_json_with_retry(
    session: aiohttp.ClientSession,
    path: str,
    payload: Dict[str, Any],
    attempts: int = DEFAULT_RETRIES,
) -> Dict[str, Any]:
    url = f"{API_BASE}{path}"
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }

    for attempt in range(1, attempts + 1):
        try:
            async with session.post(url, json=payload, headers=headers) as resp:
                if resp.status // 100 == 2:
                    text = await resp.text()
                    code, message = None, ""
                else:
                    raw_error = await resp.content.read(8192)
                    text = raw_error.decode(resp.charset or "utf-8", errors="replace")
                    code, message = _error_details(text)

                if resp.status == 429:
                    raise QiniuRateLimitError(
                        resp.status,
                        message,
                        code,
                        _retry_after_seconds(resp.headers),
                    )

                if 500 <= resp.status < 600:
                    raise QiniuTransientApiError(
                        resp.status,
                        message,
                        code,
                        _retry_after_seconds(resp.headers),
                    )

                if resp.status // 100 != 2:
                    if _looks_like_safety_error(code, message):
                        raise QiniuSafetyError(resp.status, message, code)
                    if resp.status in (401, 403):
                        raise QiniuAuthError(resp.status, message, code)
                    raise QiniuApiError(resp.status, message, code)

                try:
                    data = json.loads(text)
                except (TypeError, ValueError):
                    raise QiniuResponseError("上游返回了无效 JSON") from None
                if not isinstance(data, dict):
                    raise QiniuResponseError("上游响应必须是 JSON 对象")
                _validate_image_response(data)
                return data

        except (QiniuRateLimitError, QiniuTransientApiError) as exc:
            if attempt < attempts:
                await asyncio.sleep(_backoff_seconds(attempt, exc.retry_after))
                continue
            raise
        except QiniuResponseError:
            if attempt < attempts:
                await asyncio.sleep(_backoff_seconds(attempt))
                continue
            raise
        except QiniuApiError:
            raise
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            if attempt < attempts:
                await asyncio.sleep(_backoff_seconds(attempt))
                continue
            raise QiniuRequestUncertainError(
                f"上游请求失败（{type(exc).__name__}），已达到最大尝试次数"
            ) from None

    raise QiniuRequestUncertainError("上游请求未完成")


async def _download_to_b64(session: aiohttp.ClientSession, url: str) -> str:
    try:
        _validate_http_url(url, "响应图片 URL")
    except ValueError as exc:
        raise QiniuImageDownloadError(str(exc)) from None

    for attempt in range(1, DEFAULT_RETRIES + 1):
        try:
            async with session.get(url) as resp:
                if resp.status == 429 or 500 <= resp.status < 600:
                    retry_after = _retry_after_seconds(resp.headers) if resp.status == 429 else None
                    raise _RetryableImageDownloadError(
                        f"图片下载暂时失败：HTTP {resp.status}",
                        retry_after,
                    )
                if resp.status // 100 != 2:
                    raise QiniuImageDownloadError(f"图片下载失败：HTTP {resp.status}")

                content_length = resp.headers.get("Content-Length")
                if content_length:
                    try:
                        if int(content_length) > MAX_DOWNLOAD_BYTES:
                            raise QiniuImageDownloadError("响应图片超过大小限制")
                    except ValueError:
                        pass

                content_type = (resp.headers.get("Content-Type") or "").split(";", 1)[0].lower()
                if content_type and not (
                    content_type.startswith("image/") or content_type == "application/octet-stream"
                ):
                    raise QiniuImageDownloadError(f"响应不是图片（Content-Type: {content_type}）")

                content = bytearray()
                async for chunk in resp.content.iter_chunked(64 * 1024):
                    content.extend(chunk)
                    if len(content) > MAX_DOWNLOAD_BYTES:
                        raise QiniuImageDownloadError("响应图片超过大小限制")
                if not content:
                    raise QiniuImageDownloadError("响应图片为空")
                raw_image = bytes(content)
                if not _image_mime(raw_image):
                    raise QiniuImageDownloadError("响应内容不是支持的 PNG、JPEG 或 WebP 图片")
                return base64.b64encode(raw_image).decode("ascii")

        except _RetryableImageDownloadError as exc:
            if attempt < DEFAULT_RETRIES:
                await asyncio.sleep(_backoff_seconds(attempt, exc.retry_after))
                continue
            raise
        except QiniuImageDownloadError:
            raise
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            if attempt < DEFAULT_RETRIES:
                await asyncio.sleep(_backoff_seconds(attempt))
                continue
            raise QiniuImageDownloadError(
                f"图片下载失败（{type(exc).__name__}）"
            ) from None

    raise QiniuImageDownloadError("图片下载失败")


def _normalize_response_base64(value: str) -> str:
    if value.startswith("data:"):
        marker = ";base64,"
        index = value.find(marker)
        if index < 0:
            raise QiniuResponseError("上游返回了无效的图片 data URI")
        value = value[index + len(marker):]
    try:
        raw = _decode_base64_image(value)
    except ValueError as exc:
        raise QiniuResponseError(str(exc)) from None
    if not _image_mime(raw):
        raise QiniuResponseError("上游返回的 Base64 不是支持的 PNG、JPEG 或 WebP 图片")
    return re.sub(r"\s+", "", value)


def _validate_image_response(data: Dict[str, Any]) -> None:
    """验证并规范图片响应；失败时由调用方按成功率优先策略重试。"""
    items = data.get("data")
    if not isinstance(items, list) or not items:
        raise QiniuResponseError("生成成功但响应中没有图片数据")

    last_error: Optional[QiniuResponseError] = None
    for item in items:
        if not isinstance(item, dict):
            continue
        inline = item.get("b64_json")
        if isinstance(inline, str) and inline:
            try:
                item["b64_json"] = _normalize_response_base64(inline)
                return
            except QiniuResponseError as exc:
                last_error = exc
        url = item.get("url")
        if isinstance(url, str) and url:
            try:
                _validate_http_url(url, "响应图片 URL")
                return
            except ValueError as exc:
                last_error = QiniuResponseError(str(exc))

    if last_error:
        raise last_error
    raise QiniuResponseError("生成成功但响应中没有可用的 b64_json 或 URL")


async def _images_from_response(
    session: aiohttp.ClientSession,
    data: Dict[str, Any],
) -> List[str]:
    items = data.get("data")
    if not isinstance(items, list) or not items:
        raise QiniuResponseError("生成成功但响应中没有图片数据")

    for item in items:
        if not isinstance(item, dict):
            continue
        inline = item.get("b64_json")
        if isinstance(inline, str) and inline:
            return [_normalize_response_base64(inline)]
        url = item.get("url")
        if isinstance(url, str) and url:
            return [await _download_to_b64(session, url)]

    raise QiniuResponseError("生成成功但响应中没有可用的 b64_json 或 URL")


def _base_payload(prompt: str) -> Dict[str, Any]:
    return {
        "model": MODEL,
        "prompt": prompt or DEFAULT_PROMPT_IF_EMPTY,
        **IMAGE_CONFIG,
        "n": 1,
        "stream": False,
    }


# ===================== Modelink OpenAI Images 原厂协议封装 =====================
async def qiniu_text_to_image(prompt: str) -> List[str]:
    async with aiohttp.ClientSession(timeout=REQUEST_TIMEOUT) as session:
        data = await _post_json_with_retry(session, GENERATIONS_PATH, _base_payload(prompt))
        return await _images_from_response(session, data)


async def qiniu_image_to_image(image: str, prompt: str) -> List[str]:
    payload = _base_payload(prompt)
    # Modelink 的 JSON 编辑接口要求使用复数 images；单数 image 会被上游拒绝。
    payload["images"] = [{"image_url": _as_image_reference(image)}]

    async with aiohttp.ClientSession(timeout=REQUEST_TIMEOUT) as session:
        data = await _post_json_with_retry(session, EDITS_PATH, payload)
        return await _images_from_response(session, data)


# ===================== 指令入口 =====================
@sv.on_message()
async def handle_qiniu_image(bot, ev: CQEvent):
    # 忽略自身消息，防止消息回环。
    if str(getattr(ev, "user_id", "")) == str(getattr(bot, "self_id", "")):
        return

    plain = _extract_plain_text(ev)
    trigger = next((item for item in TRIGGERS if plain.startswith(item)), None)
    if not trigger:
        return

    # 只让真正命中本插件的消息进入去重表。
    if _dedup_hit(ev):
        return

    if not API_KEY:
        await bot.send(ev, "生成失败喵（未配置 qiniu_image.yaml 的 api_key）")
        return

    prompt = plain[len(trigger):].strip()

    # 当前消息段里查找图片或回复段。
    img_url: Optional[str] = None
    reply_id: Optional[str] = None
    try:
        for seg in ev.message:
            segment_type = getattr(seg, "type", None)
            segment_data = getattr(seg, "data", {}) or {}
            if segment_type == "image" and not img_url:
                value = segment_data.get("url") or segment_data.get("file")
                if isinstance(value, str) and value.startswith(("http://", "https://", "base64://")):
                    img_url = value
            elif segment_type == "reply" and not reply_id:
                reply_id = (
                    segment_data.get("id")
                    or segment_data.get("message_id")
                    or segment_data.get("reply_seq")
                )
    except Exception:
        pass

    # 兼容仅能取得 CQ 字符串的 OneBot 实现。
    if not img_url:
        img_url, _ = _first_image_from_cq_string(str(ev.message))
    if not reply_id:
        match = REPLY_RE.search(str(ev.message))
        if match:
            reply_id = match.group("id")
    if not img_url and reply_id:
        img_url = await _get_reply_image_url(bot, str(reply_id))

    try:
        async with _API_SEM:
            if img_url:
                images = await qiniu_image_to_image(img_url, prompt)
            else:
                images = await qiniu_text_to_image(prompt)
        await bot.send(ev, _imgs_to_cq(images[:1]))

    except QiniuSafetyError as exc:
        logger.warning(
            "qiniu-image rejected by safety | pid=%s mid=%s gid=%s uid=%s model=%s status=%s code=%s",
            os.getpid(),
            getattr(ev, "message_id", None),
            getattr(ev, "group_id", None),
            getattr(ev, "user_id", None),
            MODEL,
            exc.status,
            exc.code,
        )
        await bot.send(ev, "生成失败喵，太色了喵")
    except QiniuAuthError as exc:
        logger.error(
            "qiniu-image authentication failed | status=%s code=%s model=%s",
            exc.status,
            exc.code,
            MODEL,
        )
        await bot.send(ev, "生成失败喵（API Key 无效或没有模型权限）")
    except QiniuRateLimitError as exc:
        logger.warning(
            "qiniu-image rate limited | status=%s code=%s model=%s",
            exc.status,
            exc.code,
            MODEL,
        )
        await bot.send(ev, "生成服务繁忙喵，请稍后再试")
    except QiniuTransientApiError as exc:
        logger.warning(
            "qiniu-image upstream unavailable | status=%s code=%s model=%s",
            exc.status,
            exc.code,
            MODEL,
        )
        await bot.send(ev, "生成服务暂时不可用喵，请稍后再试")
    except QiniuApiError as exc:
        logger.warning(
            "qiniu-image request rejected | status=%s code=%s detail=%s model=%s",
            exc.status,
            exc.code,
            exc.detail,
            MODEL,
        )
        if exc.status == 400:
            await bot.send(ev, "生成失败喵（请求参数或模型配置无效）")
        else:
            await bot.send(ev, "生成失败喵（上游拒绝了请求）")
    except QiniuRequestUncertainError as exc:
        logger.error("qiniu-image request failed after retries | model=%s reason=%s", MODEL, exc)
        await bot.send(ev, "生成失败喵（多次重试仍未成功）")
    except QiniuResponseError as exc:
        logger.error("qiniu-image invalid response after retries | model=%s reason=%s", MODEL, exc)
        await bot.send(ev, "生成失败喵（上游响应异常，多次重试仍未成功）")
    except QiniuInputError as exc:
        logger.warning("qiniu-image invalid input | model=%s reason=%s", MODEL, exc)
        await bot.send(ev, "生成失败喵（输入图片无效、格式不支持或文件过大）")
    except Exception as exc:
        # 只记录异常类型和堆栈位置，不记录可能包含 URL/Base64 的异常正文。
        logger.error(
            "qiniu-image failed | pid=%s mid=%s gid=%s uid=%s model=%s error_type=%s\n%s",
            os.getpid(),
            getattr(ev, "message_id", None),
            getattr(ev, "group_id", None),
            getattr(ev, "user_id", None),
            MODEL,
            type(exc).__name__,
            "".join(traceback.format_tb(exc.__traceback__)),
        )
        await bot.send(ev, "生成失败喵")
