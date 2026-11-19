#!/bin/bash
# 首次安装依赖（只需一次）：pywebview 用来开原生窗口
# 不装也能跑 —— 启动.py 会自动退回 Edge 应用窗口 / 浏览器
set -e
cd "$(dirname "$0")"
python3 -m pip install --upgrade pywebview
echo "安装完成。双击 启动.command 启动。"
