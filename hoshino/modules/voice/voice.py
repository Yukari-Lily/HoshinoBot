"""voice 插件:关键词触发发送语音 / 图片

合并自 uma_voi(马娘角色语音)+ cha_voi(其他语音),仍保留两个 Service,
可在不同群里分别 `启用/禁用`(lssv 里是 uma_voi 和 cha_voi 两项)。

============================================================
改动指南(新增一条语音就照这个来)
============================================================
1. 加到哪个 Service?
   - cha_voi(其他语音):日常梗、其他、表情 —— 经常加,改下面【一、其他语音】里的表
   - uma_voi(马娘角色语音):某只马娘的角色语音 —— 基本固定,改下面【二、马娘角色语音】里的表

2. 哪种动作?照着对应表里已有的一行抄,改关键词和路径即可:

   【一、其他语音】4 种动作,格式如下(path 都是相对 res/ 的):
     · 单文件     ('keyword', ('关键词1', '关键词2'), 'voi/文件.wav')
     · 文件夹随机  ('keyword', ('关键词',), 'cha_voi/文件夹')        # 从该文件夹随机选一个发
     · 语音+图片   {'type':'keyword', 'keywords':(...), 'voice':'voi/x.wav', 'image':'[CQ:image,...]'}
     · 纯图片     {'type':'keyword', 'keywords':(...), 'image':'[CQ:image,...]'}

   【二、马娘角色语音】3 张表(动作固定,只填路径和关键词):
     · KEYWORD_VOICES  (含关键词即触发,文件夹随机): ('uma_voi/voi/1003东海帝王', ['帝王', '帝宝', ...])
     · FULLMATCH_VOICES(完全匹配才触发,文件夹随机): ('uma_voi/muli', ['牡蛎'])
     · MULI_SPECIFIC   (完全匹配,指定文件):        ('uma_voi/muli/xxx.wav', ['牡蛎帝王', ...])

3. 触发方式:
   - 'keyword' = 消息里含关键词就触发(最常用)
   - 'prefix'  = 消息以关键词开头才触发(其他表支持,马娘表不用)
   - 马娘部分:KEYWORD_VOICES 用 keyword;FULLMATCH_VOICES / MULI_SPECIFIC 用 fullmatch(消息必须完全等于)

4. 资源路径:都相对 res/ 目录。
     R.get('voi/喵.wav')      -> res/voi/喵.wav
     R.get('cha_voi/mika')     -> res/cha_voi/mika/(整个文件夹)
   把语音/图片文件放进 res/ 对应位置即可。

5. 改完保存,重启 bot(或重载 voice 模块)生效。

不用动的:下面【三、引擎】里的 helper 和注册循环,它们负责把上面的表接上触发器。
文件找不到 / 发送失败会自动记日志,不会让 bot 崩。
============================================================
"""

import os
import random
from nonebot import MessageSegment
from nonebot.exceptions import CQHttpError
from hoshino import R, Service, priv

# 两个 Service:可分别开关
sv_uma = Service(
    name='uma_voi',
    use_priv=priv.NORMAL,
    manage_priv=priv.ADMIN,
    visible=True,
    enable_on_default=True,
)
sv_cha = Service(
    name='cha_voi',
    use_priv=priv.NORMAL,
    manage_priv=priv.ADMIN,
    visible=True,
    enable_on_default=True,
)


# ============================================================
# 一、其他语音 (cha_voi) —— 日常梗/其他,经常加,改这里
# ============================================================

