# -*- coding: utf-8 -*-
import hashlib
import io
import math
from typing import Dict, List, Optional, Tuple

import cv2
import numpy as np
from PIL import Image

from .palette import COLOR_LIST, _PALETTE_KEY2IDX


def _pack_rgba_u32_arr(rgba_arr: np.ndarray) -> np.ndarray:
    r = rgba_arr[..., 0].astype(np.uint32)
    g = rgba_arr[..., 1].astype(np.uint32)
    b = rgba_arr[..., 2].astype(np.uint32)
    a = rgba_arr[..., 3].astype(np.uint32)
    return (r << 24) | (g << 16) | (b << 8) | a


class DiffEngine:
    def __init__(self, max_detail_points: int = 10):
        self.max_detail_points = max_detail_points

    @staticmethod
    def crop_bytes_to_png(byts: bytes, crop_box: Tuple[int, int, int, int]) -> bytes:
        with Image.open(io.BytesIO(byts)) as im:
            x1, y1, x2, y2 = crop_box
            crop = im.crop((x1, y1, x2, y2))
            out = io.BytesIO()
            crop.save(out, format="PNG")
            return out.getvalue()

    @staticmethod
    def merge_png_bytes(
        images: List[bytes], columns: Optional[int] = None
    ) -> Tuple[bytes, List[Tuple[int, int, int, int]]]:
        """Merge images in a grid; return (png, [(x, y, w, h)] per image)."""
        if not images:
            raise ValueError("未配置图块图片")
        if len(images) == 1:
            with Image.open(io.BytesIO(images[0])) as im:
                return images[0], [(0, 0, im.width, im.height)]

        opened = []
        try:
            for byts in images:
                with Image.open(io.BytesIO(byts)) as im:
                    opened.append(im.convert("RGBA"))

            if columns is None:
                columns = max(1, math.ceil(math.sqrt(len(opened))))
            if columns < 1 or columns > len(opened):
                raise ValueError("tile_columns 配置无效")

            rows = math.ceil(len(opened) / columns)
            column_widths = [
                max(im.width for i, im in enumerate(opened) if i % columns == column)
                for column in range(columns)
            ]
            row_heights = [
                max(
                    im.height
                    for i, im in enumerate(opened)
                    if i // columns == row
                )
                for row in range(rows)
            ]
            x_positions = [0]
            for width in column_widths[:-1]:
                x_positions.append(x_positions[-1] + width)
            y_positions = [0]
            for height in row_heights[:-1]:
                y_positions.append(y_positions[-1] + height)

            merged = Image.new("RGBA", (sum(column_widths), sum(row_heights)))
            positions = []
            for i, im in enumerate(opened):
                x = x_positions[i % columns]
                y = y_positions[i // columns]
                positions.append((x, y, im.width, im.height))
                merged.paste(im, (x, y))

            out = io.BytesIO()
            merged.save(out, format="PNG")
            return out.getvalue(), positions
        finally:
            for im in opened:
                im.close()

    @staticmethod
    def imread_rgba(byts: bytes) -> np.ndarray:
        arr = np.frombuffer(byts, dtype=np.uint8)
        img = cv2.imdecode(arr, cv2.IMREAD_UNCHANGED)
        if img is None:
            raise ValueError("图像解码失败")
        if img.ndim == 2:
            img = cv2.cvtColor(img, cv2.COLOR_GRAY2RGBA)
        elif img.shape[2] == 3:
            img = cv2.cvtColor(img, cv2.COLOR_BGR2RGBA)
        elif img.shape[2] == 4:
            img = img[:, :, [2, 1, 0, 3]]  # BGRA -> RGBA
        else:
            raise ValueError("不支持的通道数")
        return img

    @staticmethod
    def rgba_to_index_image(rgba: np.ndarray) -> np.ndarray:
        keys = _pack_rgba_u32_arr(rgba)
        flat = keys.reshape(-1)
        uniq, inv = np.unique(flat, return_inverse=True)
        map_arr = np.full(uniq.shape, -1, dtype=np.int8)
        for j, k in enumerate(uniq):
            map_arr[j] = _PALETTE_KEY2IDX.get(int(k), -1)
        idx_flat = map_arr[inv]
        return idx_flat.reshape(keys.shape).astype(np.int8)

    def index_hash_from_rgba(self, rgba: np.ndarray) -> str:
        idx = self.rgba_to_index_image(rgba)
        return hashlib.blake2b(idx.tobytes(), digest_size=16).hexdigest()

    def index_hash_from_png_bytes(self, byts: bytes) -> str:
        return self.index_hash_from_rgba(self.imread_rgba(byts))

    @staticmethod
    def same_shape(a: np.ndarray, b: np.ndarray) -> bool:
        return a.shape == b.shape

    @staticmethod
    def same_index(a_idx: np.ndarray, b_idx: np.ndarray) -> bool:
        return a_idx.shape == b_idx.shape and np.array_equal(a_idx, b_idx)

    @staticmethod
    def diff_regions_idx(a_idx: np.ndarray, b_idx: np.ndarray) -> List[Tuple[int, int, int, int]]:
        if a_idx.shape != b_idx.shape:
            raise ValueError("尺寸不一致，无法比较")
        mask = (a_idx != b_idx).astype(np.uint8) * 255
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        return [cv2.boundingRect(c) for c in contours]

    def changed_pixels_from_idx(
        self, a_idx: np.ndarray, b_idx: np.ndarray, limit: Optional[int] = None
    ) -> Tuple[List[Dict[str, object]], int]:
        if a_idx.shape != b_idx.shape:
            raise ValueError("尺寸不一致，无法逐像素比较")
        mask = a_idx != b_idx
        ys, xs = np.nonzero(mask)
        total = len(xs)
        if limit is not None and total > limit:
            idxs = np.linspace(0, total - 1, limit, dtype=int)
            xs = xs[idxs]
            ys = ys[idxs]
        changed: List[Dict[str, object]] = []
        for x, y in zip(xs, ys):
            old_i = int(a_idx[y, x])
            new_i = int(b_idx[y, x])
            if 0 <= new_i < len(COLOR_LIST):
                nr, ng, nb, na = COLOR_LIST[new_i].value
            else:
                nr, ng, nb, na = (0, 0, 0, 0)
            if 0 <= old_i < len(COLOR_LIST):
                or_, og, ob, oa = COLOR_LIST[old_i].value
            else:
                or_, og, ob, oa = (0, 0, 0, 0)
            changed.append({
                "x": int(x),
                "y": int(y),
                "new_color": (int(nr), int(ng), int(nb), int(na)),
                "old_color": (int(or_), int(og), int(ob), int(oa)),
                "old_idx": old_i,
                "new_idx": new_i,
            })
        return changed, total
