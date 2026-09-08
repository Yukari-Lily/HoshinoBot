import html
import json
import re
from typing import Dict, Iterable, List, Optional, Tuple

import httpx


USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

TYPE_NAMES = {
    "video": "视频",
    "live": "直播",
    "episode": "番剧集",
    "season": "番剧集",
    "media": "番剧集",
    "space": "空间",
    "opus": "动态",
    "article": "专栏",
    "audio": "音乐",
    "audio_menu": "歌单",
}

PATTERNS = (
    ("video", re.compile(r"(?<![A-Za-z0-9])((?:av\d+)|(?:BV1[0-9A-Za-z]+))", re.I)),
    ("live", re.compile(r"live\.bilibili\.com(?:/h5)?/(\d+)", re.I)),
    ("episode", re.compile(r"bilibili\.com/bangumi/play/(ep\d+)", re.I)),
    ("season", re.compile(r"bilibili\.com/bangumi/play/(ss\d+)", re.I)),
    ("media", re.compile(r"bilibili\.com/bangumi/media/(md\d+)", re.I)),
    ("space", re.compile(r"(?:space\.bilibili\.com/|bilibili\.com/space/)(\d+)", re.I)),
    ("opus", re.compile(r"bilibili\.com/opus/(\d+)", re.I)),
    ("article", re.compile(r"bilibili\.com/read/(?:cv|mobile(?:\?id=|/))(\d+)", re.I)),
    ("audio", re.compile(r"bilibili\.com/audio/au(\d+)", re.I)),
    ("audio_menu", re.compile(r"bilibili\.com/audio/am(\d+)", re.I)),
    ("short", re.compile(r"(?:b23\.tv|bili(?:22|23|33)\.cn)/(?:\\)?([0-9A-Za-z]+)", re.I)),
)


def _walk_strings(value: object) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _walk_strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk_strings(item)


def extract_message_content(message) -> str:
    """提取文本和 QQ 小程序 JSON 中的链接。"""
    parts = [message.extract_plain_text()]
    for segment in message:
        if segment.type != "json":
            continue
        raw = html.unescape(str(segment.data.get("data") or ""))
        try:
            payload = json.loads(raw)
        except (TypeError, ValueError):
            parts.append(raw)
            continue
        parts.extend(value for value in _walk_strings(payload) if "bili" in value or "b23.tv" in value)
    return "\n".join(parts)


def parse_links(content: str, video_full_url: bool = False) -> List[Tuple[str, str]]:
    found = []
    for link_type, pattern in PATTERNS:
        if link_type == "video" and video_full_url:
            pattern = re.compile(
                r"bilibili\.com/video/((?:av\d+)|(?:BV1[0-9A-Za-z]+))", re.I
            )
        for match in pattern.finditer(content):
            found.append((match.start(), link_type, match.group(1)))

    found.sort(key=lambda item: item[0])
    unique = []
    seen = set()
    for _, link_type, identifier in found:
        key = (link_type, identifier.lower())
        if key not in seen:
            seen.add(key)
            unique.append((link_type, identifier))
    return unique


def _api_url(link_type: str, identifier: str) -> Optional[str]:
    number = re.sub(r"^[a-z]+", "", identifier, flags=re.I)
    if link_type == "video":
        key = "aid" if identifier.lower().startswith("av") else "bvid"
        value = number if key == "aid" else identifier
        return "https://api.bilibili.com/x/web-interface/view?%s=%s" % (key, value)
    if link_type == "live":
        return "https://api.live.bilibili.com/room/v1/Room/get_info?room_id=" + identifier
    if link_type == "episode":
        return "https://api.bilibili.com/pgc/view/web/season?ep_id=" + number
    if link_type == "season":
        return "https://api.bilibili.com/pgc/view/web/season?season_id=" + number
    if link_type == "space":
        return "https://api.bilibili.com/x/polymer/web-dynamic/v1/feed/space?host_mid=" + identifier
    if link_type == "opus":
        return "https://api.bilibili.com/x/polymer/web-dynamic/v1/detail?id=" + identifier
    if link_type == "article":
        return "https://api.bilibili.com/x/article/viewinfo?id=" + identifier
    if link_type == "audio":
        return "https://www.bilibili.com/audio/music-service-c/web/song/info?sid=" + identifier
    if link_type == "audio_menu":
        return "https://www.bilibili.com/audio/music-service-c/web/menu/info?sid=" + identifier
    return None


async def resolve_short(client: httpx.AsyncClient, identifier: str) -> Optional[str]:
    response = await client.get("https://b23.tv/" + identifier, follow_redirects=True)
    response.raise_for_status()
    return str(response.url)


async def fetch_link(client: httpx.AsyncClient, link_type: str, identifier: str) -> Dict[str, object]:
    if link_type == "media":
        media_id = re.sub(r"^md", "", identifier, flags=re.I)
        response = await client.get(
            "https://api.bilibili.com/pgc/review/user?media_id=" + media_id
        )
        response.raise_for_status()
        media_payload = response.json()
        media = (media_payload.get("result") or {}).get("media") or {}
        season_id = media.get("season_id")
        if not season_id:
            return media_payload
        response = await client.get(
            "https://api.bilibili.com/pgc/view/web/season?season_id=" + str(season_id)
        )
    else:
        url = _api_url(link_type, identifier)
        if not url:
            raise ValueError("不支持的链接类型")
        response = await client.get(url)
    response.raise_for_status()
    return response.json()


