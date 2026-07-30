import json
import os
import re

import httpx
from PIL import Image
from bs4 import BeautifulSoup
from hoshino import R, logger

from ..plugin_utils.base_util import get_img_cq

# 图片文件名正则：匹配 "任意前缀+卡类型+版本号榜?.png"
# 例如：温泉乡智2.22.5榜.png / 台服速2.2.1榜.png / 国服毅力1.15.5榜.png
IMG_PATTERN_TMPL = r'(\S+?)({sup_type})(\S*?)([0-9]+)\.([0-9]+)\.([0-9]+)(榜\S*)?\.png'

# 各服务器合集页的完整href（从实际HTML中确认）
COLLECTION_HREF_MAP = {
    '日服':  'https://wiki.biligame.com/umamusume/%E6%94%AF%E6%8F%B4%E5%8D%A1%E8%8A%82%E5%A5%8F%E6%A6%9C%E5%90%88%E9%9B%86',
    '繁中服': 'https://wiki.biligame.com/umamusume/%E6%94%AF%E6%8F%B4%E5%8D%A1%E8%8A%82%E5%A5%8F%E6%A6%9C%E5%90%88%E9%9B%86(%E7%B9%81%E4%B8%AD%E6%9C%8D)',
    '简中服': 'https://wiki.biligame.com/umamusume/%E6%94%AF%E6%8F%B4%E5%8D%A1%E8%8A%82%E5%A5%8F%E6%A6%9C%E5%90%88%E9%9B%86%EF%BC%88%E7%AE%80%E4%B8%AD%E6%9C%8D%EF%BC%89',
}


# 获取各个节奏榜的链接
async def get_title_url(sup_type, server_name):
    if server_name not in COLLECTION_HREF_MAP:
        raise ValueError(f'未知服务器名: {server_name}')

    # 第一跳：直接用已知href访问合集页
    collection_url = COLLECTION_HREF_MAP[server_name]
    logger.info(f'{server_name}合集页链接: {collection_url}')

    res = httpx.get(collection_url, timeout=15)
    soup = BeautifulSoup(res.text, 'lxml')

    # 第二跳：合集页 → 具体卡榜页，用title属性匹配
    if server_name == '日服':
        tag = soup.find('a', {"title": re.compile(fr'^日服{sup_type}卡节奏榜')})
    else:
        tag = soup.find('a', {"title": re.compile(fr'^((?!SR).)*{sup_type}卡节奏榜\S+$')})

    if not tag:
        raise ValueError(f'未找到{server_name}{sup_type}卡节奏榜链接')

    href = tag.get('href', '')
    if href.startswith('http'):
        chart_url = href
    else:
        chart_url = 'https://wiki.biligame.com/umamusume/' + tag.get('title', '')

    logger.info(f'{server_name}{sup_type}卡节奏榜页链接: {chart_url}')
    return chart_url


# 生成字典
async def generate_url(sup_type, server_name):
    current_dir, _ = await get_config(server_name)
    with open(current_dir, 'r', encoding='UTF-8') as f:
        img_dict = json.load(f)
    img_path = os.path.join(R.img('umamusume').path, 'uma_support_chart/')
    if not os.path.exists(img_path):
        os.mkdir(img_path)
    chart_url = await get_title_url(sup_type, server_name)
    logger.info(f'chart_url={chart_url}')
    if not img_dict.get(sup_type, None):
        logger.info(f'配置文件内未找到{server_name}{sup_type}卡节奏榜相关配置，现已成功创建')
        img_dict[sup_type] = {}
    img_dict[sup_type]['chart_url'] = chart_url
    img_dict, is_update = await get_img(img_dict, sup_type, chart_url, server_name)
    with open(current_dir, 'w', encoding='UTF-8') as f:
        json.dump(img_dict, f, indent=4, ensure_ascii=False)
    return img_dict, is_update


# 获取图像版本信息
async def get_img(img_dict, sup_type, chart_url, server_name):
    pattern = re.compile(IMG_PATTERN_TMPL.format(sup_type=sup_type))

    res = httpx.get(chart_url, timeout=10)
    soup = BeautifulSoup(res.text, 'lxml')
    img_soup_list = soup.find_all('img', {"decoding": "async"})

    ver_tmp = None
    for img_soup in img_soup_list:
        alt = img_soup.get('alt', '')
        ver_tmp = re.match(pattern, alt)
        if ver_tmp:
            logger.info(f'匹配到图片: alt={alt!r}')
            break

    if not ver_tmp:
        logger.warning(f'未在页面找到{server_name}{sup_type}卡节奏榜图片')
        return img_dict, False

    # group: 1=前缀 2=卡类型 3=中间 4=主版本 5=次版本 6=补丁版本
    ver = ver_tmp.group(4) + '.' + ver_tmp.group(5) + '.' + ver_tmp.group(6)
    ver_int = int(ver.replace('.', ''))
    ver_old = img_dict[sup_type].get('version', 0)
    ver_old_int = int(str(ver_old).replace('.', '')) if ver_old else 0

    is_update = False
    if ver_int != ver_old_int:
        logger.info(f'{server_name}{sup_type}卡节奏榜有更新({ver_old} -> {ver})，正在替换旧版文件')
        is_update = True
        img_dict[sup_type]['version'] = ver
        img_dict[sup_type]['img_data'] = {}
        await del_img(sup_type, server_name)
        img_dict = await get_image(img_dict, sup_type, img_soup_list, server_name)
        if not img_dict[sup_type]['img_data']:
            img_dict[sup_type]['version'] = ver_old
    return img_dict, is_update


