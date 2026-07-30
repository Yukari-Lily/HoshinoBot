# -*- coding: utf-8 -*-
from enum import Enum
from typing import Dict, Optional, Tuple


class Color(Enum):
    TRANSPARENT = (0, 0, 0, 0)
    BLACK = (0, 0, 0, 255)
    DARK_GRAY = (60, 60, 60, 255)
    GRAY = (120, 120, 120, 255)
    LIGHT_GRAY = (210, 210, 210, 255)
    WHITE = (255, 255, 255, 255)
    DEEP_RED = (96, 0, 24, 255)
    RED = (237, 28, 36, 255)
    ORANGE = (255, 127, 39, 255)
    GOLD = (246, 170, 9, 255)
    YELLOW = (249, 221, 59, 255)
    LIGHT_YELLOW = (255, 250, 188, 255)
    DARK_GREEN = (14, 185, 104, 255)
    GREEN = (19, 230, 123, 255)
    LIGHT_GREEN = (135, 255, 94, 255)
    DARK_TEAL = (12, 129, 110, 255)
    TEAL = (16, 174, 166, 255)
    LIGHT_TEAL = (19, 225, 190, 255)
    DARK_BLUE = (40, 80, 158, 255)
    BLUE = (64, 147, 228, 255)
    CYAN = (96, 247, 242, 255)
    INDIGO = (107, 80, 246, 255)
    LIGHT_INDIGO = (153, 177, 251, 255)
    DARK_PURPLE = (120, 12, 153, 255)
    PURPLE = (170, 56, 185, 255)
    LIGHT_PURPLE = (224, 159, 249, 255)
    DARK_PINK = (203, 0, 122, 255)
    PINK = (236, 31, 128, 255)
    LIGHT_PINK = (243, 141, 169, 255)
    DARK_BROWN = (104, 70, 52, 255)
    BROWN = (149, 104, 42, 255)
    BEIGE = (248, 178, 119, 255)
    MEDIUM_GRAY = (170, 170, 170, 255)
    DARK_RED = (165, 14, 30, 255)
    LIGHT_RED = (250, 128, 114, 255)
    DARK_ORANGE = (228, 92, 26, 255)
    LIGHT_TAN = (214, 181, 148, 255)
    DARK_GOLDENROD = (156, 132, 49, 255)
    GOLDENROD = (197, 173, 49, 255)
    LIGHT_GOLDENROD = (232, 212, 95, 255)
    DARK_OLIVE = (74, 107, 58, 255)
    OLIVE = (90, 148, 74, 255)
    LIGHT_OLIVE = (132, 197, 115, 255)
    DARK_CYAN = (15, 121, 159, 255)
    LIGHT_CYAN = (187, 250, 242, 255)
    LIGHT_BLUE = (125, 199, 255, 255)
    DARK_INDIGO = (77, 49, 184, 255)
    DARK_SLATE_BLUE = (74, 66, 132, 255)
    SLATE_BLUE = (122, 113, 196, 255)
    LIGHT_SLATE_BLUE = (181, 174, 241, 255)
    LIGHT_BROWN = (219, 164, 99, 255)
    DARK_BEIGE = (209, 128, 81, 255)
    LIGHT_BEIGE = (255, 197, 165, 255)
    DARK_PEACH = (155, 82, 73, 255)
    PEACH = (209, 128, 120, 255)
    LIGHT_PEACH = (250, 182, 164, 255)
    DARK_TAN = (123, 99, 82, 255)
    TAN = (156, 132, 107, 255)
    DARK_SLATE = (51, 57, 65, 255)
    SLATE = (109, 117, 141, 255)
    LIGHT_SLATE = (179, 185, 209, 255)
    DARK_STONE = (109, 100, 63, 255)
    STONE = (148, 140, 107, 255)
    LIGHT_STONE = (205, 197, 158, 255)


COLOR_LIST = list(Color)

# 色板 uint32 key -> palette index
_PALETTE_KEY2IDX: Dict[int, int] = {
    ((r << 24) | (g << 16) | (b << 8) | a): i
    for i, (r, g, b, a) in enumerate(c.value for c in COLOR_LIST)
}

COLOR_NAME_MAP = {
    "TRANSPARENT": "透明", "BLACK": "黑色", "DARK_GRAY": "深灰色", "GRAY": "灰色",
    "LIGHT_GRAY": "浅灰色", "WHITE": "白色",
    "DEEP_RED": "深红色", "RED": "红色", "ORANGE": "橙色", "GOLD": "金色",
    "YELLOW": "黄色", "LIGHT_YELLOW": "浅黄色",
    "DARK_GREEN": "深绿色", "GREEN": "绿色", "LIGHT_GREEN": "浅绿色",
    "DARK_TEAL": "深青色", "TEAL": "青色", "LIGHT_TEAL": "浅青色",
    "DARK_BLUE": "深蓝色", "BLUE": "蓝色", "CYAN": "青蓝色",
    "INDIGO": "靛青", "LIGHT_INDIGO": "浅靛青",
    "DARK_PURPLE": "深紫色", "PURPLE": "紫色", "LIGHT_PURPLE": "浅紫色",
    "DARK_PINK": "深粉色", "PINK": "粉色", "LIGHT_PINK": "浅粉色",
    "DARK_BROWN": "深棕色", "BROWN": "棕色", "BEIGE": "米色", "MEDIUM_GRAY": "中灰色",
    "DARK_RED": "暗红色", "LIGHT_RED": "浅红色", "DARK_ORANGE": "深橙色", "LIGHT_TAN": "浅褐色",
    "DARK_GOLDENROD": "深金麒麟色", "GOLDENROD": "金麒麟色", "LIGHT_GOLDENROD": "浅金麒麟色",
    "DARK_OLIVE": "深橄榄色", "OLIVE": "橄榄色", "LIGHT_OLIVE": "浅橄榄色",
    "DARK_CYAN": "深青蓝色", "LIGHT_CYAN": "浅青蓝色", "LIGHT_BLUE": "浅蓝色",
    "DARK_INDIGO": "深靛青", "DARK_SLATE_BLUE": "深石板蓝", "SLATE_BLUE": "石板蓝",
    "LIGHT_SLATE_BLUE": "浅石板蓝",
    "LIGHT_BROWN": "浅棕色", "DARK_BEIGE": "深米色", "LIGHT_BEIGE": "浅米色",
    "DARK_PEACH": "深桃色", "PEACH": "桃色", "LIGHT_PEACH": "浅桃色",
    "DARK_TAN": "深褐色", "TAN": "褐色", "DARK_SLATE": "深石板灰", "SLATE": "石板灰",
    "LIGHT_SLATE": "浅石板灰",
    "DARK_STONE": "深石色", "STONE": "石色", "LIGHT_STONE": "浅石色",
}


def zh_color(name: Optional[str]) -> str:
    return COLOR_NAME_MAP.get(name, "未知颜色")
