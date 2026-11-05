import jinja2
import os
import random
from quart import request, session

template_folder = os.path.join(os.path.dirname(__file__), 'templates')
env = jinja2.Environment(
    loader=jinja2.FileSystemLoader(template_folder),
    enable_async=True
)


async def render_template(template, **kwargs):
    # 自动注入 request 与 session 上下文，便于模板中读取路径与状态
    if 'request' not in kwargs:
        try:
            kwargs['request'] = request
        except Exception:
            pass
    if 'session' not in kwargs:
        try:
            kwargs['session'] = session
        except Exception:
            pass
    t = env.get_template(template)
    return await t.render_async(**kwargs)


def get_random_str(num: int):
    salt = ''
    for i in range(num):
        salt += random.choice('ABCDEFGHIJKLMNOPQRSTUVWXYZ')
    return salt
