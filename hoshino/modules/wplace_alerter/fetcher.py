# -*- coding: utf-8 -*-
import logging
from typing import Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logging.getLogger("urllib3").setLevel(logging.ERROR)


class ImageFetcher:
    def __init__(self, timeout: int = 10, https_proxy: Optional[str] = None, trust_env: bool = False):
        self.timeout = timeout
        self.session = requests.Session()
        self.session.trust_env = bool(trust_env)

        retries = Retry(
            total=3,
            backoff_factor=0.5,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["GET", "HEAD"],
        )
        adapter = HTTPAdapter(max_retries=retries)
        self.session.mount("http://", adapter)
        self.session.mount("https://", adapter)

        if https_proxy:
            self.session.proxies = {"https": https_proxy}

    def fetch_png(self, url: str) -> bytes:
        r = self.session.get(url, timeout=self.timeout, headers={"Accept": "image/png,*/*;q=0.8"})
        r.raise_for_status()
        ctype = (r.headers.get("Content-Type") or "").lower()
        if "png" not in ctype and not url.lower().endswith(".png"):
            raise ValueError(f"响应 Content-Type 非 PNG：{ctype}")
        return r.content