# 单文件:('触发方式', ('关键词', ...), '资源路径')
VOICE_TRIGGERS = [
    ('keyword', ('早八', '早8'), 'voi/天天早八.wav'),
    ('keyword', ('邦邦个邦', '帮帮噶绑', '邦邦', '帮帮', 'bbgb'), 'ba_voi/bang.mp3'),
    ('keyword', ('好热', 'あつい'), 'ba_voi/热.m4a'),
    ('keyword', ('好冷', 'さむい'), 'ba_voi/冷.m4a'),
    ('keyword', ('嗯哼哼', '睡午觉'), 'ba_voi/hoshino-sleep.mp3'),
    ('keyword', ('圣诞', 'christmas'), 'ba_voi/christmas.mp3'),
    ('keyword', ('padoru',), 'voi/padoru.wav'),
    ('keyword', ('帝宝歌', 'hachimi', '哈吉米'), 'voi/帝宝hachimi.m4a'),
    ('keyword', ('喜欢', 'suki'), 'voi/オマエ、チュキ.wav'),
    ('keyword', ('叉了你', '杀了你', '鲨'), 'voi/オマエ、コロチュ.wav'),
    ('keyword', ('女仆', 'maid', 'メイド', 'konoha'), 'voi/konoha.wav'),
    ('keyword', ('vtb',), 'voi/vtb.wav'),
    ('keyword', ('喵',), 'voi/喵.wav'),
    ('keyword', ('咕咕', '咕嘎', '嘎嘎'), 'voi/guga.mp3'),
    ('keyword', ('菲比', '啾比'), 'voi/feibi.mp3'),
    ('keyword', ('灵感',), 'voi/曼波今日份灵感菇.mp3'),
    ('keyword', ('糯糯',), 'voi/糯糯.mp3'),
]

# 文件夹随机:('触发方式', ('关键词', ...), '文件夹路径')  从文件夹随机选一个发
RANDOM_VOICE_TRIGGERS = [
    ('keyword', ('mika', '未花'), 'cha_voi/mika'),
    ('keyword', ('小可爱',), 'cha_voi/小可爱'),
    ('keyword', ('hoshino', '星野'), 'cha_voi/hoshino'),
    ('keyword', ('小笨狗',), 'cha_voi/小笨狗'),
]

# 语音+图片:{'type':'触发方式', 'keywords':(...), 'voice':'语音路径', 'image':'图片CQ码'}
SPECIAL_TRIGGERS = [
    {
        'type': 'keyword',
        'keywords': ('翻车鱼', '诗宝歌', '曼波'),
        'voice': 'voi/诗歌剧翻车鱼.m4a',
        'image': '[CQ:image,file=https://gchat.qpic.cn/gchatpic_new/937334498/2180961248-2852366586-09FD730B0BE4D4CD5C60923A7C652395/0]',
    },
]

# 纯图片:{'type':'触发方式', 'keywords':(...), 'image':'图片CQ码'}
IMAGE_TRIGGERS = [
    {
        'type': 'keyword',
        'keywords': ('zllr', '这就是llr', '这就是拉拉人', '也是转发'),
        'image': '[CQ:image,file=https://gchat.qpic.cn/gchatpic_new/502087768/1150304099-2424637944-1B348389B6A382B38F41B0A302811980/0]',
    },
]


# ============================================================
# 二、马娘角色语音 (uma_voi) —— 角色语音表,基本固定
# ============================================================

