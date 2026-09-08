import asyncio
import base64
import json
from io import BytesIO
from pathlib import Path
from typing import Dict, Iterable, List, Optional
from urllib.parse import quote, urlparse

import httpx
from PIL import Image, ImageDraw, ImageFont


SEARCH_URL = "https://music.163.com/api/search/get/web"
SEARCH_PROXY_URL = "https://web-proxy.apifox.cn/api/v1/request"
FULL_SOURCE_APIS = (
    "https://music.duanjinglin.com/api",
    "https://api.qijieya.cn/meting/",
    "https://meting.naihee.com/api",
)
REQUEST_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Referer": "https://music.163.com/",
}


class MusicVoiceError(Exception):
    pass


def _search_target(keyword: str, limit: int) -> str:
    return (
        "%s?csrf_token=hlpretag=&hlposttag=&s=%s&type=1&offset=0&total=true&limit=%d"
        % (SEARCH_URL, quote(keyword), limit)
    )


def _parse_search(payload: object) -> List[Dict[str, object]]:
    if not isinstance(payload, dict):
        return []
    result = payload.get("result")
    songs = result.get("songs", []) if isinstance(result, dict) else []
    parsed = []
    for song in songs:
        if not isinstance(song, dict) or not isinstance(song.get("id"), int):
            continue
        artists = song.get("artists") or []
        album = song.get("album") or {}
        parsed.append({
            "id": song["id"],
            "name": str(song.get("name") or "未知歌曲"),
            "artists": "/".join(
                str(item.get("name"))
                for item in artists
                if isinstance(item, dict) and item.get("name")
            ),
            "album": str(album.get("name") or "") if isinstance(album, dict) else "",
            "duration": int(song.get("duration") or 0),
        })
    return parsed


async def search_netease(keyword: str, limit: int = 10) -> List[Dict[str, object]]:
    """搜索网易云；直连与上游插件使用的代理并行，采用首个有效响应。"""
    target = _search_target(keyword, limit)

    async def direct() -> List[Dict[str, object]]:
        async with httpx.AsyncClient(timeout=5, headers=REQUEST_HEADERS) as client:
            response = await client.get(target)
            response.raise_for_status()
            return _parse_search(response.json())

    async def proxy() -> List[Dict[str, object]]:
        headers = {
            "api-u": target,
            "api-o0": "method=GET, timings=true, timeout=5000",
            "Content-Type": "application/json",
        }
        async with httpx.AsyncClient(timeout=5) as client:
            response = await client.post(SEARCH_PROXY_URL, headers=headers, content=b"{}")
            response.raise_for_status()
            return _parse_search(response.json())

    tasks = [asyncio.ensure_future(direct()), asyncio.ensure_future(proxy())]
    errors = []
    had_valid_response = False
    try:
        while tasks:
            done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            tasks = list(pending)
            for task in done:
                try:
                    songs = task.result()
                    had_valid_response = True
                    if songs:
                        for remaining in pending:
                            remaining.cancel()
                        return songs
                except Exception as exc:
                    errors.append(exc)
        if had_valid_response:
            return []
        raise MusicVoiceError("网易云搜索失败") from (errors[-1] if errors else None)
    finally:
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)


def _valid_http_url(value: object) -> Optional[str]:
    if not isinstance(value, str):
        return None
    value = value.strip()
    try:
        parsed = urlparse(value)
    except ValueError:
        return None
    return value if parsed.scheme in ("http", "https") and parsed.netloc else None


def parse_source_response(content: str) -> Optional[str]:
    direct = _valid_http_url(content)
    if direct:
        return direct
    try:
        payload = json.loads(content)
    except (TypeError, ValueError):
        return None
    return _valid_http_url(payload.get("url")) if isinstance(payload, dict) else None


async def resolve_song_source(song_id: int) -> str:
    async def fetch(api: str) -> str:
        separator = "&" if "?" in api else "?"
        url = "%s%sserver=netease&type=url&id=%d" % (api, separator, song_id)
        async with httpx.AsyncClient(timeout=5, follow_redirects=True) as client:
            async with client.stream("GET", url, headers=REQUEST_HEADERS) as response:
                response.raise_for_status()
                content_type = response.headers.get("content-type", "").lower()
                if (content_type.startswith("audio/")
                        or content_type.startswith("video/")
                        or "application/octet-stream" in content_type):
                    return str(response.url)

                await response.aread()
                source = parse_source_response(response.text)
                if not source:
                    raise MusicVoiceError("音源接口未返回有效链接")
                return source

    tasks = [asyncio.ensure_future(fetch(api)) for api in FULL_SOURCE_APIS]
    errors = []
    try:
        while tasks:
            done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            tasks = list(pending)
            for task in done:
                try:
                    source = task.result()
                    for remaining in pending:
                        remaining.cancel()
                    return source
                except Exception as exc:
                    errors.append(exc)
        raise MusicVoiceError("所有音源接口均不可用") from (errors[-1] if errors else None)
    finally:
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)


