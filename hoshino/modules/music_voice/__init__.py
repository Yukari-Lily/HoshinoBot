import asyncio
import re
import time
from typing import Dict, Tuple

from nonebot import MessageSegment
from nonebot.command import SwitchException

from hoshino import Service, priv
from hoshino.util import load_yaml_config

from .core import render_song_list, resolve_song_source, search_netease


DEFAULT_CONFIG = {
    "generation_tip": "稍等喵…",
    "image_mode": True,
    "search_list_count": 10,
    "selection_timeout": 45,
    "max_song_duration_minutes": 30,
}
config = DEFAULT_CONFIG.copy()
config.update(load_yaml_config("music_voice", {}))

sv = Service(
    "music-voice",
    use_priv=priv.NORMAL,
    manage_priv=priv.ADMIN,
    visible=True,
    enable_on_default=True,
    help_="点歌 歌名：搜索网易云歌曲并以语音发送",
    bundle="音乐",
)

# key: (机器人 QQ、群号、用户 QQ)
_pending: Dict[Tuple[int, int, int], dict] = {}


def _session_key(ev) -> Tuple[int, int, int]:
    return int(ev.self_id), int(ev.group_id), int(ev.user_id)


def _message_id(result):
    if isinstance(result, dict):
        return result.get("message_id")
    return result if isinstance(result, int) else None


async def _delete_message(bot, ev, message_id):
    if message_id is None:
        return
    try:
        await bot.delete_msg(self_id=ev.self_id, message_id=message_id)
    except Exception:
        sv.logger.debug("撤回点歌流程消息失败：%s", message_id)


async def _expire_selection(bot, ev, key: Tuple[int, int, int], created_at: float):
    await asyncio.sleep(max(1, int(config["selection_timeout"])))
    selection = _pending.get(key)
    if selection and selection["created_at"] == created_at:
        _pending.pop(key, None)
        await _delete_message(bot, ev, selection.get("list_message_id"))


@sv.on_prefix("点歌")
async def music_search(bot, ev):
    keyword = ev.message.extract_plain_text().strip()
    if not keyword:
        await bot.send(ev, "请输入歌曲名，例如：点歌 晴天")
        return

    key = _session_key(ev)
    previous = _pending.pop(key, None)
    if previous and previous.get("timeout_task"):
        previous["timeout_task"].cancel()
    if previous:
        await _delete_message(bot, ev, previous.get("list_message_id"))

    try:
        songs = await search_netease(keyword, max(1, int(config["search_list_count"])))
    except Exception as exc:
        sv.logger.exception(exc)
        await bot.send(ev, "获取歌曲列表失败，请稍后再试")
        return

    if not songs:
        await bot.send(ev, "没有找到相关歌曲")
        return

    if config.get("image_mode", True):
        song_list = str(MessageSegment.image(render_song_list(songs, keyword)))
    else:
        lines = ["%d. %s -- %s -- %s" % (
            index, song["name"], song["artists"], song["album"]
        ) for index, song in enumerate(songs, 1)]
        song_list = "NetEase Music:\n" + "\n".join(lines)
    result = await bot.send(ev, song_list)
    created_at = time.monotonic()
    selection = {
        "songs": songs,
        "created_at": created_at,
        "timeout_task": None,
        "list_message_id": _message_id(result),
    }
    _pending[key] = selection
    selection["timeout_task"] = asyncio.ensure_future(_expire_selection(bot, ev, key, created_at))


@sv.on_rex(re.compile(r"^[1-9]\d*$"), normalize=False)
async def music_select(bot, ev):
    key = _session_key(ev)
    selection = _pending.get(key)
    if not selection:
        raise SwitchException()

    answer = ev.message.extract_plain_text().strip()
    index = int(answer) - 1
    songs = selection["songs"]
    if index < 0 or index >= len(songs):
        raise SwitchException()

    _pending.pop(key, None)
    timeout_task = selection.get("timeout_task")
    if timeout_task:
        timeout_task.cancel()

    song = songs[index]
    max_duration = max(1, int(config["max_song_duration_minutes"])) * 60 * 1000
    if song.get("duration", 0) > max_duration:
        await _delete_message(bot, ev, selection.get("list_message_id"))
        await bot.send(ev, "歌曲时长超出限制")
        return

    tip = str(config.get("generation_tip") or "").strip()
    tip_message_id = None
    if tip:
        tip_message_id = _message_id(await bot.send(ev, tip))

    try:
        source = await resolve_song_source(int(song["id"]))
        await bot.send(ev, MessageSegment.record(source))
    except Exception as exc:
        sv.logger.exception(exc)
        await bot.send(ev, "获取歌曲失败，请稍后再试")
    finally:
        await _delete_message(bot, ev, tip_message_id)
        await _delete_message(bot, ev, selection.get("list_message_id"))
