import asyncio
from typing import Optional

from hoshino import Service, get_bot, priv

from .client import RealtimeItemAdded

sv = Service(
    "jellyfin-notify",
    use_priv=priv.NORMAL,
    manage_priv=priv.ADMIN,
    enable_on_default=False,
    visible=True,
    help_="Jellyfin 新媒体入库推送（启用后自动推送到本群）",
    bundle="通知",
)

_daemon_task: Optional[asyncio.Task] = None


async def _broadcast_payload(payload: dict):
    msg = payload.get("message") or ""
    if not msg:
        return
    tag = payload.get("subject") or "jellyfin"
    await sv.broadcast(msg, TAG=tag, interval_time=0.5)


async def _jellyfin_daemon():
    while True:
        client: Optional[RealtimeItemAdded] = None
        try:
            from hoshino.util import load_yaml_config
            cfg = load_yaml_config('jellyfin_notify')
            if not cfg:
                sv.logger.error('未找到 hoshino/config/jellyfin_notify.yaml，请从 config_example 复制并填写')
                await asyncio.sleep(60)
                continue
            if not cfg.get("server_url") or not cfg.get("username"):
                sv.logger.error('jellyfin_notify 配置缺少 server_url 或 username')
                await asyncio.sleep(60)
                continue
            client = RealtimeItemAdded(cfg, send_func=_broadcast_payload, logger=sv.logger)
            await client.login()
            await client.run_ws()
        except asyncio.CancelledError:
            sv.logger.info("Jellyfin notify daemon 已停止")
            raise
        except Exception as e:
            sv.logger.exception(e)
            sv.logger.error("Jellyfin notify 异常，5s 后重启: %s", e)
            await asyncio.sleep(5)
        finally:
            if client is not None:
                try:
                    await client.close()
                except Exception:
                    pass


def _start_daemon():
    global _daemon_task
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        sv.logger.error("无法获取事件循环，Jellyfin daemon 未启动")
        return
    if _daemon_task is not None and not _daemon_task.done():
        return
    _daemon_task = loop.create_task(_jellyfin_daemon())
    sv.logger.info("Jellyfin notify daemon 已启动")


bot = get_bot()


@bot.on_startup
async def _on_startup():
    _start_daemon()
