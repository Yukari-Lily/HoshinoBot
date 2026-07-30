import nonebot
from hoshino.config import HOST, PORT

# bot manager web 面板外网地址：从 hoshino/config/bot_manager_web.yaml 读取，留空则用 HOST:PORT
from hoshino.util import load_yaml_config
_bw_cfg = load_yaml_config('bot_manager_web')
PUBLIC_ADDRESS = _bw_cfg.get('public_address') or f'http://{HOST}:{PORT}'

bot = nonebot.get_bot()


@bot.on_message('private')
async def setting(ctx):
    message = ctx['raw_message']
    if message == 'bot设置':
        await bot.send(ctx, f'{PUBLIC_ADDRESS}/manage', at_sender=False)
