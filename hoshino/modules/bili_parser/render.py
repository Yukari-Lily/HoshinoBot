import asyncio
import base64
from io import BytesIO
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

import httpx
from PIL import Image, ImageDraw, ImageFont


# Bilibili 官方夜间模式深色中性色规范（完全不刺眼，质感沉浸高级）
COLOR_BG_CARD = (27, 28, 32)            # #1B1C20 深色中性石墨黑底色
COLOR_BORDER_CARD = (50, 53, 61)        # #32353D 卡片外边框色
COLOR_TEXT_MAIN = (240, 242, 245)       # #F0F2F5 主文字柔和明亮
COLOR_TEXT_SUB = (162, 168, 181)        # #A2A8B5 次要文字深灰银
COLOR_TEXT_MUTED = (120, 126, 138)      # #787E8A 弱化文字/时间/未点赞交互状态
COLOR_BILI_PINK = (251, 114, 153)       # #FB7299 官方粉
COLOR_BILI_BLUE = (0, 174, 236)         # #00AEEC 官方蓝
COLOR_PILL_BG = (56, 31, 40)            # #381F28 深色粉红胶囊底色
COLOR_PILL_BORDER = (102, 45, 62)       # #662D3E 胶囊边框
COLOR_BOX_ARTICLE = (36, 38, 45)        # #24262D 专栏卡片底色
COLOR_BOX_BORDER = (58, 62, 72)         # #3A3E48 专栏/引用框边框
COLOR_BOX_QUOTE = (34, 36, 42)          # #22242A 转发引用框底色
COLOR_LINE_DIVIDER = (44, 46, 54)       # #2C2E36 分割线


def _font_candidates() -> Iterable[Path]:
    module_dir = Path(__file__).resolve().parent
    yield module_dir.parent / "torrent_analyze_HoshinoBot" / "SourceHanSansSC-Regular.otf"
    yield module_dir.parent / "Blue_Archive_HoshinoBot" / "ba_calendar" / "wqy-microhei.ttc"
    yield Path("C:/Windows/Fonts/msyh.ttc")
    yield Path("C:/Windows/Fonts/msyhbd.ttc")
    yield Path("C:/Windows/Fonts/simhei.ttf")
    yield Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc")
    yield Path("/usr/share/fonts/truetype/wqy/wqy-microhei.ttc")


def _load_font(size: int, bold: bool = False) -> ImageFont.ImageFont:
    for path in _font_candidates():
        if path.is_file():
            try:
                return ImageFont.truetype(str(path), size=size)
            except OSError:
                pass
    return ImageFont.load_default()


def _get_asset_path(filename: str) -> Path:
    return Path(__file__).resolve().parent / "assets" / filename


def _load_asset_image(filename: str) -> Optional[Image.Image]:
    path = _get_asset_path(filename)
    if not path.is_file():
        legacy_path = Path(__file__).resolve().parent / filename
        if legacy_path.is_file():
            path = legacy_path
        else:
            return None
    try:
        return Image.open(path).convert("RGBA")
    except Exception:
        return None


def _tint_icon(filename: str, color: Tuple[int, int, int], size: int) -> Optional[Image.Image]:
    """加载专业图标资产并进行平滑抗锯齿缩放与动态染色。"""
    raw_img = _load_asset_image(filename)
    if not raw_img:
        return None
    try:
        resized = raw_img.resize((size, size), Image.Resampling.LANCZOS)
        _, _, _, a = resized.split()
        solid = Image.new("RGBA", (size, size), color + (255,))
        solid.putalpha(a)
        return solid
    except Exception:
        return None


async def _fetch_image(client: httpx.AsyncClient, url: str) -> Optional[Image.Image]:
    if not url or not isinstance(url, str):
        return None
    url = url.strip()
    if url.startswith("//"):
        url = "https:" + url
    elif not url.startswith("http://") and not url.startswith("https://"):
        return None
    try:
        response = await client.get(url, timeout=10.0, follow_redirects=True)
        if response.status_code == 200 and "image" in response.headers.get("content-type", ""):
            image = Image.open(BytesIO(response.content))
            return image.convert("RGBA")
    except Exception:
        pass
    return None


