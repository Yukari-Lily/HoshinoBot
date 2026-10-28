#!/bin/sh
set -e

CONFIG_DIR=/HoshinoBot/hoshino/config

# 挂载目录不存在或为空时，从 config_example 初始化
if [ ! -d "$CONFIG_DIR" ] || [ -z "$(ls -A "$CONFIG_DIR" 2>/dev/null)" ]; then
    echo "[entrypoint] 未检测到 config，从 config_example 初始化"
    mkdir -p "$CONFIG_DIR"
    cp -r /HoshinoBot/hoshino/config_example/. "$CONFIG_DIR"/
fi

exec "$@"
