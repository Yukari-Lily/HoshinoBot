# 每天 60 秒读懂世界推送插件

每天定时推送「每天 60 秒读懂世界」简报。

## 功能

- **每天 8:00 定时推送**：每天早晨 8 点自动向开启本服务的群发送今日新闻（优先发送汇总长图，图片失败自动降级为文字）。
- **主动查询**：发送 `news` 或 `新闻` 即可获取今日新闻。
- **高可用多源容灾**：内置多数据源故障切换（主源 `60s.viki.moe` 与备用源 `api.guiguiya.com`），自动抹平字段差异并抵御单点 DNS 波动。
- **智能 CDN 镜像路由**：针对 GitHub 静态长图自动路由至高速健康镜像（如 `testingcf.jsdelivr.net`、`fastly.jsdelivr.net` 等），解决公共镜像失效（如 HTTP 421 错误）导致的图片拉取失败。

## 指令

| 指令 | 说明 |
| :--- | :--- |
| `news` / `新闻` | 获取今日 60 秒读懂世界简报 |

群管理可使用 Hoshino 原生服务管理命令（插件默认关闭）：
- `启用 daily-60s`：在本群开启每日推送与指令响应
- `禁用 daily-60s`：在本群关闭服务

## 配置

配置文件：`hoshino/config/daily_news.yaml`

```yaml
# 每天推送时间 (默认 08:00)
push_time: "08:00"

# 发送模式: image (长图，推荐) 或 text (纯文本)
send_mode: "image"

# 多群广播时各群发送间隔秒数 (防风控)
broadcast_interval: 1.0

# 新闻数据 API 接口列表 (按顺序容灾重试，可选配置)
# api_urls:
#   - "https://60s.viki.moe/v2/60s"
#   - "https://api.guiguiya.com/api/60s?type=json"
#   - "https://60s-api.viki.moe/v2/60s"
```