# 含关键词即触发 -> 从文件夹随机选一个语音
KEYWORD_VOICES = [
    ('uma_voi/yada', ['亚达', 'yada', '鸭蛋', '鸭达']),
    ('uma_voi/koba', ['口八', 'koba']),
    ('uma_voi/voi/1001特别周', ['特别周', '特别渣', '特别肥', '特别胖', '特别能吃', '小特', '斯佩酱', '斯佩夏尔维特']),
    ('uma_voi/voi/1002无声铃鹿', ['无声铃鹿', '铃鹿', 'B71', '识字卡']),
    ('uma_voi/voi/1003东海帝王', ['东海帝王', '帝王', '帝皇', '痛快铁奥', '铁奥', '帝宝']),
    ('uma_voi/voi/1004丸善斯基', ['司机', '姥爷', '老阿姨', '丸善斯基', '水司机']),
    ('uma_voi/voi/1005富士奇迹', ['富士', '奇迹', '富士奇石', '富士奇迹']),
    ('uma_voi/voi/1006小栗帽', ['历战王', '帽子', '欧鼓励', '小礼帽', '圣诞帽', '小栗帽', '帽宝']),
    ('uma_voi/voi/1007黄金船', ['皮皮船', '船', '啊船', '阿船', '船宝', '黄金船']),
    ('uma_voi/voi/1008伏特加', ['伏特加']),
    ('uma_voi/voi/1009大和赤骥', ['大和', '红字白字', '大和赤骥', '斯卡雷特']),
    ('uma_voi/voi/1010大树快车', ['大树', '大树慢车', '小树慢车', '树宝', '大树快车']),
    ('uma_voi/voi/1011草上飞', ['草女士', '那我呢', '今天的小特不是我的对手', '草上飞', '啊草', '阿草']),
    ('uma_voi/voi/1012菱亚马逊', ['亚马逊', '终点牌', '菱亚马逊']),
    ('uma_voi/voi/1013目白麦昆', ['麦昆', '昆宝', '目白麦昆']),
    ('uma_voi/voi/1014神鹰', ['神鹰', '诶路']),
    ('uma_voi/voi/1015好歌剧', ['好歌剧']),
    ('uma_voi/voi/1016成田白仁', ['成田白仁', '白仁']),
    ('uma_voi/voi/1017鲁道夫象征', ['鲁道夫', '豆腐', '会长', '露娜', '鲁道夫象征']),
    ('uma_voi/voi/1018气槽', ['女帝', '副会长', '气槽']),
    ('uma_voi/voi/1019爱丽数码', ['数码', 'dd', '爱丽数码', '尊', '尊死']),
    ('uma_voi/voi/1020星云天空', ['星云', 'sky', '星宝', '星云天空']),
    ('uma_voi/voi/1021玉藻十字', ['tama', '玉藻', 'tm', '玉藻十字', '藻子哥']),
    ('uma_voi/voi/1022美妙姿势', ['美妙', '姿势', '美妙姿势', '美味芝士']),
    ('uma_voi/voi/1023琵琶晨光', ['大头', '琵琶晨光']),
    ('uma_voi/voi/1024重炮', ['玛雅', '玛雅诺冲锋枪', '重宝', '炮宝', '重炮']),
    ('uma_voi/voi/1025曼城茶座', ['茶座', '插座', '小仓唯', '曼哈顿咖啡', 'xcw', '曼城茶座']),
    ('uma_voi/voi/1026美浦波旁', ['波旁', '赛博朋马', '亲啵', '情勃', '美浦波旁', '布鲁泵']),
    ('uma_voi/voi/1027目白赖恩', ['莱恩', '赖恩', '雷恩', '目白赖恩']),
    ('uma_voi/voi/1028菱曙', ['凌署', '凌曙', '菱署', '菱曙']),
    ('uma_voi/voi/1029雪之美人', ['雪之美人']),
    ('uma_voi/voi/1030米浴', ['大米洗澡', '小祖宗', '米宝', '米浴']),
    ('uma_voi/voi/1031艾尼斯风神', ['风神', '艾尼斯风神']),
    ('uma_voi/voi/1032爱丽速子', ['速子', '爱丽速子']),
    ('uma_voi/voi/1033爱慕织姬', ['织姬', '爱慕织姬']),
    ('uma_voi/voi/1034稻荷一', ['稻荷一']),
    ('uma_voi/voi/1035胜利奖券', ['奖券', '胜利奖券', '小哭包']),
    ('uma_voi/voi/1036空中神宫', ['神宫', '空中神宫']),
    ('uma_voi/voi/1037荣进闪耀', ['荣进', '德国女仆', '阿荣', '啊荣', '荣耀闪进']),
    ('uma_voi/voi/1038真机伶', ['可怜酱', '可怜', '华恋', '真机灵', '真机伶', '怜宝']),
    ('uma_voi/voi/1039川上公主', ['川上', '川上公主']),
    ('uma_voi/voi/1040黄金城', ['黄金城市', '黄金城']),
    ('uma_voi/voi/1041樱花进王', ['进王', '爆进', '暴进', '爆进王', '暴进王', '樱花暴进王', '樱花进王']),
    ('uma_voi/voi/1042采珠', ['采珠']),
    ('uma_voi/voi/1043"新光风"', ['新光风', '新风光']),
    ('uma_voi/voi/1044东商变革', ['东尚', '东商', '魔女', '小魔女', '东商变革', '小魔吕']),
    ('uma_voi/voi/1045超级小海湾', ['小海湾', '海湾', '大海湾', '超级大海湾', '海湾妈妈', '超级小溪', '超级小海湾', '妈咪']),
    ('uma_voi/voi/1046醒目飞鹰', ['飞鹰', '飞鹰子', '寄', '醒目飞鹰']),
    ('uma_voi/voi/1047荒漠英雄', ['荒漠', '荒漠英雄']),
    ('uma_voi/voi/1048东瀛佐敦', ['辣妹', '佐敦', '东瀛佐敦']),
    ('uma_voi/voi/1049中山庆典', ['中山庆典']),
    ('uma_voi/voi/1050成田大进', ['大进', '成田小进', '小进', '迫影人', '迫影', '成田大进']),
    ('uma_voi/voi/1051西野花', ['西野花']),
    ('uma_voi/voi/1052春乌拉拉', ['乌拉拉', '春丽', 'urara']),
    ('uma_voi/voi/1053青竹回忆', ['青竹回忆']),
    ('uma_voi/voi/1054周日宁静', ['周日宁静', '周日']),
    ('uma_voi/voi/1055微光飞驹', ['微光', '飞驹', '微光飞驹']),
    ('uma_voi/voi/1056待兼福来', ['福来', '神棍', '占卜师', '半仙', '待兼福来']),
    ('uma_voi/voi/1057千明代表', ['千明代表', 'cb']),
    ('uma_voi/voi/1058名将怒涛', ['怒涛', '户仁', '名将怒涛']),
    ('uma_voi/voi/1059目白多伯', ['多伯', '目白多伯', '多勃', '多啵']),
    ('uma_voi/voi/1060优秀素质', ['好天资', '素质女士', '内恰', '内恰女士', '三着', '三着女士', '三角之外', '八方人', '素质极好', '今天也是开心的一天', '优秀素质', '好素质']),
    ('uma_voi/voi/1061帝王光辉', ['光辉', '帝王光环', '圣王光环', '圣王光辉', '光环', 'halo', '环环', '王环环', '帝王光辉']),
    ('uma_voi/voi/1062待兼诗歌剧', ['诗宝', 'AAO', '待兼诗歌剧', '咚啪嗯', '诗歌剧']),
    ('uma_voi/voi/1063生野狄杜斯', ['生野狄杜斯']),
    ('uma_voi/voi/1064目白善信', ['目白善信', '善信']),
    ('uma_voi/voi/1065大拓太阳神', ['大拓太阳神', '太阳神']),
    ('uma_voi/voi/1066双涡轮', ['双涡轮', '哒宝', '双宝', '挞宝']),
    ('uma_voi/voi/1067里见光钻', ['光钻', '钻哥', '里见光钻', '钻宝']),
    ('uma_voi/voi/1068北部玄驹', ['北黑', '侑开始了', '悸动战士', 'harikite', '小北', '北嗨', '北部玄驹']),
    ('uma_voi/voi/1069樱花千代王', ['千代王', '樱花千代王', '小千代']),
    ('uma_voi/voi/1070天狼星象征', ['天狼星象征']),
    ('uma_voi/voi/1071目白阿尔丹', ['阿尔丹', '中国媳妇', '目白阿尔丹', '中国媳妇儿']),
    ('uma_voi/voi/1072八重无敌', ['八重', '八重无敌']),
    ('uma_voi/voi/1073鹤丸刚志', ['鹤丸刚志']),
    ('uma_voi/voi/1074目白光明', ['光明', '目白光明']),
    ('uma_voi/voi/1077成田路', ['路哥', '成田路']),
    ('uma_voi/voi/1098小林历奇', ['小林', '历奇']),
    ('uma_voi/voi/2001快乐米可', ['米可', '快乐温顺', '快乐米可', 'happy milk', '嗨皮米可']),
]