def _crop_circle(img: Image.Image, size: int) -> Image.Image:
    img = img.resize((size, size), Image.Resampling.LANCZOS)
    mask = Image.new("L", (size, size), 0)
    draw = ImageDraw.Draw(mask)
    draw.ellipse((0, 0, size - 1, size - 1), fill=255)
    output = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    output.paste(img, (0, 0), mask)
    return output


def _round_corners(img: Image.Image, radius: int, border_color: Optional[Tuple[int, int, int]] = None) -> Image.Image:
    w, h = img.size
    mask = Image.new("L", (w, h), 0)
    draw_mask = ImageDraw.Draw(mask)
    draw_mask.rounded_rectangle((0, 0, w - 1, h - 1), radius=radius, fill=255)
    output = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    output.paste(img, (0, 0), mask)
    if border_color:
        draw_border = ImageDraw.Draw(output)
        draw_border.rounded_rectangle((0, 0, w - 1, h - 1), radius=radius, outline=border_color, width=1)
    return output


def _wrap_text(text: str, font: ImageFont.ImageFont, max_width: int, max_lines: int = 50) -> List[str]:
    if not text:
        return []
    lines: List[str] = []
    temp_img = Image.new("RGBA", (1, 1))
    draw = ImageDraw.Draw(temp_img)

    for paragraph in text.split("\n"):
        paragraph = paragraph.rstrip()
        if not paragraph:
            lines.append("")
            continue
        current_line = ""
        for char in paragraph:
            test_line = current_line + char
            bbox = draw.textbbox((0, 0), test_line, font=font)
            line_w = bbox[2] - bbox[0]
            if line_w <= max_width:
                current_line = test_line
            else:
                lines.append(current_line)
                current_line = char
                if len(lines) >= max_lines:
                    lines[-1] = lines[-1][:-1] + "…"
                    return lines
        if current_line:
            lines.append(current_line)
            if len(lines) >= max_lines:
                lines[-1] = lines[-1][:-1] + "…"
                return lines
    return lines


def _draw_pill_badge(
    draw: ImageDraw.ImageDraw,
    x: int,
    y: int,
    text: str,
    font: ImageFont.ImageFont,
    text_color: Tuple[int, int, int] = COLOR_BILI_PINK,
    bg_color: Tuple[int, int, int] = COLOR_PILL_BG,
    border_color: Tuple[int, int, int] = COLOR_PILL_BORDER,
    padding_x: int = 8,
    padding_y: int = 4,
    radius: int = 4,
) -> Tuple[int, int]:
    """高精度绘制自适应尺寸与精细居中的胶囊小标，返回 (width, height)。"""
    bbox = draw.textbbox((0, 0), text, font=font)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    pw = tw + padding_x * 2
    ph = th + padding_y * 2
    draw.rounded_rectangle((x, y, x + pw, y + ph), radius=radius, fill=bg_color, outline=border_color, width=1)
    tx = x + padding_x - bbox[0]
    ty = y + padding_y - bbox[1]
    draw.text((tx, ty), text, fill=text_color, font=font)
    return pw, ph


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


