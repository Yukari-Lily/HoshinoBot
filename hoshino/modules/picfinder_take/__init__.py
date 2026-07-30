import asyncio
from asyncio import sleep
from datetime import datetime, timedelta

from nonebot import get_bot

import hoshino
from hoshino import Service, log, priv
from hoshino.typing import CQEvent
from hoshino.util import DailyNumberLimiter, load_yaml_config
from hoshino.config import NICKNAME
from aiocqhttp.exceptions import ActionFailed

from .image import get_image_data_sauce, get_image_data_ascii

_cfg = load_yaml_config('picfinder_take')
threshold = _cfg.get('threshold', 70)
SAUCENAO_KEY = str(_cfg.get('saucenao_key', '') or '')
SEARCH_TIMEOUT = int(_cfg.get('search_timeout', 60))
CHAIN_REPLY = bool(_cfg.get('chain_reply', True))
DAILY_LIMIT = int(_cfg.get('daily_limit', 100))
helptext = _cfg.get('helptext') or '''搜图功能
- 识图/搜图/查图/找图 + 图片
- 图片 + 识图/搜图/查图/找图
- 回复图片 + 识图
- @机器人 + 图片（批量搜索模式）'''
_eg = _cfg.get('enableguild') or {}
enableguild = {int(k): [int(x) for x in (v or [])] for k, v in _eg.items()}

if type(NICKNAME) == str:
    NICKNAME = [NICKNAME]


sv = Service('picfinder', help_=helptext)

lmtd = DailyNumberLimiter(DAILY_LIMIT)
logger = log.new_logger('image')


class PicListener:
    def __init__(self):
        self.on = {}
        self.count = {}
        self.limit = {}
        self.timeout = {}

    def get_on_off_status(self, gid):
        return self.on[gid] if self.on.get(gid) is not None else False

    def turn_on(self, gid, uid):
        self.on[gid] = uid
        self.timeout[gid] = datetime.now()+timedelta(seconds=SEARCH_TIMEOUT)
        self.count[gid] = 0
        self.limit[gid] = DAILY_LIMIT-lmtd.get_num(uid)

    def turn_off(self, gid):
        self.on.pop(gid)
        self.count.pop(gid)
        self.timeout.pop(gid)
        self.limit.pop(gid)

    def count_plus(self, gid):
        self.count[gid] += 1


pls = PicListener()


@sv.on_prefix(('识图', '搜图', '查图', '找图', '识图喵'), only_to_me=False)
async def start_finder(bot, ev: CQEvent):
    uid = ev.user_id
    gid = ev.group_id
    ret = None
    for m in ev.message:
        if m.type == 'image':
            file = m.data['file']
            url = m.data['url']
            ret=1
            break
    if not ret:
        if pls.get_on_off_status(gid):
            if uid == pls.on[gid]:
                pls.timeout[gid] = datetime.now()+timedelta(seconds=30)
                await bot.finish(ev, f"发，快发喵")
            else:
                await bot.finish(ev, f"[CQ:at,qq={pls.on[gid]}]正在搜图，请耐心等待喵~")
        pls.turn_on(gid, uid)
        await bot.send(ev, f"发，快发喵")
        await sleep(30)
        ct = 0
        while pls.get_on_off_status(gid):
            if datetime.now() < pls.timeout[gid]:
                if ct != pls.count[gid]:
                    ct = pls.count[gid]
                    pls.timeout[gid] = datetime.now()+timedelta(seconds=60)
            else:
                temp = pls.on[gid]
                if not pls.count[gid]:
                    await bot.send(ev, f"[CQ:at,qq={temp}] 我的图图呢喵？")
                else:
                    await bot.send(ev, f"怎么才{pls.count[gid]}张喵～")
                pls.turn_off(ev.group_id)
                break
            await sleep(30)
        return
    if not priv.check_priv(ev, priv.SUPERUSER):
        if not lmtd.check(uid):
            await bot.send(ev, f'累了喵，亚达亚达！', at_sender=True)
            return
    if 'c2cpicdw.qpic.cn/offpic_new/' in url:
        md5 = file[:-6].upper()
        url = f"http://gchat.qpic.cn/gchatpic_new/0/0-0-{md5}/0?term=2"
    await picfinder(bot, ev, url)


