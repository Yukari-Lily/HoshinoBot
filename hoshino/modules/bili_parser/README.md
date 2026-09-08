# bili_parser

面向 HoshinoBot / NoneBot 1 的精简 Bilibili 链接解析插件，行为移植自
[@summonhim/koishi-plugin-bili-parser](https://github.com/SummonHIM/koishi-plugin-bili-parser)（MIT）。

默认不引用原消息；单条消息最多解析 3 个链接；视频支持直接发送 AV/BV 号。输出格式按迁移时提供的 Koishi 配置固化，移除了 Puppeteer 和模板引擎依赖。

将 `hoshino/config_example/bili_parser.yaml` 复制到 `hoshino/config/` 可修改精简配置。