# 完全匹配才触发 -> 从文件夹随机选一个语音
FULLMATCH_VOICES = [
    ('uma_voi/muli', ['牡蛎']),
]

# 完全匹配才触发 -> 发指定文件 ('wav文件路径', ['关键词', ...])
MULI_SPECIFIC = [
    ('uma_voi/muli/snd_voi_race_101600_8 -.wav', ['牡蛎成田白仁', '成田白仁牡蛎', '白仁牡蛎', '牡蛎白仁']),
    ('uma_voi/muli/snd_voi_race_101900_8 -.wav', ['牡蛎DD', 'DD牡蛎', '牡蛎dd', 'dd牡蛎']),
    ('uma_voi/muli/snd_voi_race_102100_8 -.wav', ['tm牡蛎', 'TM牡蛎', '牡蛎TAMA', 'TAMA牡蛎', '牡蛎tama', 'tama牡蛎']),
    ('uma_voi/muli/snd_voi_race_102200_8 -.wav', ['芝士牡蛎', '牡蛎芝士']),
    ('uma_voi/muli/snd_voi_race_102500_8 -.wav', ['牡蛎xcw', 'xcw牡蛎', 'XCW牡蛎', '牡蛎XCW']),
    ('uma_voi/muli/snd_voi_race_102800_8 -.wav', ['牡蛎菱曙', '菱曙牡蛎']),
    ('uma_voi/muli/snd_voi_race_103100_8 -.wav', ['牡蛎风神', '风神牡蛎']),
    ('uma_voi/muli/snd_voi_race_103300_8 -.wav', ['牡蛎织姬', '织姬牡蛎']),
    ('uma_voi/muli/snd_voi_race_103400_8 -.wav', ['牡蛎稻荷一', '稻荷一牡蛎']),
    ('uma_voi/muli/snd_voi_race_103600_8 -.wav', ['牡蛎神宫', '神宫牡蛎']),
    ('uma_voi/muli/snd_voi_race_103700_8 -.wav', ['牡蛎阿荣', '阿荣牡蛎']),
    ('uma_voi/muli/snd_voi_race_103900_8 -.wav', ['牡蛎川上', '川上牡蛎']),
    ('uma_voi/muli/snd_voi_race_104400_8 -.wav', ['牡蛎小魔女', '小魔女牡蛎']),
    ('uma_voi/muli/snd_voi_race_104500_8 -.wav', ['牡蛎小海湾', '小海湾牡蛎']),
    ('uma_voi/muli/snd_voi_race_104600_8 -.wav', ['牡蛎寄寄子', '寄寄子牡蛎']),
    ('uma_voi/muli/snd_voi_race_104800_8 -.wav', ['牡蛎佐敦', '佐敦牡蛎']),
    ('uma_voi/muli/snd_voi_race_105000_10 -.wav', ['牡蛎大进', '大进牡蛎']),
    ('uma_voi/muli/snd_voi_race_105100_8 -.wav', ['牡蛎西野花', '西野花牡蛎']),
    ('uma_voi/muli/snd_voi_race_105300_8 -.wav', ['牡蛎青竹', '青竹牡蛎']),
    ('uma_voi/muli/snd_voi_race_105800_8 -.wav', ['牡蛎怒涛', '怒涛牡蛎']),
    ('uma_voi/muli/snd_voi_race_105900_8 -.wav', ['牡蛎多勃', '多勃牡蛎', '牡蛎多啵', '多啵牡蛎', '牡蛎多伯', '多伯牡蛎']),
    ('uma_voi/muli/snd_voi_race_106200_8 -.wav', ['牡蛎诗宝', '诗宝牡蛎']),
    ('uma_voi/muli/snd_voi_race_106400_8 -.wav', ['牡蛎善信', '善信牡蛎']),
    ('uma_voi/muli/snd_voi_race_106700_8 -.wav', ['牡蛎钻宝', '钻宝牡蛎']),
    ('uma_voi/muli/snd_voi_race_106800_8 -.wav', ['牡蛎小北', '小北牡蛎']),
    ('uma_voi/muli/snd_voi_race_106900_8 -.wav', ['牡蛎小千代', '小千代牡蛎']),
    ('uma_voi/muli/snd_voi_race_107100_8 -.wav', ['牡蛎中国媳妇', '中国媳妇牡蛎', '牡蛎阿尔丹', '阿尔丹牡蛎']),
    ('uma_voi/muli/snd_voi_race_107200_8 -.wav', ['牡蛎八重', '八重牡蛎']),
    ('uma_voi/muli/snd_voi_race_107400_8 -.wav', ['牡蛎光明', '光明牡蛎']),
    ('uma_voi/muli/snd_voi_race_109800_8 -.wav', ['牡蛎小林', '小林牡蛎']),
]


