import json
import time
from datetime import timedelta
import nonebot
from quart import request, session, redirect, Blueprint, jsonify
from hoshino import Service
from hoshino.util import load_yaml_config
import hoshino
from .http_handler import render_template, get_random_str

# 读取配置密码与外网地址
_bw_cfg = load_yaml_config('bot_manager_web')
PASSWORD = str(_bw_cfg.get("password") or "123456")

switcher = Blueprint('switcher', __name__, static_folder='statics', static_url_path='/statics', url_prefix="/manage")
bot = nonebot.get_bot()
app = bot.server_app
if not app.config.get('SECRET_KEY'):
    app.config['SECRET_KEY'] = get_random_str(16)

# 内存缓存群列表，默认 TTL 60 秒，避免频繁 CQHTTP 网络调用导致页面打开卡顿
_group_cache = {
    "data": [],
    "last_update": 0.0
}


async def get_groups(force_refresh: bool = False):
    """获取所有自机加入的群列表（带内存缓存与异常兜底）"""
    now = time.time()
    if not force_refresh and _group_cache["data"] and (now - _group_cache["last_update"] < 60.0):
        return _group_cache["data"]

    gl = []
    seen_ids = set()
    for sid in hoshino.get_self_ids():
        try:
            gl_ = await bot.get_group_list(self_id=sid)
            for g in gl_:
                gid = g.get('group_id')
                if gid not in seen_ids:
                    seen_ids.add(gid)
                    gl.append(g)
        except Exception as e:
            hoshino.logger.warning(f'[bot_manager_web] 获取 bot({sid}) 群列表失败: {e}')

    if gl:
        _group_cache["data"] = gl
        _group_cache["last_update"] = now
    elif _group_cache["data"]:
        # 兜底：如果拉取失败且有旧缓存，则复用旧缓存
        return _group_cache["data"]

    return gl


@switcher.before_request
async def _():
    user_ip = request.remote_addr
    if session.get('user_ip') == user_ip:
        return
    if request.path.startswith(switcher.url_prefix):
        r_path = request.path[len(switcher.url_prefix):]
        if r_path == '/login':
            return
        if r_path.startswith(switcher.static_url_path):
            return
        if r_path == '/check':
            return
        return redirect(switcher.url_prefix + '/login')


@switcher.route('/login', methods=['GET', 'POST'])
async def login():
    if request.method == 'GET':
        error = request.args.get('error')
        return await render_template(
            'login.html',
            title='登录',
            error=error,
            static_url_path=switcher.static_url_path,
            url_prefix=switcher.url_prefix
        )
    else:
        login_data = await request.form
        input_psd = str(login_data.get('password', ''))
        if input_psd == PASSWORD:
            user_ip = request.remote_addr
            session['user_ip'] = user_ip
            session.permanent = True
            app.permanent_session_lifetime = timedelta(weeks=2)
            next_url = login_data.get('next')
            if not next_url or not next_url.startswith(switcher.url_prefix):
                next_url = switcher.url_prefix + '/'
            return redirect(next_url)
        else:
            return redirect(switcher.url_prefix + '/login?error=1')


@switcher.route('/logout')
async def logout():
    session.pop('user_ip', None)
    return redirect(switcher.url_prefix + '/login')


@switcher.route('/')
async def manage():
    groups = await get_groups()
    svs = Service.get_loaded_services()
    bundles = Service.get_bundles()
    return await render_template(
        'manage.html',
        title='控制台首页',
        group_count=len(groups),
        service_count=len(svs),
        bundle_count=len(bundles),
        static_url_path=switcher.static_url_path,
        url_prefix=switcher.url_prefix
    )


@switcher.route('/group')
async def group_list():
    force_refresh = request.args.get('refresh') == '1'
    groups = await get_groups(force_refresh=force_refresh)
    svs = Service.get_loaded_services()

    # 计算各群启用的服务数量
    items = []
    total_sv = len(svs)
    for g in groups:
        gid = g.get('group_id')
        enabled_count = sum(1 for sv in svs.values() if sv.check_enabled(gid))
        items.append({
            'group_id': gid,
            'group_name': g.get('group_name', f'群 {gid}'),
            'enabled_count': enabled_count,
            'total_count': total_sv
        })

    return await render_template(
        'group_list.html',
        title='按群管理',
        items=items,
        static_url_path=switcher.static_url_path,
        url_prefix=switcher.url_prefix
    )


