import re
import urllib.parse

from curl_cffi.requests import AsyncSession
from lxml import etree
from io import BytesIO
from PIL import Image
from random import randint
from traceback import format_exc

from hoshino import log, aiorequests
from hoshino.typing import MessageSegment
from hoshino.util import pic2b64, load_yaml_config

_cfg = load_yaml_config('picfinder_take')
SAUCENAO_RESULT_NUM = int(_cfg.get('saucenao_result_num', 2))
ASCII_RESULT_NUM = int(_cfg.get('ascii_result_num', 2))
THUMB_ON = bool(_cfg.get('thumb_on', True))
proxies = _cfg.get('proxies') or {}
_hc = _cfg.get('host_custom') or {}
HOST_CUSTOM = {
    'SAUCENAO': (_hc.get('saucenao') or _hc.get('SAUCENAO') or ''),
    'ASCII': (_hc.get('ascii') or _hc.get('ASCII') or ''),
}

logger = log.new_logger('image')

async def get_pic(address):
    return await (await aiorequests.get(address,timeout=20, proxies=proxies)).content

def randcolor():
    return (randint(0, 255), randint(0, 255), randint(0, 255))

def ats_pic(img):
    if(img.mode!='RGB'):
        img = img.convert("RGB")
    width = img.size[0]-1#长度
    height = img.size[1]-1#宽度
    img.putpixel((0,0), randcolor())
    img.putpixel((0,height), randcolor())
    img.putpixel((width,0), randcolor())
    img.putpixel((width,height), randcolor())
    return img

