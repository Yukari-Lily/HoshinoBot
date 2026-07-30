# -*- coding: utf-8 -*-
import logging
import os
from typing import Dict, Optional

import hoshino

DATA_DIR = os.path.join(os.path.dirname(hoshino.config.__file__), 'wplace_alerter_data')


def ensure_dir(path: str) -> None:
    os.makedirs(path, exist_ok=True)


def _safe_key(key: str) -> str:
    """Reject path-like art keys (config is trusted but still guard)."""
    k = str(key).strip()
    if not k or k in (".", "..") or "/" in k or "\\" in k:
        raise ValueError(f"非法 art key: {key!r}")
    return k


def art_data_path(key: str) -> str:
    """Path for art data dir (does not create)."""
    return os.path.join(DATA_DIR, _safe_key(key))


def art_dir(key: str) -> str:
    p = art_data_path(key)
    ensure_dir(p)
    return p


def get_https_proxy(cfg: dict) -> Optional[str]:
    p = cfg.get("https_proxy")
    if p:
        return str(p).strip() or None
    proxy_obj = cfg.get("proxy") or {}
    p2 = proxy_obj.get("https")
    return (str(p2).strip() or None) if p2 else None


def load_config(logger: Optional[logging.Logger] = None) -> Dict:
    """Load config from hoshino/config/wplace_alerter.yaml."""
    from hoshino.util import load_yaml_config
    cfg = load_yaml_config('wplace_alerter')
    if not cfg:
        raise FileNotFoundError(
            '未找到 hoshino/config/wplace_alerter.yaml，请从 config_example 复制并填写'
        )
    return cfg