# ============================================================
# 三、引擎(以下不用改:helper 负责发送,注册循环把上面的表接上触发器)
# ============================================================

async def send_file_voice(sv, bot, ev, res_path):
    """发送指定语音文件"""
    try:
        path = R.get(res_path)
        await bot.send(ev, MessageSegment.record(f'file:///{os.path.abspath(path.path)}'))
    except CQHttpError:
        sv.logger.error(f'语音发送失败: {res_path}')


async def send_folder_voice(sv, bot, ev, res_path):
    """从文件夹随机选一个语音文件发送"""
    folder = R.get(res_path)
    folder_abs = os.path.abspath(folder.path)
    try:
        files = [f for f in os.listdir(folder_abs) if os.path.isfile(os.path.join(folder_abs, f))]
    except FileNotFoundError:
        sv.logger.error(f'语音文件夹不存在: {res_path}')
        return
    if not files:
        sv.logger.error(f'语音文件夹为空: {res_path}')
        return
    filename = random.choice(files)
    try:
        await bot.send(ev, MessageSegment.record(f'file:///{os.path.join(folder_abs, filename)}'))
    except CQHttpError:
        sv.logger.error(f'语音发送失败: {res_path}')


def _make_file_handler(sv, res_path):
    """创建发送固定语音的 handler"""
    async def handler(bot, ev):
        await send_file_voice(sv, bot, ev, res_path)
    return handler