def sauces_info(sauce):
    service_name=''
    info = ""
    try:
        if sauce['header']['index_id'] == 0:
            service_name='H-Magazines'
            title = sauce['data']['title']
            part = sauce['data']['part']
            date = sauce['data']['date']
            info = f"{title}-{part}/{date}"

        #index 1 "h-anime" disabled

        elif sauce['header']['index_id'] == 2:
            service_name='H-Game CG'
            #getchu_id=sauce['data']['getchu_id']
            company = sauce['data']['company']
            title = sauce['data']['title']
            info = f"[{company}] {title}"

        #index 3 "ddb-objects" disabled

        #index 4 "ddb-samples" disabled

        elif sauce['header']['index_id'] == 5 or sauce['header']['index_id'] == 6:
            service_name='pixiv'
            #member_id = sauce['data']['member_id']
            #illust_id=sauce['data']['pixiv_id']
            author_name = sauce['data']['member_name']
            title = sauce['data']['title']
            info = f"「{title}」/「{author_name}」"

        #index 6 "pixiv historical" with 5

        #index 7 "anime" disabled

        elif sauce['header']['index_id'] == 8:
            service_name='nico nico seiga'
            #member_id = sauce['data']['member_id']
            #illust_id=sauce['data']['seiga_id']
            author_name = sauce['data']['member_name']
            title = sauce['data']['title']
            info = f"「{title}」/「{author_name}」"
            
        elif sauce['header']['index_id'] == 9:
            service_name='Danbooru'
            #illust_id=sauce['data']['danbooru_id']
            #source = sauce['data']['source']
            creator = sauce['data']['creator']
            material = sauce['data']['material']
            info = f"[{creator}]({material})"
            
        elif sauce['header']['index_id'] == 10:
            service_name='drawr Images'
            #member_id = sauce['data']['member_id']
            #illust_id=sauce['data']['drawr_id']
            author_name = sauce['data']['member_name']
            title = sauce['data']['title']
            info = f"「{title}」/「{author_name}」"
            
        elif sauce['header']['index_id'] == 11:
            service_name='Nijie Images'
            #member_id = sauce['data']['member_id']
            #illust_id=sauce['data']['nijie_id']
            author_name = sauce['data']['member_name']
            title = sauce['data']['title']
            info = f"「{title}」/「{author_name}」"
            
        elif sauce['header']['index_id'] == 12:
            service_name='Yande.re'
            creator = sauce['data']['creator']
            #yandere_id=sauce['data']['yandere_id']
            material = sauce['data']['material']
            #source = sauce['data']['source']
            info = f"[{creator}]({material})"

        #index 13 "animeop" disabled

        #index 14 "IMDb" disabled

        #index 15 "Shutterstock" disabled

        elif sauce['header']['index_id'] == 16:
            service_name='FAKKU'
            creator = sauce['data']['creator']
            source = sauce['data']['source']
            info = f"[{creator}]({source})"

        #index 17 reserved
        
        elif sauce['header']['index_id'] == 18 or sauce['header']['index_id'] == 38:
            service_name='H-Misc (ehentai)'
            eng_name = sauce['data']['eng_name']
            jp_name = sauce['data']['jp_name']
            info = f"{jp_name}" if jp_name else f"{eng_name}"
        
        elif sauce['header']['index_id'] == 19:
            service_name='2D-Market'
            creator = sauce['data']['creator']
            source = sauce['data']['source']
            info = f"[{creator}]({source})"

        elif sauce['header']['index_id'] == 20:
            service_name='MediBang'
            #member_id = sauce['data']['member_id']
            member_name = sauce['data']['member_name']
            title = sauce['data']['title']
            info = f"「{title}」/「{member_name}」"
        
        elif sauce['header']['index_id'] == 21:
            service_name='Anime'
            title = sauce['data']['source']
            #anidb_id = sauce['data']['anidb_aid']
            year = sauce['data']['year']
            part = sauce['data']['part']
            est_time = sauce['data']['est_time']
            time = est_time.split('/')[0]
            info = f"《{title}》/{year}\n第{part}集，{time}"
            
        elif sauce['header']['index_id'] == 22:
            service_name='H-Anime'
            title = sauce['data']['source']
            #anidb_id = sauce['data']['anidb_aid']
            year = sauce['data']['year']
            part = sauce['data']['part']
            est_time = sauce['data']['est_time']
            time = est_time.split('/')[0]
            info = f"《{title}》/{year}\n第{part}集，{time}"
            
        elif sauce['header']['index_id'] == 23:
            service_name='IMDb-Movies'
            title = sauce['data']['source']
            #imdb_id = sauce['data']['imdb_id']
            year = sauce['data']['year']
            #part = sauce['data']['part']
            est_time = sauce['data']['est_time']
            time = est_time.split('/')[0]
            info = f"《{title}》/{year}，{time}"
        
        elif sauce['header']['index_id'] == 24:
            service_name='IMDb-Shows'
            title = sauce['data']['source']
            #imdb_id = sauce['data']['imdb_id']
            year = sauce['data']['year']
            part = sauce['data']['part']
            est_time = sauce['data']['est_time']
            time = est_time.split('/')[0]
            info = f"《{title}》/{year}\n第{part}集，{time}"
            
        elif sauce['header']['index_id'] == 25:
            service_name='Gelbooru'
            #illust_id=sauce['data']['gelbooru_id']
            creator = sauce['data']['creator']
            material = sauce['data']['material']
            info = f"[{creator}]({material})"
        
        elif sauce['header']['index_id'] == 26:
            service_name='Konachan'
            creator = sauce['data']['creator']
            material = sauce['data']['material']
            #illust_id=sauce['data']['konachan_id']
            info = f"[{creator}]({material})"
        
        elif sauce['header']['index_id'] == 27:
            service_name='Sankaku Channel'
            #illust_id=sauce['data']['sankaku_id']
            creator = sauce['data']['creator']
            material = sauce['data']['material']
            info = f"[{creator}]({material})"
            
        elif sauce['header']['index_id'] == 28:
            service_name='Anime-Pictures.net'
            #illust_id=sauce['data']['anime-pictures_id']
            creator = sauce['data']['creator']
            material = sauce['data']['material']
            info = f"[{creator}]({material})"
            
        elif sauce['header']['index_id'] == 29:
            service_name='e621.net'
            creator = sauce['data']['creator']
            #e621_id=sauce['data']['e621_id']
            material=sauce['data']['material']
            info = f"[{creator}]({material})"

        elif sauce['header']['index_id'] == 30:
            service_name='Idol Complex'
            creator = sauce['data']['creator']
            #idol_id=sauce['data']['idol_id']
            material=sauce['data']['material']
            info = f"[{creator}]({material})"
        
        elif sauce['header']['index_id'] == 31:
            service_name='bcy.net Illust'
            #member_id = sauce['data']['member_id']
            #illust_id=sauce['data']['bcy_id']
            author_name = sauce['data']['member_name']
            title = sauce['data']['title']
            info = f"「{title}」/「{author_name}」"
            
        elif sauce['header']['index_id'] == 32:
            service_name='bcy.net Cosplay'
            #member_id = sauce['data']['member_id']
            #illust_id=sauce['data']['bcy_id']
            author_name = sauce['data']['member_name']
            title = sauce['data']['title']
            info = f"「{title}」/「{author_name}」"
            
        elif sauce['header']['index_id'] == 33:
            service_name='PortalGraphics.net'
            #illust_id=sauce['data']['pg_id']
            #member_id = sauce['data']['member_id']
            member_name = sauce['data']['member_name']
            title = sauce['data']['title']
            info = f"「{title}」/「{member_name}」"
        
        elif sauce['header']['index_id'] == 34:
            service_name='deviantArt'
            #illust_id=sauce['data']['da_id']
            author_name = sauce['data']['author_name']
            title = sauce['data']['title']
            info = f"「{title}」/「{author_name}」"
            
        elif sauce['header']['index_id'] == 35:
            service_name='Pawoo.net'
            illust_id=sauce['data']['pawoo_id']
            #member_id = sauce['data']['pawoo_user_acct']
            author_name = sauce['data']['pawoo_user_display_name']
            info = f"「{illust_id}」/「{author_name}」"
            
        elif sauce['header']['index_id'] == 36:
            service_name='Madokami (Manga)'
            source = sauce['data']['source']
            part = sauce['data']['part']
            
            info =part if source in part else f"{source}-{part}"

        elif sauce['header']['index_id'] == 37 or sauce['header']['index_id'] == 371:
            service_name='MangaDex'
            #illust_id=sauce['data']['md_id']
            artist = sauce['data']['artist']
            author = sauce['data']['author']
            source = sauce['data']['source']
            part = sauce['data']['part']
            info_a =f"[{artist}]" if artist==author else f"[{artist}({author})]"
            info_b =part if source in part else f"{source}-{part}"
            info = info_a+info_b

        #index 38 "H-Misc (ehentai)" with 18
            
        elif sauce['header']['index_id'] == 39:
            service_name='Artstation'
            #member_id = sauce['data']['author_url']
            #illust_id=sauce['data']['as_project']
            author_name = sauce['data']['author_name']
            title = sauce['data']['title']
            info = f"「{title}」/「{author_name}」"
        
        elif sauce['header']['index_id'] == 40:
            service_name='FurAffinity'
            #member_id = sauce['data']['fa_id']
            author_name = sauce['data']['author_name']
            title = sauce['data']['title']
            info = f"「{title}」/「{author_name}」"
            
        elif sauce['header']['index_id'] == 41:
            service_name='Twitter'
            #member_id = sauce['data']['twitter_user_id']
            #illust_id=sauce['data']['tweet_id']
            author_name = sauce['data']['twitter_user_handle']
            time= sauce['data']['created_at']
            info = f"「{time[0:10]}」/「{author_name}」"
            
        elif sauce['header']['index_id'] == 42:
            service_name='Furry Network'
            #illust_id = sauce['data']['fn_id']
            author_name = sauce['data']['author_name']
            title = sauce['data']['title']
            info = f"「{title}」/「{author_name}」"

        elif sauce['header']['index_id'] == 43:
            service_name='Kemono'
            #illust_id = sauce['data']['id']
            service = sauce['data']['service_name']
            author_name = sauce['data']['user_name']
            title = sauce['data']['title']
            info = f"「{title}」/「({service}){author_name}」"

        elif sauce['header']['index_id'] == 44:
            service_name='Skeb'
            #illust_id = sauce['data']['id']
            creator_name = sauce['data']['creator_name']
            creator = sauce['data']['creator']
            info = f"[{creator_name}]({creator})"

        else:
            index = sauce['header']['index_id']
            service_name = f'Index #{index}'
            info ="no info"
            
    except Exception as e:
        index = sauce['header']['index_id']
        service_name = f'Index #{index}'
        info ="no info"
        logger.error(format_exc())

    return service_name, info