def _font_candidates() -> Iterable[Path]:
    module_dir = Path(__file__).resolve().parent
    yield module_dir.parent / "torrent_analyze_HoshinoBot" / "SourceHanSansSC-Regular.otf"
    yield module_dir.parent / "Blue_Archive_HoshinoBot" / "ba_calendar" / "wqy-microhei.ttc"
    yield Path("C:/Windows/Fonts/msyh.ttc")
    yield Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc")
    yield Path("/usr/share/fonts/truetype/wqy/wqy-microhei.ttc")


def _load_font(size: int):
    for path in _font_candidates():
        if path.is_file():
            try:
                return ImageFont.truetype(str(path), size=size)
            except OSError:
                pass
    return ImageFont.load_default()


def _ellipsize(draw: ImageDraw.ImageDraw, text: str, font, max_width: int) -> str:
    if draw.textbbox((0, 0), text, font=font)[2] <= max_width:
        return text
    suffix = "…"
    while text and draw.textbbox((0, 0), text + suffix, font=font)[2] > max_width:
        text = text[:-1]
    return text + suffix


def _duration_text(milliseconds: object) -> str:
    try:
        seconds = max(0, int(milliseconds) // 1000)
    except (TypeError, ValueError):
        seconds = 0
    return "%d:%02d" % divmod(seconds, 60)


def render_song_list(songs: List[Dict[str, object]]) -> str:
    """生成网易云风格歌单图片，返回 OneBot 可直接发送的 base64 URL。"""
    width = 1040
    row_height = 78
    margin = 42
    header_height = 142
    height = header_height + row_height * len(songs) + 38
    image = Image.new("RGB", (width, height))
    pixels = image.load()
    for y in range(height):
        ratio = y / max(1, height - 1)
        color = (
            int(24 + 5 * ratio),
            int(26 + 4 * ratio),
            int(35 + 10 * ratio),
        )
        for x in range(width):
            pixels[x, y] = color

    draw = ImageDraw.Draw(image)
    title_font = _load_font(36)
    subtitle_font = _load_font(19)
    item_font = _load_font(25)
    meta_font = _load_font(18)
    duration_font = _load_font(18)

    draw.ellipse((margin, 31, margin + 62, 93), fill=(236, 65, 82))
    draw.ellipse((margin + 17, 48, margin + 45, 76), outline="white", width=4)
    draw.ellipse((margin + 27, 58, margin + 35, 66), fill="white")
    draw.text((margin + 82, 27), "网易云音乐", fill=(250, 250, 252), font=title_font)
    draw.text((margin + 84, 77), "发送序号选择歌曲", fill=(157, 163, 178), font=subtitle_font)
    draw.line((margin, 116, width - margin, 116), fill=(61, 65, 78), width=2)

    for index, song in enumerate(songs, 1):
        y = header_height + (index - 1) * row_height
        card_fill = (34, 37, 48) if index % 2 else (31, 34, 44)
        draw.rounded_rectangle(
            (margin, y, width - margin, y + 64), radius=14, fill=card_fill
        )
        draw.rounded_rectangle(
            (margin + 14, y + 11, margin + 58, y + 53), radius=12, fill=(218, 58, 76)
        )
        number = str(index)
        bbox = draw.textbbox((0, 0), number, font=item_font)
        draw.text(
            (margin + 36 - (bbox[2] - bbox[0]) / 2, y + 31 - (bbox[3] - bbox[1]) / 2 - 3),
            number,
            fill="white",
            font=item_font,
        )
        title = _ellipsize(draw, str(song.get("name") or ""), item_font, 690)
        meta = "%s · %s" % (song.get("artists") or "未知歌手", song.get("album") or "未知专辑")
        meta = _ellipsize(draw, meta, meta_font, 690)
        draw.text((margin + 78, y + 5), title, fill=(244, 245, 249), font=item_font)
        draw.text((margin + 78, y + 38), meta, fill=(153, 159, 175), font=meta_font)
        duration = _duration_text(song.get("duration"))
        duration_box = draw.textbbox((0, 0), duration, font=duration_font)
        draw.text(
            (width - margin - 22 - (duration_box[2] - duration_box[0]), y + 23),
            duration,
            fill=(126, 132, 148),
            font=duration_font,
        )

    buffer = BytesIO()
    image.save(buffer, "PNG", optimize=True)
    return "base64://" + base64.b64encode(buffer.getvalue()).decode("ascii")
