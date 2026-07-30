import asyncio
import json
import logging
import time
from typing import Any, Awaitable, Callable, Dict, List, Optional, Set, Tuple

import aiohttp

SendFunc = Callable[[Dict[str, Any]], Awaitable[None]]

# REST 短超时；WebSocket 长连接不能带 total/sock_read 上限，否则约 30s 会被掐断
_REST_TIMEOUT = aiohttp.ClientTimeout(total=30, sock_connect=10)
_WS_TIMEOUT = aiohttp.ClientTimeout(total=None, sock_connect=10, sock_read=None)
_WS_FAIL_RELOGIN = 5  # 连续失败这么多次后抛出，让外层 daemon 重新 login

_ITEM_FIELDS = ",".join(
    [
        "SeriesId",
        "SeriesName",
        "SeasonId",
        "ParentId",
        "ParentIndexNumber",
        "IndexNumber",
        "ProductionYear",
        "RunTimeTicks",
        "Overview",
        "Path",
        "Album",
        "AlbumArtist",
        "Artists",
        "MediaType",
    ]
)


def _to_hhmmss(ticks) -> str:
    try:
        s = int(ticks // 10_000_000)
    except Exception:
        return "00:00:00"
    h = s // 3600
    m = (s % 3600) // 60
    sec = s % 60
    return f"{h:02d}:{m:02d}:{sec:02d}"


class RealtimeItemAdded:
    def __init__(self, cfg: Dict, send_func: SendFunc, logger: Optional[logging.Logger] = None):
        self.cfg = cfg
        self.send_func = send_func
        self.logger = logger or logging.getLogger("jellyfin-notify")
        self.server = self.cfg["server_url"].rstrip("/")
        self.napcat_jellyfin = (self.cfg.get("napcat_jellyfin_url") or "").rstrip("/") or None

        fcfg = self.cfg.get("filters", {}) or {}
        wl = fcfg.get("whitelist", {}) or {}
        bl = fcfg.get("blacklist", {}) or {}
        self.whitelist_enabled = bool(wl.get("enabled", False))
        self.whitelist = [str(x).lower() for x in (wl.get("keywords") or []) if x]
        self.blacklist_enabled = bool(bl.get("enabled", False))
        self.blacklist = [str(x).lower() for x in (bl.get("keywords") or []) if x]

        self.library_policies = self._normalize_policies(self.cfg.get("library_policies", []))
        self.library_policies_norm: Dict[str, Dict] = {}
        for k, v in self.library_policies.items():
            if isinstance(k, str):
                self.library_policies_norm[self._norm_name(k)] = v

        self.client = "Yukari Notify Bot"
        self.device = "Yukari Notify Bot"
        self.version = "1.0.0"
        self.token: Optional[str] = None
        self._http: Optional[aiohttp.ClientSession] = None

        self._library_cache: Dict[str, Tuple[str, str]] = {}
        self._ancestors_cache: Dict[str, List[Dict]] = {}

        dedupe_cfg = self.cfg.get("dedupe") or {}
        try:
            self.season_summary_delay = int(
                dedupe_cfg.get("season_summary_delay_seconds")
                or dedupe_cfg.get("season_summary_ttl_seconds", 300)
            )
        except Exception:
            self.season_summary_delay = 300
        try:
            self.movie_delay_seconds = int(dedupe_cfg.get("movie_delay_seconds", 300))
        except Exception:
            self.movie_delay_seconds = 300
        self.season_summary_delay = max(1, self.season_summary_delay)
        self.movie_delay_seconds = max(1, self.movie_delay_seconds)
        self.per_episode_delay = max(1, int(dedupe_cfg.get("per_episode_delay_seconds", 30)))
        self.merge_threshold = max(2, int(dedupe_cfg.get("merge_threshold", 4)))

        self._season_batches: Dict[str, Dict[str, Any]] = {}
        self._season_lock = asyncio.Lock()
        self._movie_queue: Dict[str, Dict[str, Any]] = {}
        self._movie_lock = asyncio.Lock()
        self._flush_task: Optional[asyncio.Task] = None
        self._warned_libs: Set[str] = set()

    # ---- config helpers ----

    def _normalize_policies(self, arr) -> Dict[str, Dict]:
        policies: Dict[str, Dict] = {}
        for x in arr or []:
            if "libraries" in x and x["libraries"]:
                keys = [str(k).strip() for k in x["libraries"]]
            elif "library" in x and x["library"]:
                keys = [str(x["library"]).strip()]
            else:
                continue
            policy = {"mode": x.get("mode", "per_episode")}
            for key in keys:
                if key:
                    policies[key] = policy
        return policies

    def _norm_name(self, s: Optional[str]) -> str:
        if not s:
            return ""
        table = str.maketrans({
            "（": "(", "）": ")",
            "【": "[", "】": "]",
            "｛": "{", "｝": "}",
        })
        return s.translate(table).replace("　", " ").strip().lower()

    def _pick_policy(self, lib_id: str, lib_name: str) -> Optional[Dict]:
        p = self.library_policies.get(lib_id) or self.library_policies.get(lib_name)
        if p:
            return p
        norm = self._norm_name(lib_name)
        p = self.library_policies_norm.get(norm)
        if p:
            return p
        if norm:
            for k, v in self.library_policies_norm.items():
                if k and (k in norm or norm in k):
                    return v
        return None

    def _auth_headers(self) -> Dict[str, str]:
        headers = {
            "X-Emby-Authorization": (
                f'MediaBrowser Client="{self.client}", Device="{self.device}", '
                f'DeviceId="{self.device}", Version="{self.version}"'
            )
        }
        if self.token:
            headers["X-Emby-Token"] = self.token
        return headers

    # ---- lifecycle ----

    async def _ensure_http(self):
        if self._http is None or self._http.closed:
            self._http = aiohttp.ClientSession(timeout=_REST_TIMEOUT, headers=self._auth_headers())

    async def close(self):
        if self._flush_task and not self._flush_task.done():
            self._flush_task.cancel()
            try:
                await self._flush_task
            except (asyncio.CancelledError, Exception):
                pass
        self._flush_task = None
        if self._http and not self._http.closed:
            await self._http.close()
        self._http = None

    async def login(self):
        await self._ensure_http()
        assert self._http is not None
        # re-create session after token so default headers carry X-Emby-Token
        async with self._http.post(
            f"{self.server}/Users/AuthenticateByName",
            headers=self._auth_headers(),
            json={"Username": self.cfg["username"], "Pw": self.cfg["password"]},
        ) as resp:
            resp.raise_for_status()
            data = await resp.json(content_type=None)
        self.token = data["AccessToken"]
        await self._http.close()
        self._http = aiohttp.ClientSession(
            timeout=_REST_TIMEOUT,
            headers=self._auth_headers(),
        )
        self.logger.info("已连接到 Jellyfin: %s", self.server)

    # ---- jellyfin REST ----

    async def get_items_by_ids(self, ids: List[str]) -> List[Dict]:
        if not ids:
            return []
        await self._ensure_http()
        assert self._http is not None
        try:
            async with self._http.get(
                f"{self.server}/Items",
                params={"ids": ",".join(ids), "enableImages": "true", "fields": _ITEM_FIELDS},
            ) as resp:
                resp.raise_for_status()
                data = await resp.json(content_type=None)
            return data.get("Items") or data.get("items") or []
        except Exception as e:
            self.logger.error("获取 Items 失败: %s", e)
            return []

    async def get_ancestors(self, item_key: str) -> List[Dict]:
        if not item_key:
            return []
        if item_key in self._ancestors_cache:
            return self._ancestors_cache[item_key]
        await self._ensure_http()
        assert self._http is not None
        try:
            async with self._http.get(f"{self.server}/Items/{item_key}/Ancestors") as resp:
                resp.raise_for_status()
                arr = await resp.json(content_type=None) or []
            self._ancestors_cache[item_key] = arr
            return arr
        except Exception:
            return []

    async def get_library_of_item(self, item_id: str) -> Tuple[str, str]:
        if item_id in self._library_cache:
            return self._library_cache[item_id]
        ancestors = await self.get_ancestors(item_id)
        lib = ("", "")
        for a in ancestors:
            if a.get("Type") == "CollectionFolder":
                lib = (a.get("Id") or "", a.get("Name") or "")
                break
        if lib[0]:
            self._library_cache[item_id] = lib
        return lib

    async def get_library_for(self, item: Dict) -> Tuple[str, str]:
        key = item.get("SeriesId") or item.get("ParentId") or item.get("Id")
        if not key:
            return "", ""
        if key in self._library_cache:
            return self._library_cache[key]
        lib = await self.get_library_of_item(key)
        item_id = item.get("Id")
        if item_id and item_id not in self._library_cache and lib[0]:
            self._library_cache[item_id] = lib
        return lib

    async def _pick_policy_for_item(self, item: Dict) -> Optional[Dict]:
        key = item.get("SeriesId") or item.get("ParentId") or item.get("Id")
        for a in await self.get_ancestors(key):
            pol = self._pick_policy(a.get("Id") or "", a.get("Name") or "")
            if pol:
                return pol
        lib_id, lib_name = await self.get_library_for(item)
        return self._pick_policy(lib_id, lib_name)

    # ---- filters ----

    def _hay_from_item(self, item: Dict) -> str:
        parts = [
            item.get("Name"),
            item.get("SeriesName"),
            item.get("Overview"),
            item.get("Path"),
            item.get("ProductionYear"),
            item.get("Type"),
            item.get("Album"),
            item.get("AlbumArtist"),
            ", ".join(item.get("Artists", [])) if item.get("Artists") else None,
        ]
        return " ".join(str(x) for x in parts if x).lower()

    def _hay_for_series(self, series_name: str, example_item: Dict) -> str:
        return " ".join(str(x) for x in (series_name, example_item.get("Path")) if x).lower()

    def _pass_filters(self, hay_lower: str) -> bool:
        if self.blacklist_enabled and self.blacklist and any(k in hay_lower for k in self.blacklist):
            return False
        if self.whitelist_enabled:
            if not self.whitelist or not any(k in hay_lower for k in self.whitelist):
                return False
        return True

    # ---- queues / flusher ----

    def _ensure_flusher(self):
        if self._flush_task is None or self._flush_task.done():
            self._flush_task = asyncio.create_task(self._batch_flusher())

    async def _queue_season_summary(
        self,
        series_id: str,
        season_no: int,
        series_name: str,
        episodes: List[Dict],
        delay: Optional[int] = None,
    ):
        if not episodes:
            return
        self._ensure_flusher()
        wait = max(1, int(delay if delay is not None else self.season_summary_delay))
        series_key = series_id or ""
        s_no = int(season_no or 0)
        async with self._season_lock:
            entry = self._season_batches.get(series_key)
            if entry is None:
                entry = {
                    "series_id": series_id,
                    "series_name": series_name,
                    "seasons": {},
                    "due_time": time.time() + wait,
                }
                self._season_batches[series_key] = entry
            else:
                if series_name and not entry.get("series_name"):
                    entry["series_name"] = series_name
                entry["due_time"] = time.time() + wait
            season_bucket = entry["seasons"].setdefault(s_no, {"episodes": {}})
            episodes_dict: Dict[str, Dict] = season_bucket["episodes"]
            for ep in episodes:
                ep_id = str(ep.get("Id") or ep.get("EpisodeId") or len(episodes_dict))
                episodes_dict[ep_id] = ep

    async def _queue_movie(self, item: Dict):
        self._ensure_flusher()
        key = str(item.get("Id") or "")
        if not key:
            return
        wait = max(1, int(self.movie_delay_seconds))
        now = time.time()
        async with self._movie_lock:
            entry = self._movie_queue.get(key)
            if entry is None:
                self._movie_queue[key] = {"item": item, "due_time": now + wait}
            else:
                entry["item"] = item
                entry["due_time"] = now + wait

    async def _flush_due_batches(self):
        now = time.time()
        ready: List[Dict[str, Any]] = []
        async with self._season_lock:
            due_keys = [
                key
                for key, entry in self._season_batches.items()
                if entry.get("due_time") and entry["due_time"] <= now and entry.get("seasons")
            ]
            for key in due_keys:
                entry = self._season_batches.pop(key, None)
                if entry:
                    ready.append(entry)
        for entry in ready:
            seasons = entry.get("seasons", {})
            all_eps = []
            for s in seasons.values():
                all_eps.extend(s.get("episodes", {}).values())
            if not all_eps:
                continue
            series_name = entry.get("series_name") or (all_eps[0].get("SeriesName") or "")
            series_id = entry.get("series_id") or (all_eps[0].get("SeriesId") or "")
            if len(all_eps) < self.merge_threshold:
                for ep in all_eps:
                    if self._pass_filters(self._hay_from_item(ep)):
                        await self.forward_async(lambda: self.build_payload(ep))
            else:
                season_info: Dict[int, Dict] = {}
                for s_no, s_data in seasons.items():
                    eps = s_data.get("episodes", {})
                    if not eps:
                        continue
                    ep_nums = [int(ep.get("IndexNumber") or 0) for ep in eps.values()]
                    season_info[s_no] = {
                        "count": len(eps),
                        "min_ep": min(ep_nums),
                        "max_ep": max(ep_nums),
                    }
                await self.forward_async(lambda: self.build_season_payload(series_name, season_info, series_id))

    async def _flush_due_movies(self):
        now = time.time()
        ready: List[Dict[str, Any]] = []
        async with self._movie_lock:
            due_keys = [k for k, v in self._movie_queue.items() if v.get("due_time", 0) <= now]
            for k in due_keys:
                entry = self._movie_queue.pop(k, None)
                if entry:
                    ready.append(entry)
        for entry in ready:
            item = entry.get("item") or {}
            if self._pass_filters(self._hay_from_item(item)):
                await self.forward_async(lambda: self.build_payload(item))

    async def _batch_flusher(self):
        try:
            while True:
                await asyncio.sleep(5)
                try:
                    await self._flush_due_batches()
                    await self._flush_due_movies()
                except asyncio.CancelledError:
                    raise
                except Exception:
                    self.logger.exception("flush 批次异常，已跳过本轮")
        except asyncio.CancelledError:
            pass

    # ---- message build / send ----

    def _image_url(self, item_id: str) -> str:
        base = self.napcat_jellyfin or self.server
        return f"{base}/Items/{item_id}/Images/Primary"

    def build_payload(self, item: Dict) -> Dict:
        t = (item.get("Type") or "").strip()
        name = item.get("Name") or ""
        year = item.get("ProductionYear") or ""
        series_name = item.get("SeriesName") or ""
        season_no = item.get("ParentIndexNumber")
        ep_no = item.get("IndexNumber")
        series_id = item.get("SeriesId") or item.get("Id")
        runtime_ticks = item.get("RunTimeTicks") or 0
        dur = (
            _to_hhmmss(runtime_ticks)
            if t in ("Episode", "Movie", "MusicVideo") and runtime_ticks
            else None
        )

        if t == "Episode":
            msg = f"{series_name} 更新啦！\nS{int(season_no or 0):02d}E{int(ep_no or 0):02d}\n{name}"
            subject = f"{series_name} S{int(season_no or 0):02d}E{int(ep_no or 0):02d}"
            image_item_id = series_id
        elif t == "Movie":
            msg = f"{name} ({year}) 更新啦！"
            subject = f"{name} ({year})" if year else name
            image_item_id = item.get("Id")
        elif t == "MusicAlbum":
            album = name or (item.get("Album") or "")
            artist = item.get("AlbumArtist") or (
                ", ".join(item.get("Artists", [])) if item.get("Artists") else ""
            )
            msg = f"新专辑！：{album} - {artist} ({year})" if artist else f"新专辑！：{album} ({year})"
            subject = f"{album} - {artist}" if artist else album
            image_item_id = item.get("Id")
        else:
            msg = f"{name} 更新啦！\n媒体：{name} ({year})"
            subject = name
            image_item_id = item.get("Id")

        if dur:
            msg += f"\n时长：{dur}"
        return {
            "message": f"{msg}\n[CQ:image,file={self._image_url(image_item_id)}]",
            "subject": subject,
        }

    def build_season_payload(self, series_name: str, season_info: Dict[int, Dict], series_id: str) -> Dict:
        def season_label(n: int) -> str:
            return "特别篇" if n == 0 else f"第{n:02d}季"

        def ep_range_label(info: Dict) -> str:
            cnt, mn, mx = info["count"], info["min_ep"], info["max_ep"]
            if cnt == 1:
                return f"EP{mn:02d}，共 1 集"
            return f"EP{mn:02d}-EP{mx:02d}，共 {cnt} 集"

        sorted_seasons = sorted(season_info.items())
        if len(sorted_seasons) == 1:
            s_no, info = sorted_seasons[0]
            detail = f"{season_label(s_no)}（{ep_range_label(info)}）"
        else:
            detail = "\n".join(
                f"{season_label(s_no)}（{ep_range_label(info)}）" for s_no, info in sorted_seasons
            )
        subject_parts = [f"S{s_no:02d}" if s_no else "SP" for s_no, _ in sorted_seasons]
        title = f"{series_name} 更新啦！\n{detail}"
        return {
            "message": f"{title}\n[CQ:image,file={self._image_url(series_id)}]",
            "subject": f"{series_name} {'+'.join(subject_parts)} 合集",
        }

    async def forward_async(self, payload):
        if callable(payload):
            try:
                payload = payload()
            except Exception:
                self.logger.exception("构造推送消息失败，已跳过")
                return
        subj = payload.get("subject") or "通知"
        try:
            await self.send_func(payload)
            self.logger.info("【%s】推送完成", subj)
        except Exception as e:
            self.logger.error("【%s】推送异常: %s", subj, e)

    # ---- item dispatch (single path) ----

    async def _dispatch_item(self, it: Dict, default_policy: Dict):
        pol = await self._pick_policy_for_item(it) or default_policy
        mode = pol.get("mode", "per_episode")
        t = it.get("Type") or ""

        if mode == "mute":
            return
        if mode == "album_only":
            if t == "MusicAlbum" and self._pass_filters(self._hay_from_item(it)):
                await self.forward_async(lambda: self.build_payload(it))
            return
        if mode == "season_summary":
            if t == "Episode":
                sid = it.get("SeriesId") or ""
                s_no = int(it.get("ParentIndexNumber") or 0)
                s_name = it.get("SeriesName") or ""
                if self._pass_filters(self._hay_for_series(s_name, it)):
                    await self._queue_season_summary(sid, s_no, s_name, [it])
            elif t == "Movie" and self._pass_filters(self._hay_from_item(it)):
                await self._queue_movie(it)
            return
        # per_episode (default)
        if t == "Episode":
            sid = it.get("SeriesId") or ""
            s_no = int(it.get("ParentIndexNumber") or 0)
            s_name = it.get("SeriesName") or ""
            if self._pass_filters(self._hay_for_series(s_name, it)):
                await self._queue_season_summary(
                    sid, s_no, s_name, [it], delay=self.per_episode_delay
                )
        elif t == "Movie" and self._pass_filters(self._hay_from_item(it)):
            await self._queue_movie(it)

    async def _handle_items(self, items: List[Dict]):
        by_lib: Dict[str, Dict] = {}
        for it in items:
            if not it.get("Id"):
                continue
            lib_id, lib_name = await self.get_library_for(it)
            key = f"{lib_id or 'NA'}::{lib_name or 'NA'}"
            pack = by_lib.setdefault(key, {"lib_id": lib_id, "lib_name": lib_name, "items": []})
            pack["items"].append(it)

        for pack in by_lib.values():
            lib_id, lib_name, its = pack["lib_id"], pack["lib_name"], pack["items"]
            policy = self._pick_policy(lib_id, lib_name) or {}
            if not policy:
                key = f"{lib_id or 'NA'}::{lib_name or 'NA'}"
                if key not in self._warned_libs:
                    self.logger.info(
                        "未匹配库策略，按 per_episode：Id=%s Name=%s",
                        lib_id or "NA",
                        lib_name or "NA",
                    )
                    self._warned_libs.add(key)
            if policy.get("mode") == "mute":
                continue
            for it in its:
                await self._dispatch_item(it, policy)

    # ---- websocket loop ----

    def _ws_url(self) -> str:
        ws_url = self.server
        if ws_url.startswith("https://"):
            ws_url = "wss://" + ws_url[len("https://"):]
        elif ws_url.startswith("http://"):
            ws_url = "ws://" + ws_url[len("http://"):]
        return ws_url.rstrip("/") + f"/socket?api_key={self.token}"

    async def run_ws(self):
        await self._ensure_http()
        assert self._http is not None
        backoff = 1
        fails = 0
        while True:
            try:
                async with self._http.ws_connect(
                    self._ws_url(), heartbeat=30, timeout=_WS_TIMEOUT
                ) as ws:
                    backoff = 1
                    fails = 0
                    self.logger.info("Jellyfin WebSocket 已连接")
                    await ws.send_str(json.dumps({"MessageType": "KeepAlive"}))
                    async for msg in ws:
                        if msg.type == aiohttp.WSMsgType.TEXT:
                            try:
                                data = json.loads(msg.data)
                            except Exception:
                                continue
                            if (data.get("MessageType") or data.get("message_type")) != "LibraryChanged":
                                continue
                            body = data.get("Data") or data.get("data") or {}
                            items_added = body.get("ItemsAdded") or body.get("items_added") or []
                            ids_added = list(dict.fromkeys([i for i in items_added if i]))
                            if not ids_added:
                                continue
                            items = await self.get_items_by_ids(ids_added)
                            if items:
                                await self._handle_items(items)
                        elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                            break
            except asyncio.CancelledError:
                raise
            except Exception as e:
                fails += 1
                self.logger.warning(
                    "WebSocket 断开，%ss 后重连 (%s/%s): %s",
                    backoff,
                    fails,
                    _WS_FAIL_RELOGIN,
                    e,
                )
                if fails >= _WS_FAIL_RELOGIN:
                    raise RuntimeError(f"WebSocket 连续失败 {fails} 次，重新登录") from e
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30)
