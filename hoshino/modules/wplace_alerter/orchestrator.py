# -*- coding: utf-8 -*-
"""Wplace art monitor core. Sync-only; call from run_in_executor.

refresh_one / set_current_as_baseline return structured dicts — the plugin
layer is responsible for broadcasting. Never call bot APIs from here.
"""
import hashlib
import logging
import os
import threading
from typing import Dict, List, Optional, Set, Tuple

from .paths import art_data_path, art_dir
from .diff import DiffEngine
from .fetcher import ImageFetcher
from .palette import COLOR_LIST, zh_color


def _bytes_hash(b: bytes) -> str:
    return hashlib.blake2b(b, digest_size=16).hexdigest()


def _save_text(path: str, s: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        f.write(s)


def _read_text(path: str) -> Optional[str]:
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        return f.read().strip()


def _save_bytes(path: str, byts: bytes) -> None:
    with open(path, "wb") as f:
        f.write(byts)


def _read_bytes(path: str) -> Optional[bytes]:
    if not os.path.exists(path):
        return None
    with open(path, "rb") as f:
        return f.read()


def _result(
    status: str,
    message: str,
    *,
    key: str = "",
    name: str = "",
    notify: bool = False,
    images: Optional[List[str]] = None,
    changed: Optional[int] = None,
    regions: Optional[int] = None,
    error: Optional[str] = None,
) -> Dict:
    out: Dict = {
        "status": status,
        "message": message,
        "key": key,
        "name": name,
        "notify": notify,
        "images": images or [],
    }
    if changed is not None:
        out["changed"] = changed
    if regions is not None:
        out["regions"] = regions
    if error is not None:
        out["error"] = error
    return out


class Orchestrator:
    def __init__(
        self,
        fetcher: ImageFetcher,
        diff: DiffEngine,
        settings: Dict,
        logger: Optional[logging.Logger] = None,
    ):
        self.fetcher = fetcher
        self.diff = diff
        self.settings = settings or {}
        self.logger = logger or logging.getLogger("wplace-alerter")
        self.alerted_once: Set[str] = set()
        self._locks: Dict[str, threading.Lock] = {}
        self._global_lock = threading.Lock()

    def update_settings(self, settings: Dict) -> None:
        self.settings = settings or {}

    def _get_lock(self, key: str) -> threading.Lock:
        with self._global_lock:
            if key not in self._locks:
                self._locks[key] = threading.Lock()
            return self._locks[key]

    def _art_meta(self, key: str) -> Tuple[Optional[Dict], str, str, Tuple[int, int, int, int]]:
        arts = self.settings.get("arts") or {}
        art = arts.get(key)
        if not art:
            return None, "", "", (0, 0, 0, 0)
        name = (art.get("name") or "").strip() or key
        try:
            api_image = art["api_image"]
            coords = (
                int(art["start_coords"]["x"]),
                int(art["start_coords"]["y"]),
                int(art["end_coords"]["x"]),
                int(art["end_coords"]["y"]),
            )
        except (KeyError, TypeError, ValueError) as e:
            self.logger.error("art[%s] 配置不完整：%s", key, e)
            return None, name, "", (0, 0, 0, 0)
        if not api_image:
            return None, name, "", (0, 0, 0, 0)
        return art, name, str(api_image), coords

    def _mark_restored(self, key: str, name: str, how: str) -> Dict:
        """Clear alerted state and optionally request a restore notification."""
        if key in self.alerted_once:
            self.alerted_once.discard(key)
            self.logger.info("%s 已恢复（%s）", name, how)
            return _result(
                "restored",
                f"[Wplace]{name}已恢复喵！",
                key=key,
                name=name,
                notify=True,
            )
        self.logger.info("%s 未变化（%s）", name, how)
        return _result("nochange", f"未变化：{name}（{how}）", key=key, name=name)

    def set_current_as_baseline(self, key: str) -> Dict:
        lock = self._get_lock(key)
        with lock:
            art, name, api_image, coords = self._art_meta(key)
            if not art:
                tip = f"未找到或配置无效 key={key}"
                return _result("error", tip, key=key, name=name, error=tip)

            try:
                path = art_dir(key)
            except ValueError as e:
                return _result("error", str(e), key=key, name=name, error=str(e))
            try:
                png_bytes = self.fetcher.fetch_png(api_image)
                cropped_bytes = self.diff.crop_bytes_to_png(png_bytes, coords)
                rgba = self.diff.imread_rgba(cropped_bytes)
                idx_hash = self.diff.index_hash_from_rgba(rgba)
            except Exception as e:
                return _result(
                    "error",
                    f"获取或解码图片失败：{e}",
                    key=key,
                    name=name,
                    error=str(e),
                )

            _save_bytes(os.path.join(path, "original.png"), cropped_bytes)
            _save_bytes(os.path.join(path, "good.png"), cropped_bytes)
            _save_bytes(os.path.join(path, "new.png"), cropped_bytes)
            _save_text(os.path.join(path, "good.hash"), idx_hash)
            self.alerted_once.discard(key)

            self.logger.info("[baseline] %s 已将当前最新图像设为原图", name)
            return _result(
                "baseline",
                f"{name} 已重设为基准",
                key=key,
                name=name,
            )

    def refresh_one(self, key: str) -> Dict:
        lock = self._get_lock(key)
        with lock:
            art, name, api_image, coords = self._art_meta(key)
            if not art:
                tip = f"未找到或配置无效 key={key}"
                return _result("error", tip, key=key, name=name, error=tip)
            if not art.get("track"):
                return _result("skipped", f"key={key} 未开启 track", key=key, name=name)

            try:
                path = art_dir(key)
            except ValueError as e:
                return _result("error", str(e), key=key, name=name, error=str(e))
            hash_path = os.path.join(path, "good.hash")
            good_path = os.path.join(path, "good.png")
            new_path = os.path.join(path, "new.png")
            orig_path = os.path.join(path, "original.png")

            try:
                png_bytes = self.fetcher.fetch_png(api_image)
                cropped_bytes = self.diff.crop_bytes_to_png(png_bytes, coords)
                new_rgba = self.diff.imread_rgba(cropped_bytes)
                new_idx = self.diff.rgba_to_index_image(new_rgba)
            except Exception as e:
                self.logger.error("拉取/解码 %s 失败：%s", name, e)
                return _result("error", f"拉取失败：{name}：{e}", key=key, name=name, error=str(e))

            new_idx_hash = hashlib.blake2b(new_idx.tobytes(), digest_size=16).hexdigest()
            old_idx_hash = _read_text(hash_path)

            # 1) index-hash short-circuit
            if old_idx_hash and new_idx_hash == old_idx_hash:
                _save_bytes(new_path, cropped_bytes)
                return self._mark_restored(key, name, "hash检测")

            # 2) binary-hash short-circuit
            old_good_bytes = _read_bytes(good_path)
            if old_good_bytes is not None and _bytes_hash(cropped_bytes) == _bytes_hash(old_good_bytes):
                try:
                    _save_text(hash_path, self.diff.index_hash_from_png_bytes(old_good_bytes))
                except Exception:
                    pass
                _save_bytes(new_path, cropped_bytes)
                return self._mark_restored(key, name, "二进制hash")

            _save_bytes(new_path, cropped_bytes)

            # 3) first run: establish baseline
            if old_good_bytes is None:
                self.alerted_once.discard(key)
                self.logger.info("%s 未找到基准，已保存为基准", name)
                _save_bytes(orig_path, cropped_bytes)
                _save_bytes(good_path, cropped_bytes)
                _save_text(hash_path, new_idx_hash)
                return _result("baseline", f"初始化基准：{name}", key=key, name=name)

            good_rgba = self.diff.imread_rgba(old_good_bytes)
            if not self.diff.same_shape(good_rgba, new_rgba):
                self.logger.error(
                    "%s 尺寸不一致 good=%s new=%s", name, good_rgba.shape, new_rgba.shape
                )
                return _result("error", f"尺寸不一致：{name}", key=key, name=name)

            good_idx = self.diff.rgba_to_index_image(good_rgba)

            # equal to current good
            if self.diff.same_index(good_idx, new_idx):
                try:
                    _save_text(hash_path, new_idx_hash)
                except Exception:
                    pass
                return self._mark_restored(key, name, "像素判定")

            # equal to original → treat as restored, update good
            if os.path.exists(orig_path):
                ob = _read_bytes(orig_path)
                if ob is not None:
                    orig_rgba = self.diff.imread_rgba(ob)
                    if self.diff.same_shape(orig_rgba, new_rgba):
                        orig_idx = self.diff.rgba_to_index_image(orig_rgba)
                        if self.diff.same_index(orig_idx, new_idx):
                            _save_bytes(good_path, cropped_bytes)
                            _save_text(hash_path, new_idx_hash)
                            return self._mark_restored(key, name, "恢复至原图")

            # vandalized
            bboxes = self.diff.diff_regions_idx(good_idx, new_idx)
            sample_limit = max(1, min(5000, max(self.diff.max_detail_points, 20)))
            changed, total = self.diff.changed_pixels_from_idx(
                good_idx, new_idx, limit=sample_limit
            )

            details: List[str] = []
            top_n = min(self.diff.max_detail_points, len(changed))
            for i in range(top_n):
                px = changed[i]
                old_idx = px["old_idx"]
                new_idx_i = px["new_idx"]
                old_name = COLOR_LIST[old_idx].name if 0 <= old_idx < len(COLOR_LIST) else None
                new_name = (
                    COLOR_LIST[new_idx_i].name if 0 <= new_idx_i < len(COLOR_LIST) else None
                )
                details.append(
                    f"[{i + 1}] X={coords[0] + px['x']}, Y={coords[1] + px['y']} "
                    f"{zh_color(old_name)} → {zh_color(new_name)}"
                )
            if total > top_n:
                details.append(f"...(共 {total} 个像素修改，{len(bboxes)} 个区域)")

            name_fmt = f"[{name}]" if name else ""
            msg = (
                f"[Wplace]{name_fmt} 检测到 {total} 个像素被修改喵！\n修改详情：\n"
                + ("\n".join(details) if details else "（变化较大，略去具体像素）")
            )

            if key not in self.alerted_once:
                self.alerted_once.add(key)
                self.logger.warning("%s 检测到 %s 个像素被修改", name, total)
                return _result(
                    "changed",
                    msg,
                    key=key,
                    name=name,
                    notify=True,
                    images=[new_path, good_path],
                    changed=total,
                    regions=len(bboxes),
                )

            self.logger.info("%s 仍有修改未修复（变更 %s 像素）", name, total)
            return _result(
                "changed",
                f"图像有修改并且未修复（{name}，变更像素 {total}）",
                key=key,
                name=name,
                notify=False,
                changed=total,
                regions=len(bboxes),
            )

    def status_lines(self) -> List[str]:
        lines = []
        for key, art in (self.settings.get("arts") or {}).items():
            name = (art.get("name") or "").strip() or key
            track = "开" if art.get("track") else "关"
            try:
                good = os.path.join(art_data_path(key), "good.png")
                baseline = "有" if os.path.exists(good) else "无"
            except ValueError:
                baseline = "非法key"
            alerted = "报警中" if key in self.alerted_once else "正常"
            lines.append(f"· {key} | {name} | track={track} | 基准={baseline} | {alerted}")
        return lines or ["（未配置 arts）"]