async def render_dynamic_card(
    item: Dict[str, object],
    client: httpx.AsyncClient,
    card_width: int = 800,
    max_text_len: int = 300,
) -> str:
    """将 Bilibili 动态数据渲染为贴合官方质感的深色中性风格长图卡片。"""
    padding = 34
    content_width = card_width - padding * 2

    font_name = _load_font(24, bold=True)
    font_meta = _load_font(16)
    font_body = _load_font(22)
    font_art_title = _load_font(23, bold=True)
    font_art_desc = _load_font(18)
    font_tag = _load_font(15, bold=True)
    font_footer = _load_font(18)
    font_bili = _load_font(21, bold=True)
    font_link = _load_font(15)

    # 加载真实 Bilibili 官方小电视 Logo
    bili_logo_raw = _load_asset_image("bilibili_logo.png")

    author = _dig(item, "modules", "module_author", default={})
    author_name = str(author.get("name") or "Bilibili 用户")
    pub_time = str(author.get("pub_time") or "")
    face_url = str(author.get("face") or "")

    dynamic = _dig(item, "modules", "module_dynamic", default={})
    desc_text = str(_dig(dynamic, "desc", "text") or "")
    if max_text_len > 0 and len(desc_text) > max_text_len:
        desc_text = desc_text[:max_text_len] + "…"

    major = _dig(dynamic, "major", default={})
    major_type = str(major.get("type") or "")

    # 预拉取作者头像
    avatar_task = _fetch_image(client, face_url)

    # 提取配图/卡片图片
    draw_pics: List[str] = []
    if major_type == "MAJOR_TYPE_DRAW" or "draw" in major:
        items = _dig(major, "draw", "items", default=[])
        for it in items:
            if isinstance(it, dict) and it.get("src"):
                draw_pics.append(str(it["src"]))
    elif major_type == "MAJOR_TYPE_OPUS" or "opus" in major:
        pics = _dig(major, "opus", "pics", default=[])
        for pic in pics:
            if isinstance(pic, dict) and pic.get("url"):
                draw_pics.append(str(pic["url"]))

    # 专栏文章信息
    article_title = ""
    article_desc = ""
    article_cover_url = ""
    if major_type == "MAJOR_TYPE_ARTICLE" or "article" in major:
        art = major.get("article") or {}
        article_title = str(art.get("title") or "")
        article_desc = str(art.get("desc") or "")
        if max_text_len > 0 and len(article_desc) > max_text_len:
            article_desc = article_desc[:max_text_len] + "…"
        covers = art.get("covers") or []
        if covers and isinstance(covers, list):
            article_cover_url = str(covers[0])
    elif major_type == "MAJOR_TYPE_OPUS" or "opus" in major:
        opus = major.get("opus") or {}
        if opus.get("title"):
            article_title = str(opus.get("title"))
        summary = str(_dig(opus, "summary", "text") or "")
        if summary and not desc_text:
            desc_text = summary
            if max_text_len > 0 and len(desc_text) > max_text_len:
                desc_text = desc_text[:max_text_len] + "…"

    # 转发原动态信息
    orig_item = item.get("orig")
    orig_author_name = ""
    orig_text = ""
    orig_pics: List[str] = []
    orig_art_title = ""
    orig_art_desc = ""
    orig_art_cover_url = ""

    if isinstance(orig_item, dict) and orig_item.get("type") != "DYNAMIC_TYPE_NONE":
        orig_author = _dig(orig_item, "modules", "module_author", default={})
        orig_author_name = str(orig_author.get("name") or "")
        orig_dyn = _dig(orig_item, "modules", "module_dynamic", default={})
        orig_text = str(_dig(orig_dyn, "desc", "text") or "")
        if max_text_len > 0 and len(orig_text) > max_text_len:
            orig_text = orig_text[:max_text_len] + "…"
        orig_major = _dig(orig_dyn, "major", default={})
        orig_major_type = str(orig_major.get("type") or "")
        if orig_major_type == "MAJOR_TYPE_DRAW" or "draw" in orig_major:
            for it in _dig(orig_major, "draw", "items", default=[]):
                if isinstance(it, dict) and it.get("src"):
                    orig_pics.append(str(it["src"]))
        elif orig_major_type == "MAJOR_TYPE_ARTICLE" or "article" in orig_major:
            o_art = orig_major.get("article") or {}
            orig_art_title = str(o_art.get("title") or "")
            orig_art_desc = str(o_art.get("desc") or "")
            o_covers = o_art.get("covers") or []
            if o_covers and isinstance(o_covers, list):
                orig_art_cover_url = str(o_covers[0])

    # 并行拉取关联图片
    img_urls_to_fetch = []
    if article_cover_url:
        img_urls_to_fetch.append(("art_cover", article_cover_url))
    for idx, pic_url in enumerate(draw_pics[:9]):
        img_urls_to_fetch.append((f"draw_{idx}", pic_url))
    if orig_art_cover_url:
        img_urls_to_fetch.append(("orig_art_cover", orig_art_cover_url))
    for idx, pic_url in enumerate(orig_pics[:9]):
        img_urls_to_fetch.append((f"orig_draw_{idx}", pic_url))

    fetch_tasks = [avatar_task] + [_fetch_image(client, u) for _, u in img_urls_to_fetch]
    results = await asyncio.gather(*fetch_tasks, return_exceptions=True)
    avatar_img = results[0] if isinstance(results[0], Image.Image) else None
    fetched_images: Dict[str, Image.Image] = {}
    for (key, _), img_res in zip(img_urls_to_fetch, results[1:]):
        if isinstance(img_res, Image.Image):
            fetched_images[key] = img_res

    # 统计数据
    stat = _dig(item, "modules", "module_stat", default={})
    like_count = _dig(stat, "like", "count", default=0)
    comment_count = _dig(stat, "comment", "count", default=0)
    forward_count = _dig(stat, "forward", "count", default=0)

    # ---------------- 测量布局高度 ----------------
    cur_y = padding

    # 1. Header (Avatar 58x58 + Name + PubTime + Badge)
    avatar_size = 58
    header_h = avatar_size
    cur_y += header_h + 18

    # 2. Main dynamic text
    body_lines = _wrap_text(desc_text, font_body, content_width)
    line_h_body = 32
    if body_lines:
        cur_y += len(body_lines) * line_h_body + 16

    # 3. Dynamic pictures (Draw)
    main_draw_imgs = [fetched_images[f"draw_{idx}"] for idx in range(len(draw_pics[:9])) if f"draw_{idx}" in fetched_images]
    pictures_layout: List[Tuple[Image.Image, int, int, int, int]] = []
    if main_draw_imgs:
        if len(main_draw_imgs) == 1:
            img = main_draw_imgs[0]
            iw, ih = img.size
            scale = min(1.0, content_width / max(1, iw))
            target_w = int(iw * scale)
            target_h = int(ih * scale)
            if target_h > 1200:
                target_h = 1200
                target_w = int(iw * (1200 / max(1, ih)))
            pictures_layout.append((img, 0, 0, target_w, target_h))
            cur_y += target_h + 18
        else:
            cols = 2 if len(main_draw_imgs) in (2, 4) else 3
            cell_size = int((content_width - (cols - 1) * 8) / cols)
            rows = (len(main_draw_imgs) + cols - 1) // cols
            for idx, img in enumerate(main_draw_imgs):
                r = idx // cols
                c = idx % cols
                rx = c * (cell_size + 8)
                ry = r * (cell_size + 8)
                pictures_layout.append((img, rx, ry, cell_size, cell_size))
            cur_y += rows * cell_size + (rows - 1) * 8 + 18

    # 4. Article card
    art_cover_img = fetched_images.get("art_cover")
    art_box_h = 0
    art_title_lines = _wrap_text(article_title, font_art_title, content_width - 32)
    art_desc_lines = _wrap_text(article_desc, font_art_desc, content_width - 32, max_lines=4)
    if article_title or article_desc or art_cover_img:
        art_box_padding = 18
        art_box_h += art_box_padding * 2
        if art_cover_img:
            art_box_h += 220 + 14
        # 专栏精致小标高度
        art_box_h += 24 + 10
        if art_title_lines:
            art_box_h += len(art_title_lines) * 32 + 8
        if art_desc_lines:
            art_box_h += len(art_desc_lines) * 26 + 4
        cur_y += art_box_h + 18

    # 5. Forward Quote Box
    orig_box_h = 0
    orig_lines = _wrap_text(orig_text, font_art_desc, content_width - 36)
    orig_draw_imgs = [fetched_images[f"orig_draw_{idx}"] for idx in range(len(orig_pics[:9])) if f"orig_draw_{idx}" in fetched_images]
    orig_art_cover_img = fetched_images.get("orig_art_cover")
    if orig_author_name or orig_lines or orig_draw_imgs or orig_art_title:
        orig_box_padding = 18
        orig_box_h += orig_box_padding * 2
        orig_box_h += 28 + 8
        if orig_lines:
            orig_box_h += len(orig_lines) * 26 + 12
        if orig_draw_imgs:
            cols = 3
            cell_size = int((content_width - 36 - (cols - 1) * 8) / cols)
            rows = (len(orig_draw_imgs) + cols - 1) // cols
            orig_box_h += rows * cell_size + (rows - 1) * 8 + 12
        if orig_art_title or orig_art_cover_img:
            orig_box_h += 70 + (160 if orig_art_cover_img else 0)
        cur_y += orig_box_h + 18

    # 6. Footer (Separator + Stats + Logo + Link)
    footer_h = 76
    cur_y += footer_h + padding

    total_height = cur_y

    # ---------------- 渲染画布 ----------------
    card = Image.new("RGBA", (card_width, total_height), COLOR_BG_CARD + (255,))
    draw = ImageDraw.Draw(card)

    # 绘制整体卡片边框与圆角
    draw.rounded_rectangle(
        (1, 1, card_width - 2, total_height - 2),
        radius=20,
        outline=COLOR_BORDER_CARD,
        width=1,
    )

    draw_y = padding

    # 1. 绘制 Header
    if avatar_img:
        avatar_circle = _crop_circle(avatar_img, avatar_size)
        card.paste(avatar_circle, (padding, draw_y), avatar_circle)
        draw.ellipse(
            (padding, draw_y, padding + avatar_size - 1, draw_y + avatar_size - 1),
            outline=COLOR_BORDER_CARD,
            width=1,
        )
    else:
        draw.ellipse((padding, draw_y, padding + avatar_size, draw_y + avatar_size), fill=(45, 48, 55))

    name_x = padding + avatar_size + 14
    draw.text((name_x, draw_y + 2), author_name, fill=COLOR_TEXT_MAIN, font=font_name)
    if pub_time:
        draw.text((name_x, draw_y + 34), pub_time, fill=COLOR_TEXT_MUTED, font=font_meta)

    # 右上角带真实 Bilibili Logo 的胶囊徽章（高精度垂直居中）
    pill_w = 76
    pill_h = 28
    pill_x = card_width - padding - pill_w
    pill_y = draw_y + 4
    draw.rounded_rectangle(
        (pill_x, pill_y, pill_x + pill_w, pill_y + pill_h),
        radius=14,
        fill=COLOR_PILL_BG,
        outline=COLOR_PILL_BORDER,
        width=1,
    )
    if bili_logo_raw:
        logo_top = bili_logo_raw.resize((18, 18), Image.Resampling.LANCZOS)
        card.paste(logo_top, (pill_x + 8, pill_y + 5), logo_top)
    draw.text((pill_x + 32, pill_y + 4), "动态", fill=COLOR_BILI_PINK, font=font_tag)

    draw_y += header_h + 18

    # 2. 绘制主体文案（原文）
    if body_lines:
        for line in body_lines:
            draw.text((padding, draw_y), line, fill=COLOR_TEXT_MAIN, font=font_body)
            draw_y += line_h_body
        draw_y += 16

    # 3. 绘制动态配图
    if pictures_layout:
        start_y = draw_y
        for img, rx, ry, tw, th in pictures_layout:
            if len(pictures_layout) == 1:
                resized = img.resize((tw, th), Image.Resampling.LANCZOS)
                rounded = _round_corners(resized, 12, border_color=COLOR_BORDER_CARD)
                card.paste(rounded, (padding + rx, start_y + ry), rounded)
                draw_y += th + 18
            else:
                iw, ih = img.size
                min_edge = min(iw, ih)
                crop_box = ((iw - min_edge) // 2, (ih - min_edge) // 2, (iw + min_edge) // 2, (ih + min_edge) // 2)
                square = img.crop(crop_box).resize((tw, th), Image.Resampling.LANCZOS)
                rounded = _round_corners(square, 8, border_color=COLOR_BORDER_CARD)
                card.paste(rounded, (padding + rx, start_y + ry), rounded)
        if len(pictures_layout) > 1:
            rows = (len(pictures_layout) + (2 if len(pictures_layout) in (2, 4) else 3) - 1) // (2 if len(pictures_layout) in (2, 4) else 3)
            cell_size = pictures_layout[0][3]
            draw_y += rows * cell_size + (rows - 1) * 8 + 18

    # 4. 绘制专栏卡片
    if art_box_h > 0:
        box_y1 = draw_y
        box_y2 = draw_y + art_box_h
        draw.rounded_rectangle(
            (padding, box_y1, card_width - padding, box_y2),
            radius=14,
            fill=COLOR_BOX_ARTICLE,
            outline=COLOR_BOX_BORDER,
            width=1,
        )
        inner_y = box_y1 + 18
        inner_x = padding + 18
        inner_w = content_width - 36

        if art_cover_img:
            cw, ch = art_cover_img.size
            scale = inner_w / max(1, cw)
            scaled_h = int(ch * scale)
            resized = art_cover_img.resize((inner_w, scaled_h), Image.Resampling.LANCZOS)
            if scaled_h > 220:
                cy = (scaled_h - 220) // 2
                resized = resized.crop((0, cy, inner_w, cy + 220))
            rounded = _round_corners(resized, 8, border_color=COLOR_BORDER_CARD)
            card.paste(rounded, (inner_x, inner_y), rounded)
            inner_y += 220 + 14

        # 专栏小标（采用高精度居中排版，尺寸贴合无溢出）
        _, pill_actual_h = _draw_pill_badge(
            draw,
            inner_x,
            inner_y,
            "专栏",
            font_tag,
            text_color=COLOR_BILI_PINK,
            bg_color=COLOR_PILL_BG,
            border_color=COLOR_PILL_BORDER,
            padding_x=8,
            padding_y=4,
            radius=4,
        )
        inner_y += pill_actual_h + 10

        if art_title_lines:
            for tl in art_title_lines:
                draw.text((inner_x, inner_y), tl, fill=COLOR_TEXT_MAIN, font=font_art_title)
                inner_y += 32
            inner_y += 4

        if art_desc_lines:
            for dl in art_desc_lines:
                draw.text((inner_x, inner_y), dl, fill=COLOR_TEXT_SUB, font=font_art_desc)
                inner_y += 26

        draw_y += art_box_h + 18

    # 5. 绘制转发引用框
    if orig_box_h > 0:
        box_y1 = draw_y
        box_y2 = draw_y + orig_box_h
        draw.rounded_rectangle(
            (padding, box_y1, card_width - padding, box_y2),
            radius=14,
            fill=COLOR_BOX_QUOTE,
            outline=COLOR_BOX_BORDER,
            width=1,
        )
        inner_y = box_y1 + 18
        inner_x = padding + 18
        draw.text((inner_x, inner_y), f"@{orig_author_name}", fill=COLOR_BILI_BLUE, font=font_name)
        inner_y += 36

        if orig_lines:
            for ol in orig_lines:
                draw.text((inner_x, inner_y), ol, fill=COLOR_TEXT_SUB, font=font_art_desc)
                inner_y += 26
            inner_y += 10

        if orig_draw_imgs:
            cols = 3
            cell_size = int((content_width - 36 - (cols - 1) * 8) / cols)
            for idx, img in enumerate(orig_draw_imgs[:9]):
                r = idx // cols
                c = idx % cols
                rx = inner_x + c * (cell_size + 8)
                ry = inner_y + r * (cell_size + 8)
                iw, ih = img.size
                min_edge = min(iw, ih)
                crop_box = ((iw - min_edge) // 2, (ih - min_edge) // 2, (iw + min_edge) // 2, (ih + min_edge) // 2)
                square = img.crop(crop_box).resize((cell_size, cell_size), Image.Resampling.LANCZOS)
                rounded = _round_corners(square, 6, border_color=COLOR_BORDER_CARD)
                card.paste(rounded, (rx, ry), rounded)
            rows = (len(orig_draw_imgs[:9]) + cols - 1) // cols
            inner_y += rows * cell_size + (rows - 1) * 8 + 10

        if orig_art_title:
            draw.text((inner_x, inner_y), f"【专栏】{orig_art_title}", fill=COLOR_TEXT_MAIN, font=font_art_title)

        draw_y += orig_box_h + 18

    # 6. 绘制 Footer (分割线 + 真实图标互动数据 + 真实 Logo + 原链接)
    draw.line((padding, draw_y, card_width - padding, draw_y), fill=COLOR_LINE_DIVIDER, width=1)
    footer_bar_y = draw_y + 16

    # 真实矢量图标渲染（左侧：转发、评论、点赞，点赞使用统一的质感中性灰，不加粉色）
    icon_share = _tint_icon("icon_share.png", COLOR_TEXT_MUTED, 18)
    icon_comment = _tint_icon("icon_comment.png", COLOR_TEXT_MUTED, 18)
    icon_like = _tint_icon("icon_like.png", COLOR_TEXT_MUTED, 18)

    stat_x = padding
    if icon_share:
        card.paste(icon_share, (stat_x, footer_bar_y + 3), icon_share)
    draw.text((stat_x + 24, footer_bar_y + 1), str(forward_count), fill=COLOR_TEXT_MUTED, font=font_footer)

    stat_x += 70
    if icon_comment:
        card.paste(icon_comment, (stat_x, footer_bar_y + 3), icon_comment)
    draw.text((stat_x + 24, footer_bar_y + 1), str(comment_count), fill=COLOR_TEXT_MUTED, font=font_footer)

    stat_x += 70
    if icon_like:
        card.paste(icon_like, (stat_x, footer_bar_y + 3), icon_like)
    draw.text((stat_x + 24, footer_bar_y + 1), str(like_count), fill=COLOR_TEXT_MUTED, font=font_footer)

    # 官方品牌标（右侧：真实小电视 Logo + bilibili 字体）
    brand_text = "bilibili"
    brand_w = draw.textbbox((0, 0), brand_text, font=font_bili)[2]
    logo_size = 26
    logo_x = card_width - padding - brand_w - logo_size - 8
    logo_y = footer_bar_y - 1
    if bili_logo_raw:
        logo_footer = bili_logo_raw.resize((logo_size, logo_size), Image.Resampling.LANCZOS)
        card.paste(logo_footer, (logo_x, logo_y), logo_footer)
    draw.text((logo_x + logo_size + 8, footer_bar_y - 2), brand_text, fill=COLOR_BILI_PINK, font=font_bili)

    # 底部专属链接
    link_y = footer_bar_y + 30
    id_str = str(item.get("id_str") or "")
    if id_str:
        link_str = f"https://www.bilibili.com/opus/{id_str}"
        draw.text((padding, link_y), link_str, fill=COLOR_TEXT_MUTED, font=font_link)

    # 导出为 base64 PNG
    buffer = BytesIO()
    card.convert("RGB").save(buffer, "PNG", optimize=True)
    return "base64://" + base64.b64encode(buffer.getvalue()).decode("ascii")