class SauceNAO():
    def __init__(self, api_key, output_type=2, testmode=0, dbmask=None, dbmaski=None, db=999, numres=3, shortlimit=20, longlimit=300):
        params = dict()
        params['api_key'] = api_key
        params['output_type'] = output_type
        params['testmode'] = testmode
        params['dbmask'] = dbmask
        params['dbmaski'] = dbmaski
        params['db'] = db
        params['numres'] = numres
        self.params = params
        self.host = HOST_CUSTOM['SAUCENAO'] or 'https://saucenao.com'
        self.header = "————>saucenao<————"


    async def get_sauce(self, url):
        self.params['url'] = url
        logger.debug(f"Now starting get the SauceNAO data:{url}")
        response = await aiorequests.get(f'{self.host}/search.php', params=self.params, timeout=15, proxies=proxies)
        data = await response.json()
        
        return data


    async def get_view(self, sauce) -> list:
        sauces = await self.get_sauce(sauce)
        repass = ""
        simimax = 0
        
        for sauce in sauces['results']:
            try:
                url = sauce['data']['ext_urls'][0].replace("\\","").strip() if 'ext_urls' in sauce['data'] else "no link"
                similarity = sauce['header']['similarity']
                if not similarity.replace(".", "").isdigit():
                    similarity=0
                simimax = float(similarity) if float(similarity)>simimax else simimax
                thumbnail_url = sauce['header']['thumbnail']
                if THUMB_ON:
                    try:
                        thumbnail_image = str(MessageSegment.image(pic2b64(ats_pic(Image.open(BytesIO(await get_pic(thumbnail_url)))))))
                    except Exception as e:
                        logger.error(format_exc())
                        thumbnail_image = "[预览图下载失败]"
                else:
                    thumbnail_image = ""

                service_name, info = sauces_info(sauce)
                
                putline = f"{thumbnail_image}\n[{service_name}][{url}] 相似度:{similarity}%\n{info}"
                if repass:
                    repass = "\n".join([repass, putline])
                else:
                    repass = putline

            except Exception as e:
                logger.error(format_exc())
                pass
        
        return [repass, simimax]


