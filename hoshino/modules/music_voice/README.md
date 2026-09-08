# music_voice

面向 HoshinoBot / NoneBot 1 的精简点歌插件，行为移植自
[koishi-plugin-music-voice](https://github.com/idranme/koishi-plugin-music-voice)（MIT）。

发送 `点歌歌名` 或 `点歌 歌名`，再在 45 秒内发送图片歌单中的序号。默认搜索 10 条，选中后以 OneBot 语音消息发送；超时后歌单图片会静默撤回。

将 `hoshino/config_example/music_voice.yaml` 复制到 `hoshino/config/` 可修改精简配置。