@switcher.route('/service')
async def service_list():
    groups = await get_groups()
    svs = Service.get_loaded_services()
    bundles = Service.get_bundles()

    # 建立 service -> bundle 映射
    sv_bundle_map = {}
    for b_name, b_svs in bundles.items():
        for s in b_svs:
            sv_bundle_map[s.name] = b_name

    all_bundles = sorted(list(bundles.keys()))
    items = []
    total_groups_count = len(groups)
    for name, sv in svs.items():
        enabled_groups_count = sum(1 for g in groups if sv.check_enabled(g['group_id']))
        b_name = sv_bundle_map.get(name, '通用')
        items.append({
            'name': name,
            'bundle': b_name,
            'enable_on_default': sv.enable_on_default,
            'visible': sv.visible,
            'enabled_groups_count': enabled_groups_count,
            'total_groups_count': total_groups_count
        })

    # 按模块和名称排序
    items.sort(key=lambda x: (x['bundle'], x['name']))

    return await render_template(
        'service_list.html',
        title='按服务管理',
        items=items,
        all_bundles=all_bundles,
        static_url_path=switcher.static_url_path,
        url_prefix=switcher.url_prefix
    )


@switcher.route('/group/<gid_str>')
async def show_group_services(gid_str: str):
    gid = int(gid_str)
    groups = await get_groups()
    group_info = next((g for g in groups if g['group_id'] == gid), {'group_id': gid, 'group_name': f'群 {gid}'})
    svs = Service.get_loaded_services()
    bundles = Service.get_bundles()

    sv_bundle_map = {}
    for b_name, b_svs in bundles.items():
        for s in b_svs:
            sv_bundle_map[s.name] = b_name

    conf = {gid_str: {}}
    services_data = []
    all_bundles = sorted(list(bundles.keys()))

    for key, sv in svs.items():
        is_enabled = sv.check_enabled(gid)
        conf[gid_str][key] = is_enabled
        b_name = sv_bundle_map.get(key, '通用')
        services_data.append({
            'name': key,
            'bundle': b_name,
            'enabled': is_enabled,
            'enable_on_default': sv.enable_on_default,
            'help': getattr(sv, 'help', '') or ''
        })

    services_data.sort(key=lambda x: (x['bundle'], x['name']))

    return await render_template(
        'group_services.html',
        title=f'{group_info["group_name"]} 服务配置',
        group_id=gid_str,
        group_info=group_info,
        conf=conf,
        services_data=services_data,
        all_bundles=all_bundles,
        static_url_path=switcher.static_url_path,
        url_prefix=switcher.url_prefix
    )


@switcher.route('/service/<sv_name>')
async def show_service_groups(sv_name: str):
    svs = Service.get_loaded_services()
    if sv_name not in svs:
        return redirect(switcher.url_prefix + '/service')

    sv = svs[sv_name]
    bundles = Service.get_bundles()
    sv_bundle = '通用'
    for b_name, b_svs in bundles.items():
        if any(s.name == sv_name for s in b_svs):
            sv_bundle = b_name
            break

    groups = await get_groups()
    conf = {}
    groups_data = []

    for group in groups:
        gid = group['group_id']
        gid_str = str(gid)
        conf[gid_str] = {}
        is_enabled = sv.check_enabled(gid)
        conf[gid_str][sv_name] = is_enabled
        groups_data.append({
            'group_id': gid,
            'group_name': group.get('group_name', f'群 {gid}'),
            'enabled': is_enabled
        })

    return await render_template(
        'service_groups.html',
        title=f'服务 {sv_name} 群授权',
        sv_name=sv_name,
        sv_bundle=sv_bundle,
        sv_default_enabled=sv.enable_on_default,
        conf=conf,
        groups_data=groups_data,
        static_url_path=switcher.static_url_path,
        url_prefix=switcher.url_prefix
    )


@switcher.route('/set', methods=['POST'])
async def set_group():
    # 接收前端传来的配置数据，数据格式{"<gid>":{'serviceA':True,'serviceB':False}}
    data = await request.get_data()
    conf = json.loads(data.decode('utf-8'))
    svs = Service.get_loaded_services()
    changed_count = 0

    for k in conf:
        gid = int(k)
        for sv_name in conf[k]:
            if sv_name not in svs:
                continue
            target_state = bool(conf[k][sv_name])
            current_state = svs[sv_name].check_enabled(gid)
            # 性能关键优化：仅当状态真正发生变动时才写磁盘，避免 50+ 次磁盘 I/O
            if target_state != current_state:
                if target_state:
                    svs[sv_name].set_enable(gid)
                    svs[sv_name].logger.info(f'[bot_manager_web] 启用群 {gid} 服务 {sv_name}')
                else:
                    svs[sv_name].set_disable(gid)
                    svs[sv_name].logger.info(f'[bot_manager_web] 禁用群 {gid} 服务 {sv_name}')
                changed_count += 1

    return jsonify({'code': 0, 'message': '保存成功', 'changed': changed_count})