def _dig(data: object, *keys, default=""):
    current = data
    for key in keys:
        if isinstance(current, dict):
            current = current.get(key)
        elif isinstance(current, list) and isinstance(key, int) and 0 <= key < len(current):
            current = current[key]
        else:
            return default
        if current is None:
            return default
    return current


def _image(url: object) -> str:
    if not isinstance(url, str) or not url.strip():
        return ""
    url = url.strip()
    if url.startswith("//"):
        url = "https:" + url
    escaped = (url.replace("&", "&amp;").replace("[", "&#91;")
               .replace("]", "&#93;").replace(",", "&#44;"))
    return "[CQ:image,file=%s]" % escaped


def _compact(lines: Iterable[object]) -> str:
    return "\n".join(str(line).strip() for line in lines if str(line).strip())


def _truncate(value: object, length: int = 35) -> str:
    text = str(value or "")
    return text if len(text) <= length else text[:length] + "…"


def render_result(link_type: str, identifier: str, payload: Dict[str, object]) -> str:
    if int(payload.get("code", 0)) != 0:
        message = payload.get("message") or payload.get("msg") or "未知错误"
        raise ValueError(str(message))
    data = payload.get("data")
    if link_type in ("episode", "season", "media"):
        data = payload.get("result") or data
    if not isinstance(data, dict):
        raise ValueError("接口未返回有效数据")

    if link_type == "video":
        return _compact((data.get("title"), "b23.tv/" + str(data.get("bvid") or identifier)))

    if link_type == "live":
        statuses = {0: "未开播", 1: "直播中", 2: "轮播中"}
        status = statuses.get(data.get("live_status"), "未知状态")
        return _compact(("[%s]%s" % (status, data.get("title") or ""),
                         "https://live.bilibili.com/" + str(data.get("room_id") or identifier)))

    if link_type in ("season", "media"):
        score = _dig(data, "rating", "score")
        title = str(data.get("season_title") or "") + (("（%s分）" % score) if score != "" else "")
        return _compact((title, _image(data.get("cover")),
                         "https://www.bilibili.com/bangumi/media/md" + str(data.get("media_id") or re.sub(r"^md", "", identifier, flags=re.I))))

    if link_type == "episode":
        ep_id = int(re.sub(r"^ep", "", identifier, flags=re.I))
        episodes = data.get("episodes") or []
        episode = next((item for item in episodes if int(item.get("ep_id") or item.get("id") or 0) == ep_id), {})
        return _compact((
            data.get("season_title"),
            _image(episode.get("cover")),
            "第 %s 话 - %s" % (episode.get("title") or "", episode.get("long_title") or ""),
            _truncate(data.get("evaluate"), 35),
            "https://www.bilibili.com/bangumi/play/ep" + str(episode.get("ep_id") or episode.get("id") or ep_id),
        ))

    if link_type == "audio":
        return _compact((data.get("title"), "https://www.bilibili.com/audio/au" + str(data.get("id") or identifier)))

    if link_type == "audio_menu":
        return _compact((data.get("title"), "https://www.bilibili.com/audio/am" + str(data.get("menuId") or identifier)))

    if link_type == "article":
        return _compact((data.get("title"), "https://www.bilibili.com/read/cv" + re.sub(r"^cv", "", identifier, flags=re.I)))

    if link_type == "opus":
        item = data.get("item") or {}
        author = _dig(item, "modules", "module_author", default={})
        dynamic = _dig(item, "modules", "module_dynamic", default={})
        text = _dig(dynamic, "desc", "text") or _dig(dynamic, "major", "article", "title")
        return _compact((
            str(author.get("name") or "") + "的动态",
            _image(author.get("face")),
            text,
            "https://www.bilibili.com/opus/" + str(item.get("id_str") or identifier),
        ))

    if link_type == "space":
        item = _dig(data, "items", 0, default={})
        author = _dig(item, "modules", "module_author", default={})
        return _compact((
            str(author.get("name") or "") + " 的个人空间",
            _image(author.get("face")),
            "https://space.bilibili.com/" + str(author.get("mid") or identifier),
        ))

    raise ValueError("不支持的链接类型")


async def parse_and_render(content: str, parse_limit: int = 3, video_full_url: bool = False) -> List[str]:
    links = parse_links(content, video_full_url=video_full_url)
    headers = {"User-Agent": USER_AGENT, "Referer": "https://www.bilibili.com/"}
    results = []
    async with httpx.AsyncClient(timeout=10, headers=headers) as client:
        expanded = []
        for link_type, identifier in links:
            if link_type != "short":
                expanded.append((link_type, identifier))
                continue
            try:
                target = await resolve_short(client, identifier)
                expanded.extend(parse_links(target or "", video_full_url=video_full_url))
            except Exception:
                continue

        seen = set()
        for link_type, identifier in expanded:
            key = (link_type, identifier.lower())
            if key in seen:
                continue
            seen.add(key)
            if len(results) >= max(1, parse_limit):
                break
            try:
                payload = await fetch_link(client, link_type, identifier)
                results.append(render_result(link_type, identifier, payload))
            except Exception as exc:
                results.append("获取 ID 为 %s 的%s时发生错误：%s。" % (
                    identifier, TYPE_NAMES.get(link_type, "链接"), exc
                ))
    return results