@sv.on_suffix(('识图', '搜图', '查图', '找图', '识图喵'), only_to_me=False)
async def suffix_finder(bot, ev: CQEvent):
    # 处理「图片在前、指令在后」的情况（on_prefix 只匹配指令在前的消息）
    # @机器人 与 连续搜图模式 仍交给 picmessage 处理，避免重复搜图
    if pls.get_on_off_status(ev.group_id) and int(pls.on[ev.group_id]) == int(ev.user_id):
        return
    for m in ev.message:
        if m.type == 'at' and m.data['qq'] == ev.self_id:
            return
    uid = ev.user_id
    file = url = None
    for m in ev.message:
        if m.type == 'image':
            file = m.data['file']
            url = m.data['url']
            break
    if not url:
        return
    if not priv.check_priv(ev, priv.SUPERUSER):
        if not lmtd.check(uid):
            await bot.send(ev, f'累了喵，亚达亚达！', at_sender=True)
            return
    if 'c2cpicdw.qpic.cn/offpic_new/' in url:
        md5 = file[:-6].upper()
        url = f"http://gchat.qpic.cn/gchatpic_new/0/0-0-{md5}/0?term=2"
    await picfinder(bot, ev, url)


@sv.on_message('group')
async def picmessage(bot, ev: CQEvent):
    atcheck = False
    batchcheck = False
    for m in ev.message:
        if m.type == 'at' and m.data['qq']==ev.self_id:
            atcheck = True
    if pls.get_on_off_status(ev.group_id):
        if int(pls.on[ev.group_id]) == int(ev.user_id):
            batchcheck = True
    if not(batchcheck or atcheck):
        return
    uid = ev.user_id
    ret = None
    for m in ev.message:
        if m.type == 'image':
            file = m.data['file']
            url = m.data['url']
            ret=1
            break
    if not ret:
        return
    if not priv.check_priv(ev, priv.SUPERUSER):
        if not lmtd.check(uid):
            await bot.send(ev, f'累了喵，亚达亚达！', at_sender=True)
            if pls.get_on_off_status(ev.group_id):
                pls.turn_off(ev.group_id)
                return
    if pls.get_on_off_status(ev.group_id):
        pls.count_plus(ev.group_id)
        if pls.count[ev.group_id] > pls.limit[ev.group_id]:
            await bot.send(ev, f'累了喵，亚达亚达！', at_sender=True)
            pls.turn_off(ev.group_id)
            return
    if 'c2cpicdw.qpic.cn/offpic_new/' in url:
        md5 = file[:-6].upper()
        url = f"http://gchat.qpic.cn/gchatpic_new/0/0-0-{md5}/0?term=2"
    await picfinder(bot, ev, url)


@sv.on_message('group')
async def replymessage(bot, ev: CQEvent):
    uid = ev.user_id
    seg=ev.message[0]
    if seg.type != 'reply':
        return
    tmid = seg.data['id']
    cmd = ev.message.extract_plain_text()
    flag1 = 0
    flag2 = 0
    for m in ev.message[2:]:
        if m.type == 'at' and m.data['qq'] == ev.self_id:
            flag1 = 1
    for name in NICKNAME:
        if name in cmd:
            flag1 = 1
            break
    for pfcmd in ['识图', '搜图', '查图', '找图', '识图喵']:
        if pfcmd in cmd:
            flag2 = 1
    if not (flag1 and flag2):
        return
    if not priv.check_priv(ev, priv.SUPERUSER):
        if not lmtd.check(uid):
            await bot.send(ev, f'累了喵，亚达亚达！', at_sender=True)
    try:
        tmsg = await bot.get_msg(self_id=ev.self_id, message_id=int(tmid))
    except ActionFailed:
        await bot.finish(ev, '消息过期了喵，重发重发~')
    file = ''
    url = ''
    for m in tmsg["message"]:
        if m["type"] == 'image':
            file = m['data']['file']
            url = m['data']['url']
            break
    if not file:
        await bot.send(ev, '未找到图片喵')
        return
    if 'c2cpicdw.qpic.cn/offpic_new/' in url:
        md5 = file[:-6].upper()
        url = f"http://gchat.qpic.cn/gchatpic_new/0/0-0-{md5}/0?term=2"
    await picfinder(bot, ev, url)