class ascii2d():
    def __init__(self, num=2):
        self.num = num
        self.host = HOST_CUSTOM['ASCII'] or "https://ascii2d.net"
        self.header = "————>ascii2d<————"
        self.session = AsyncSession(impersonate="chrome120", timeout=30)

    async def _get_authenticity_token(self) -> str:
        """从主页获取 authenticity_token"""
        resp = await self.session.get(self.host)
        if resp.status_code != 200:
            logger.warning(f"ascii2d main page failed: {resp.status_code}")
            return ''
        patterns = [
            r'name="authenticity_token"\s+value="([^"]+)"',
            r'value="([^"]+)"\s*name="authenticity_token"',
        ]
        for p in patterns:
            m = re.search(p, resp.text, re.IGNORECASE)
            if m:
                return m.group(1)
        logger.warning("ascii2d: authenticity_token not found")
        return ''

    async def _post_search(self, image_url: str, token: str) -> str:
        """POST 搜索请求，返回结果页 URL"""
        form_data = urllib.parse.urlencode({
            "utf8": "✓",
            "authenticity_token": token,
            "uri": image_url,
            "search": "",
        })
        resp = await self.session.post(
            f"{self.host}/search/uri",
            data=form_data,
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Origin": self.host,
                "Referer": f"{self.host}/",
            },
            allow_redirects=True,
        )
        if resp.status_code == 200:
            return str(resp.url)
        logger.warning(f"ascii2d POST failed: {resp.status_code}")
        return ''

    def _parse_results(self, html) -> list:
        """解析搜索结果页"""
        all_data = html.xpath('//div[@class="row item-box"]')
        info = []
        for item in all_data[1:self.num+1]:
            try:
                if not item.xpath('.//img[@loading="lazy"]/@src'):
                    continue
                thumb_url = item.xpath('.//img[@loading="lazy"]/@src')[0].strip()
                thumb_url = f"{self.host}{thumb_url}"

                if not item.xpath('.//div[@class="detail-box gray-link"]/h6'):
                    ext = item.xpath('.//div[@class="external"]')
                    data2 = ext[0] if ext else item
                    info_url = data2.xpath('.//a/@href')[0].strip() if item.xpath('.//a/@rel') else "no link"
                    tag = "外部登录" if info_url == "no link" else info_url.split('/')[2]
                else:
                    data2 = item.xpath('.//div[@class="detail-box gray-link"]/h6')[0]
                    info_url = data2.xpath(".//a/@href")[0].strip()
                    tag = (data2.xpath("./small/text()") or data2.xpath(".//a/text()"))[0].strip()

                if tag in ("pixiv", "twitter"):
                    title = data2.xpath(".//a//text()")[0]
                    member = data2.xpath(".//a//text()")[1]
                    title = f"「{title}」/「{member}」"
                elif tag == "外部登录":
                    title = data2.text.replace("\n", "") if data2.text else ""
                else:
                    title = data2.text.replace("\n", "")
                    title = f"「{title}」"

                info.append([info_url, tag, thumb_url, title])
            except Exception as e:
                logger.error(format_exc())
                continue
        return info

    async def _format_repass(self, tag: str, data):
        po = "——{}——".format(tag)
        for line in data:
            if THUMB_ON:
                try:
                    img_resp = await self.session.get(line[2], timeout=20)
                    thumbnail_image = str(MessageSegment.image(pic2b64(ats_pic(Image.open(BytesIO(img_resp.content))))))
                except Exception as e:
                    logger.error(format_exc())
                    thumbnail_image = "[预览图下载失败]"
            else:
                thumbnail_image = ""
            putline = f"{thumbnail_image}\n[{line[1]}][{line[0]}]\n{line[3]}"
            po = "\n".join([po, putline])
        return po

    async def get_view(self, image_url: str) -> list:
        putline1 = ''
        putline2 = ''

        try:
            # 1. 获取 authenticity_token
            token = await self._get_authenticity_token()
            if not token:
                logger.error("ascii2d: 获取 token 失败")
                return [putline1, putline2]

            # 2. POST 搜索请求
            result_url = await self._post_search(image_url, token)
            if not result_url:
                logger.error("ascii2d: 搜索请求失败")
                return [putline1, putline2]

            # 3. 解析结果页（色调检索）
            resp = await self.session.get(result_url, headers={
                "Accept": "text/html,application/xhtml+xml",
                "Accept-Language": "ja,en-US;q=0.9,en;q=0.8",
                "Sec-Fetch-Dest": "document",
                "Sec-Fetch-Mode": "navigate",
                "Sec-Fetch-Site": "none",
                "Sec-Fetch-User": "?1",
                "Upgrade-Insecure-Requests": "1",
            })
            if resp.status_code != 200:
                logger.error(f"ascii2d: 获取结果页失败 {resp.status_code}")
                return [putline1, putline2]

            html = etree.HTML(resp.text)
            color = self._parse_results(html)

            # 4. 获取特征检索结果
            bovw = []
            neet_div = html.xpath('//div[@class="detail-link pull-xs-right hidden-sm-down gray-link"]')
            if neet_div:
                a_links = neet_div[0].xpath('./span/a/@href')
                if len(a_links) >= 2:
                    bovw_url = f"{self.host}{a_links[1]}"
                    resp2 = await self.session.get(bovw_url, headers={
                        "Accept": "text/html,application/xhtml+xml",
                        "Accept-Language": "ja,en-US;q=0.9,en;q=0.8",
                        "Referer": result_url,
                        "Sec-Fetch-Dest": "document",
                        "Sec-Fetch-Mode": "navigate",
                        "Sec-Fetch-Site": "same-origin",
                        "Upgrade-Insecure-Requests": "1",
                    })
                    if resp2.status_code == 200:
                        html2 = etree.HTML(resp2.text)
                        bovw = self._parse_results(html2)

            if color:
                putline1 = await self._format_repass("色调检索", color)
            if bovw:
                putline2 = await self._format_repass("特征检索", bovw)

        except Exception as e:
            logger.error(f"ascii2d search failed:\n{format_exc()}")

        return [putline1, putline2]

    async def close(self):
        await self.session.close()


