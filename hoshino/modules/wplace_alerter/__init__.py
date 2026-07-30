# -*- coding: utf-8 -*-
"""Wplace 像素画监控 — Hoshino 插件入口。"""
import asyncio
import base64
import os
from typing import List, Optional

from hoshino import Service, get_bot, priv
from hoshino.typing import CQEvent

from .paths import DATA_DIR, get_https_proxy, load_config
from .diff import DiffEngine
from .fetcher import ImageFetcher
from .orchestrator import Orchestrator

HELP = """【Wplace 像素画监控】
启用本服务后，破坏/恢复会推送到本群。
wplace / wplace帮助 — 本帮助
wplace状态 — 监控列表
wplace检查 [key] — 立即检查（管理员）
wplace重设 [key|all] — 将当前图设为基准（管理员）
刷新 — 同 wplace重设 all（管理员）
群内发送：启用 wplace-alerter
"""

sv = Service(
    "wplace-alerter",
    use_priv=priv.NORMAL,
    manage_priv=priv.ADMIN,
    enable_on_default=False,
    visible=True,
    help_=HELP,
    bundle="通知",
)

_daemon_task: Optional[asyncio.Task] = None
_orch: Optional[Orchestrator] = None
_tick_lock = asyncio.Lock()
_ART_GAP = 2  # seconds between arts to ease API pressure


def _build_orch(cfg: dict) -> Orchestrator:
    proxy = get_https_proxy(cfg)
    fetcher = ImageFetcher(timeout=10, https_proxy=proxy, trust_env=False)
    diff = DiffEngine(max_detail_points=int((cfg.get("diff") or {}).get("max_points", 10)))
    return Orchestrator(fetcher, diff, cfg, logger=sv.logger)


def _get_orch(reload_cfg: bool = False) -> Orchestrator:
    global _orch
    if _orch is None:
        _orch = _build_orch(load_config(sv.logger))
        return _orch
    if reload_cfg:
        cfg = load_config(sv.logger)
        old_proxy = get_https_proxy(_orch.settings)
        new_proxy = get_https_proxy(cfg)
        max_pts = int((cfg.get("diff") or {}).get("max_points", 10))
        if new_proxy != old_proxy:
            # proxy changed — rebuild fetcher, keep alert state
            new = _build_orch(cfg)
            new.alerted_once = _orch.alerted_once
            _orch = new
        else:
            _orch.update_settings(cfg)
            _orch.diff.max_detail_points = max_pts
    return _orch


def _image_cq(path: str) -> str:
    if not path or not os.path.exists(path):
        return ""
    with open(path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode()
    return f"[CQ:image,file=base64://{b64}]"


def _compose_message(result: dict) -> Optional[str]:
    """Text + optional images for broadcast / reply."""
    text = (result.get("message") or "").strip()
    parts = [text] if text else []
    for p in result.get("images") or []:
        cq = _image_cq(p)
        if cq:
            parts.append(cq)
    if not parts:
        return None
    return "\n".join(parts)


async def _broadcast_result(result: dict):
    if not result.get("notify"):
        return
    msg = _compose_message(result)
    if msg is None:
        return
    tag = result.get("name") or result.get("key") or "wplace"
    await sv.broadcast(msg, TAG=tag, interval_time=0.5)


async def _run_refresh(key: str, orch: Optional[Orchestrator] = None) -> dict:
    orch = orch or _get_orch()
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, orch.refresh_one, key)


async def _run_baseline(key: str, orch: Optional[Orchestrator] = None) -> dict:
    orch = orch or _get_orch()
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, orch.set_current_as_baseline, key)


async def _poll_once():
    """One full pass over track=true arts. Skips if no enable groups (saves API)."""
    if _tick_lock.locked():
        sv.logger.info("上一轮检查尚未结束，跳过本轮")
        return
    async with _tick_lock:
        groups = await sv.get_enable_groups()
        if not groups:
            return
        try:
            orch = _get_orch(reload_cfg=True)
        except Exception as e:
            sv.logger.error("加载配置失败：%s", e)
            return
        arts = orch.settings.get("arts") or {}
        tracked = [(k, a) for k, a in arts.items() if a.get("track")]
        for i, (key, _) in enumerate(tracked):
            try:
                result = await _run_refresh(key, orch)
                await _broadcast_result(result)
            except Exception as e:
                sv.logger.exception(e)
                sv.logger.error("检查 %s 失败：%s", key, e)
            if i + 1 < len(tracked):
                await asyncio.sleep(_ART_GAP)