@sv.on_prefix('我都不看这些的喵')
async def thanks(bot, ev: CQEvent):
    gid = ev.group_id
    name = ev.message.extract_plain_text().strip()
    if name not in NICKNAME:
        return
    if pls.get_on_off_status(gid):
        if pls.on[gid] != ev.user_id:
            await bot.send(ev, '不能替别人结束搜图喵～')
            return
        if not pls.count[gid]:
            await bot.send(ev, f"超时了喵，图图是被夹了喵？")
        else:
            await bot.send(ev, f'好喵，一共发了{pls.count[gid]}张喵～')
        pls.turn_off(gid)
        logger.debug('turned off')
        return
    await bot.send(ev, 'にゃ～')

async def gsend(ev: CQEvent, msg):
    hbot = hoshino.get_bot()
    return await hbot.send_guild_channel_msg(
                        guild_id=ev.guild_id,
                        channel_id=ev.channel_id,
                        message=msg,
                        self_id=ev.self_id
                    )

async def chain_reply(bot, ev, chain, msg):
    if ev.detail_type == 'guild':
        await gsend(ev, msg)
        return chain
    if not CHAIN_REPLY:
        await bot.send(ev, msg)
        return chain
    else:
        data = {
            "type": "node",
            "data": {
                    "name": str(NICKNAME[0]) if str(NICKNAME[0]) else '柠檬睡了',
                    "user_id": str(ev.self_id),
                    "content": str(msg)
            }
        }
        chain.append(data)
        return chain


async def picfinder(bot, ev, image_data):
    uid = ev.user_id
    chain = []
    result = await get_image_data_sauce(image_data, SAUCENAO_KEY)
    image_data_report = result[0]
    simimax = result[1]
    if 'Index #' in image_data_report:
        await bot.send_private_msg(self_id=ev.self_id, user_id=bot.config.SUPERUSERS[0], message='发生index解析错误')
        await bot.send_private_msg(self_id=ev.self_id, user_id=bot.config.SUPERUSERS[0], message=image_data)
        await bot.send_private_msg(self_id=ev.self_id, user_id=bot.config.SUPERUSERS[0], message=image_data_report)
    chain = await chain_reply(bot, ev, chain, image_data_report)

    if float(simimax) > float(threshold):
        lmtd.increase(uid)
    else:
        if simimax != 0:
            chain = await chain_reply(bot, ev, chain, "相似度过低喵，换用ascii2d检索中…")
        else:
            logger.error("SauceNao not found imageInfo")
            chain = await chain_reply(bot, ev, chain, 'SauceNao检索失败喵,换用ascii2d检索中…')

        image_data_report = await get_image_data_ascii(image_data)
        if image_data_report[0]:
            chain = await chain_reply(bot, ev, chain, image_data_report[0])
            lmtd.increase(uid)
        if image_data_report[1]:
            chain = await chain_reply(bot, ev, chain, image_data_report[1])
        if not (image_data_report[0] or image_data_report[1]):
            logger.error("ascii2d not found imageInfo")
            chain = await chain_reply(bot, ev, chain, '呜呜呜，ascii2d也检索失败喵，牡蛎~')

    if CHAIN_REPLY and (ev.detail_type != 'guild'):
        await bot.send_group_forward_msg(group_id=ev['group_id'], messages=chain)
bot = get_bot()
@bot.on_message('guild')
async def gpicfinder(ev: CQEvent):
    if (tid := ev.user_id) == ev.self_tiny_id:
        return
    if int(ev.channel_id) not in enableguild.get(int(ev.guild_id), []):
        return
    ret = []
    for i in ev.message:
        if i['type'] == 'image':
            ret.append(i['data']['url'])
    if not ret:
        return
    if not lmtd.check(tid):
        await gsend(ev, f'累了喵，亚达亚达！')
        return
    await gsend(ev, '正在搜索，稍等喵～')
    bot = hoshino.get_bot()
    for url in ret:
        asyncio.get_event_loop().create_task(picfinder(bot, ev, url))