async def get_image_data_sauce(image_url: str, api_key: str):
    if type(image_url) == list:
        image_url = image_url[0]

    logger.info("Loading Image Search Container……")
    NAO = SauceNAO(api_key, numres=SAUCENAO_RESULT_NUM)

    logger.debug("Loading all view……")
    repass = ''
    simimax = 0
    try:
        result = await NAO.get_view(image_url)
        if result:
            header = NAO.header
            simimax = result[1]
            repass = "\n".join([header, result[0]])
    except Exception as e:
        logger.error(format_exc())
        return ["SauceNAO搜索失败……", 0]

    return [repass, simimax]

async def get_image_data_ascii(image_url: str):
    if type(image_url) == list:
        image_url = image_url[0]

    logger.info("Loading Image Search Container……")
    ii2d = ascii2d(ASCII_RESULT_NUM)

    logger.debug("Loading all view……")
    repass1 = ''
    repass2 = ''
    try:
        putline = await ii2d.get_view(image_url)
        if putline:
            header = ii2d.header
            if putline[0]:
                repass1 = "\n".join([header, putline[0]])
            if putline[1]:
                repass2 = "\n".join([header, putline[1]])
    except Exception as e:
        logger.error(format_exc())
        return ["", ""]
    finally:
        await ii2d.close()

    return [repass1, repass2]