# 获取详细图片数据（只抓当前卡榜页上的节奏榜主图，排除马娘头像等小图）
async def get_image(img_dict, sup_type, img_soup_list, server_name):
    pattern = re.compile(IMG_PATTERN_TMPL.format(sup_type=sup_type))

    for img_soup in img_soup_list:
        alt = img_soup.get('alt', '')
        m = re.match(pattern, alt)
        if m:
            file_name = alt  # alt本身就是文件名，如"温泉乡智2.22.5榜.png"
            src = img_soup.get('src', '')
            logger.info(f'下载图片: {file_name}')
            img_dict[sup_type]['img_data'][file_name] = src
            await download_img(file_name, src)
    return img_dict


# 下载图片
async def download_img(file_name, url):
    img_path = os.path.join(R.img('umamusume').path, 'uma_support_chart/')
    current_dir = os.path.join(img_path, file_name)
    response = httpx.get(url, timeout=10)
    with open(current_dir, 'wb') as f:
        f.write(response.read())
    # 不改变分辨率，仅90%质量压缩
    im = Image.open(current_dir)
    im.save(current_dir, quality=90)
    logger.info(f'最新版节奏榜图片：[{file_name}]下载成功')


# 删除旧版图片（按服务器+卡类型删除）
async def del_img(sup_type, server_name):
    img_path = os.path.join(R.img('umamusume').path, 'uma_support_chart/')
    pattern = re.compile(IMG_PATTERN_TMPL.format(sup_type=sup_type))
    for file in os.listdir(img_path):
        if re.match(pattern, file):
            current_dir = os.path.join(img_path, file)
            os.remove(current_dir)
            logger.info(f'旧版{sup_type}卡节奏榜图片[{file}]删除成功')


# 合成图片
async def fix_img(img_dict, sup_type, server_name):
    data_dict = {}
    for img_name in list(img_dict[sup_type]['img_data'].keys()):
        current_dir = os.path.join(R.img('umamusume').path, f'uma_support_chart/{img_name}')
        img = Image.open(current_dir)
        data_dict[img_name] = {'width': img.width, 'height': img.height, 'img': img}

    all_height = sum(v['height'] for v in data_dict.values())
    all_width = list(data_dict.values())[0]['width']

    end_img = Image.new('RGB', (all_width, all_height))
    y_offset = 0
    for img_name in list(img_dict[sup_type]['img_data'].keys()):
        img_tmp = data_dict[img_name]['img']
        end_img.paste(img_tmp, (0, y_offset))
        y_offset += data_dict[img_name]['height']

    logger.info(f'{server_name}{sup_type}卡节奏榜图片合成成功，即将准备发送')
    return end_img


# 删除错误的信息配置
async def del_err_info(sup_type, server_name):
    current_dir, _ = await get_config(server_name)
    with open(current_dir, 'r', encoding='UTF-8') as f:
        img_dict = json.load(f)
    img_dict.pop(sup_type)
    with open(current_dir, 'w', encoding='UTF-8') as f:
        json.dump(img_dict, f, indent=4, ensure_ascii=False)


# 返回消息
async def generate_img(sup_type, server_name):
    sup_type = '毅力' if server_name == '简中服' and sup_type == '根' else sup_type
    try:
        img_dict, is_update = await generate_url(sup_type, server_name)
    except httpx.ConnectTimeout:
        return '请求超时，请重试'
    except Exception as e:
        logger.error(f'{server_name}{sup_type}卡节奏榜获取失败: {e}')
        return f'{server_name}{sup_type}卡节奏榜获取失败，请检查节奏榜页面是否有图片或联系维护组反馈BUG'

    _, code = await get_config(server_name)
    code = '' if code == 'jp' else f'_{code}'
    end_path = os.path.join(R.img('umamusume').path, f'uma_support_chart/end_img{code}/')
    if not os.path.exists(end_path):
        os.mkdir(end_path)
    end_img_path = os.path.join(end_path, f'end_{sup_type}{code}.png')

    # 没有更新，直接发送本地图片
    if not is_update:
        logger.info(f'{server_name}{sup_type}卡节奏榜没有更新，将发送本地图片文件')
        if not os.path.exists(end_img_path):
            logger.info(f'本地{server_name}{sup_type}卡节奏榜图片文件丢失，正在重新合成')
            try:
                end_img = await fix_img(img_dict, sup_type, server_name)
            except (UnboundLocalError, FileNotFoundError) as e:
                await del_err_info(sup_type, server_name)
                msg_ = f'遇到特殊问题{e}，因此已清除缓存信息，请尝试重新使用命令"{server_name}{sup_type}卡节奏榜"'
                logger.info(msg_)
                return msg_
            end_img.save(end_img_path, 'PNG')
        return await get_img_cq(end_img_path)

    # 检测到更新但图片未上传
    if not img_dict[sup_type]['img_data']:
        logger.info(f'{server_name}{sup_type}卡节奏榜有更新，但未获取到图片，将发送旧版图片')
        if os.path.exists(end_img_path):
            return await get_img_cq(end_img_path)
        return f'{server_name}{sup_type}卡节奏榜图片暂未上传，请稍后再试'

    # 有更新，合成并发送
    end_img = await fix_img(img_dict, sup_type, server_name)
    end_img.save(end_img_path, 'PNG')
    return await get_img_cq(end_img_path)


async def get_config(server_name):
    if server_name == '日服':
        current_dir = os.path.join(os.path.dirname(__file__), 'sup_config.json')
        code = 'jp'
    elif server_name == '繁中服':
        current_dir = os.path.join(os.path.dirname(__file__), 'sup_config_tw.json')
        code = 'tw'
    elif server_name == '简中服':
        current_dir = os.path.join(os.path.dirname(__file__), 'sup_config_bili.json')
        code = 'bili'
    else:
        current_dir = ''
        code = ''
    return current_dir, code