def _make_folder_handler(sv, folder_path):
    """创建从文件夹随机选一个语音发送的 handler"""
    async def handler(bot, ev):
        await send_folder_voice(sv, bot, ev, folder_path)
    return handler


# --- 其他语音 注册 ---
for _trig_type, _keywords, _res_path in VOICE_TRIGGERS:
    _handler = _make_file_handler(sv_cha, _res_path)
    if _trig_type == 'keyword':
        sv_cha.on_keyword(*_keywords)(_handler)
    elif _trig_type == 'prefix':
        sv_cha.on_prefix(*_keywords)(_handler)

for _trig_type, _keywords, _folder_path in RANDOM_VOICE_TRIGGERS:
    _handler = _make_folder_handler(sv_cha, _folder_path)
    if _trig_type == 'keyword':
        sv_cha.on_keyword(*_keywords)(_handler)
    elif _trig_type == 'prefix':
        sv_cha.on_prefix(*_keywords)(_handler)

for _item in SPECIAL_TRIGGERS:
    async def _special_handler(bot, ev, _voice=_item['voice'], _img=_item['image']):
        await send_file_voice(sv_cha, bot, ev, _voice)
        try:
            await bot.send(ev, _img)
        except CQHttpError:
            sv_cha.logger.error(f'图片发送失败: {_voice}')
    sv_cha.on_keyword(*_item['keywords'])(_special_handler)

for _item in IMAGE_TRIGGERS:
    async def _image_handler(bot, ev, _img=_item['image']):
        try:
            await bot.send(ev, _img)
        except CQHttpError:
            sv_cha.logger.error('图片发送失败')
    sv_cha.on_keyword(*_item['keywords'])(_image_handler)

# --- 马娘角色语音 注册 ---
for _res_path, _keywords in KEYWORD_VOICES:
    @sv_uma.on_keyword(*_keywords)
    async def _keyword_handler(bot, ev, _path=_res_path):
        await send_folder_voice(sv_uma, bot, ev, _path)

for _res_path, _keywords in FULLMATCH_VOICES:
    @sv_uma.on_fullmatch(*_keywords)
    async def _fullmatch_handler(bot, ev, _path=_res_path):
        await send_folder_voice(sv_uma, bot, ev, _path)

for _wav_path, _keywords in MULI_SPECIFIC:
    @sv_uma.on_fullmatch(*_keywords)
    async def _muli_specific_handler(bot, ev, _path=_wav_path):
        await send_file_voice(sv_uma, bot, ev, _path)
