import asyncio
import base64
import json
from io import BytesIO
from pathlib import Path
from typing import Dict, Iterable, List, Optional
from urllib.parse import quote, urlparse

import httpx
from PIL import Image, ImageDraw, ImageFilter, ImageFont


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


def render_song_list(songs: List[Dict[str, object]], keyword: str = "") -> str:
    """生成海报式歌单图片，返回 OneBot 可直接发送的 base64 URL。"""
    width = 1080
    row_height = 82
    margin = 54
    panel_top = 248
    panel_padding = 22
    footer_height = 82
    panel_height = panel_padding * 2 + row_height * len(songs)
    height = panel_top + panel_height + footer_height
    image = Image.new("RGB", (width, height))
    pixels = image.load()
    for y in range(height):
        ratio = y / max(1, height - 1)
        color = (
            int(19 + 10 * ratio),
            int(20 + 7 * ratio),
            int(34 + 16 * ratio),
        )
        for x in range(width):
            pixels[x, y] = color

    glow = Image.new("RGBA", image.size, (0, 0, 0, 0))
    glow_draw = ImageDraw.Draw(glow)
    glow_draw.ellipse((-160, -180, 440, 420), fill=(226, 55, 101, 105))
    glow_draw.ellipse((760, -230, 1260, 300), fill=(91, 75, 220, 95))
    glow_draw.ellipse((730, height - 330, 1250, height + 140), fill=(34, 128, 190, 45))
    glow = glow.filter(ImageFilter.GaussianBlur(105))
    image = Image.alpha_composite(image.convert("RGBA"), glow)

    overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
    overlay_draw = ImageDraw.Draw(overlay)
    overlay_draw.rounded_rectangle(
        (margin, panel_top, width - margin, panel_top + panel_height),
        radius=30,
        fill=(255, 255, 255, 17),
        outline=(255, 255, 255, 25),
        width=2,
    )
    image = Image.alpha_composite(image, overlay)
    draw = ImageDraw.Draw(image)

    title_font = _load_font(47)
    subtitle_font = _load_font(20)
    item_font = _load_font(26)
    meta_font = _load_font(18)
    duration_font = _load_font(18)
    index_font = _load_font(19)

    display_keyword = keyword.strip() or "歌曲搜索"
    display_keyword = _ellipsize(draw, "“%s”" % display_keyword, title_font, 700)
    draw.text((margin, 65), display_keyword, fill=(251, 250, 253), font=title_font)
    draw.text(
        (margin + 2, 134),
        "网易云音乐  ·  %d 首搜索结果" % len(songs),
        fill=(180, 181, 196),
        font=subtitle_font,
    )

    disc_x, disc_y = width - margin - 94, 102
    draw.ellipse((disc_x - 66, disc_y - 66, disc_x + 66, disc_y + 66), fill=(24, 25, 36, 220))
    draw.ellipse((disc_x - 52, disc_y - 52, disc_x + 52, disc_y + 52), outline=(96, 94, 117), width=2)
    draw.ellipse((disc_x - 35, disc_y - 35, disc_x + 35, disc_y + 35), outline=(72, 72, 92), width=2)
    draw.ellipse((disc_x - 18, disc_y - 18, disc_x + 18, disc_y + 18), fill=(239, 68, 98))
    draw.ellipse((disc_x - 5, disc_y - 5, disc_x + 5, disc_y + 5), fill=(248, 222, 226))

    for index, song in enumerate(songs, 1):
        y = panel_top + panel_padding + (index - 1) * row_height
        if index > 1:
            draw.line(
                (margin + 40, y, width - margin - 40, y),
                fill=(70, 70, 86),
                width=1,
            )
        number = "%02d" % index
        draw.text(
            (margin + 35, y + 27),
            number,
            fill=(245, 78, 108),
            font=index_font,
        )
        title = _ellipsize(draw, str(song.get("name") or ""), item_font, 690)
        meta = "%s · %s" % (song.get("artists") or "未知歌手", song.get("album") or "未知专辑")
        meta = _ellipsize(draw, meta, meta_font, 690)
        draw.text((margin + 105, y + 10), title, fill=(244, 244, 248), font=item_font)
        draw.text((margin + 105, y + 47), meta, fill=(147, 150, 169), font=meta_font)
        duration = _duration_text(song.get("duration"))
        duration_box = draw.textbbox((0, 0), duration, font=duration_font)
        draw.text(
            (width - margin - 36 - (duration_box[2] - duration_box[0]), y + 30),
            duration,
            fill=(137, 140, 160),
            font=duration_font,
        )

    footer_y = panel_top + panel_height + 29
    draw.ellipse((margin + 4, footer_y + 5, margin + 12, footer_y + 13), fill=(245, 72, 103))
    draw.text(
        (margin + 25, footer_y),
        "发送序号选择歌曲  ·  45 秒后自动关闭",
        fill=(135, 138, 157),
        font=meta_font,
    )

    buffer = BytesIO()
    image.convert("RGB").save(buffer, "PNG", optimize=True)
    return "base64://" + base64.b64encode(buffer.getvalue()).decode("ascii")