async def _wplace_daemon():
    while True:
        try:
            try:
                cfg = load_config(sv.logger)
                cooldown = max(5, int(cfg.get("cooldown", 30)))
            except Exception:
                cooldown = 30
            await _poll_once()
        except asyncio.CancelledError:
            sv.logger.info("Wplace daemon 已停止")
            raise
        except Exception as e:
            sv.logger.exception(e)
            sv.logger.error("Wplace 轮询异常：%s", e)
            cooldown = 30
        await asyncio.sleep(cooldown)


def _start_daemon():
    global _daemon_task
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        sv.logger.error("无法获取事件循环，Wplace daemon 未启动")
        return
    if _daemon_task is not None and not _daemon_task.done():
        return
    os.makedirs(DATA_DIR, exist_ok=True)
    _daemon_task = loop.create_task(_wplace_daemon())
    sv.logger.info("Wplace alerter daemon 已启动")


bot = get_bot()


@bot.on_startup
async def _on_startup():
    _start_daemon()


def _is_admin(ev: CQEvent) -> bool:
    return priv.check_priv(ev, priv.ADMIN)


@sv.on_fullmatch("wplace", "wplace帮助")
async def cmd_help(bot, ev: CQEvent):
    await bot.send(ev, HELP)


@sv.on_fullmatch("wplace状态")
async def cmd_status(bot, ev: CQEvent):
    try:
        orch = _get_orch(reload_cfg=True)
        lines = ["【Wplace 监控状态】"] + orch.status_lines()
        await bot.send(ev, "\n".join(lines))
    except Exception as e:
        await bot.send(ev, f"读取状态失败：{e}")


@sv.on_prefix("wplace检查")
async def cmd_check(bot, ev: CQEvent):
    if not _is_admin(ev):
        await bot.send(ev, "需要群管理权限")
        return
    arg = ev.message.extract_plain_text().strip()
    try:
        orch = _get_orch(reload_cfg=True)
    except Exception as e:
        await bot.send(ev, f"配置错误：{e}")
        return

    keys: List[str]
    if arg:
        keys = [arg]
    else:
        keys = [k for k, a in (orch.settings.get("arts") or {}).items() if a.get("track")]
        if not keys:
            await bot.send(ev, "没有 track=true 的画")
            return

    # notify 结果必须走 broadcast：refresh_one 会写入 alerted_once，
    # 若只回当前群，其它已启用群会永久错过首次破坏告警。
    replies = []
    for key in keys:
        try:
            result = await _run_refresh(key, orch)
            if result.get("notify"):
                await _broadcast_result(result)
                replies.append(f"{key}: {result.get('status')}（已推送到启用群）")
            else:
                replies.append(f"{key}: {result.get('status')} — {result.get('message')}")
        except Exception as e:
            replies.append(f"{key}: error — {e}")
    if replies:
        await bot.send(ev, "\n".join(replies))


async def _do_reset(bot, ev: CQEvent, arg: str):
    if not _is_admin(ev):
        await bot.send(ev, "需要群管理权限")
        return
    try:
        orch = _get_orch(reload_cfg=True)
    except Exception as e:
        await bot.send(ev, f"配置错误：{e}")
        return

    arts = orch.settings.get("arts") or {}
    if not arg or arg.lower() == "all":
        keys = [k for k, a in arts.items() if a.get("track")]
    else:
        keys = [arg]

    if not keys:
        await bot.send(ev, "没有可重设的画")
        return

    lines = ["[Wplace]重设基准："]
    for key in keys:
        try:
            result = await _run_baseline(key, orch)
            ok = result.get("status") == "baseline"
            lines.append(f"{'✓' if ok else '✗'} {key}: {result.get('message')}")
        except Exception as e:
            lines.append(f"✗ {key}: {e}")
    await bot.send(ev, "\n".join(lines))


@sv.on_prefix("wplace重设")
async def cmd_reset(bot, ev: CQEvent):
    arg = ev.message.extract_plain_text().strip()
    await _do_reset(bot, ev, arg)


@sv.on_fullmatch("刷新")
async def cmd_refresh_alias(bot, ev: CQEvent):
    """原 yukari「刷新」语义：全部重设基准。"""
    await _do_reset(bot, ev, "all")
