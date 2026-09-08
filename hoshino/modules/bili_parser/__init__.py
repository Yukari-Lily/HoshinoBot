from hoshino import Service, priv
from hoshino.util import load_yaml_config

from .core import extract_message_content, parse_and_render, parse_links


DEFAULT_CONFIG = {
    "show_quote": False,
    "parse_limit": 3,
    "delimiter": "------",
    "video_full_url": False,
}
config = DEFAULT_CONFIG.copy()
config.update(load_yaml_config("bili_parser", {}))

sv = Service(
    "bili-parser",
    use_priv=priv.NORMAL,
    manage_priv=priv.ADMIN,
    visible=True,
    enable_on_default=True,
    help_="自动解析 Bilibili 视频、直播、番剧、动态、专栏和音频链接",
    bundle="链接解析",
)


@sv.on_message("group")
async def bili_parser(bot, ev):
    content = extract_message_content(ev.message)
    if not parse_links(content, video_full_url=bool(config["video_full_url"])):
        return

    results = await parse_and_render(
        content,
        parse_limit=int(config["parse_limit"]),
        video_full_url=bool(config["video_full_url"]),
    )
    if not results:
        return

    delimiter = "\n%s\n" % str(config.get("delimiter") or "------")
    message = delimiter.join(results)
    if config.get("show_quote"):
        message = "[CQ:reply,id=%s]\n%s" % (ev.message_id, message)
    await bot.send(ev, message)
