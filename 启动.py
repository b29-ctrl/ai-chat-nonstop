#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""桌面端启动器：本地服务 + 原生窗口（pywebview / Edge 应用窗口 / 浏览器兜底）。

用法：
  python 启动.py             # 原生窗口（默认新界面 web_flow）
  python 启动.py --ui classic  # 回退旧界面 web/
  python 启动.py --browser   # 强制 Edge 应用窗口
  python 启动.py --no-window # 只起服务，不打开窗口
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
import urllib.parse
import urllib.request
import webbrowser
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import 服务 as svc  # noqa: E402


def wait_health(port: int, timeout: float = 8.0) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=1) as r:
                if r.status == 200:
                    return True
        except Exception:
            time.sleep(0.15)
    return False


def open_edge(url: str):
    if not sys.platform.startswith("win"):
        return None
    edge = Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe")
    if not edge.exists():
        return None
    proc = subprocess.Popen(
        [str(edge), f"--app={url}", "--window-size=1380,900"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    return proc


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=0)
    ap.add_argument("--ui", choices=("classic", "flow"), default="flow",
                    help="flow=新前端 web_flow/ 目录（默认）；classic=旧版 web/ 目录")
    ap.add_argument("--no-window", action="store_true")
    ap.add_argument("--browser", action="store_true")
    args = ap.parse_args()

    if args.ui == "flow":
        os.environ["AI_CHAT_WEB_DIR"] = "web_flow"
        # 服务.py 在导入时就固化了 WEB 全局；这里必须同步覆盖，否则窗口仍跑旧界面。
        svc.WEB = HERE / "web_flow"
    else:
        svc.WEB = HERE / "web"

    httpd, port, token = svc.start_server(args.port)
    url = f"http://127.0.0.1:{port}/?token={urllib.parse.quote(token)}"
    if not wait_health(port):
        print("本地服务启动失败", file=sys.stderr)
        httpd.shutdown()
        return 1
    print(f"{svc.APP_TITLE} v{svc.APP_VERSION}")
    print(f"本地地址：http://127.0.0.1:{port}/")

    if args.no_window:
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            pass
        httpd.shutdown()
        return 0

    # 1) pywebview 原生窗口
    if not args.browser:
        try:
            import webview  # pywebview

            webview.create_window(
                svc.APP_TITLE, url, width=1380, height=900,
                min_size=(960, 640), background_color="#0b1220",
                text_select=True,
            )
            icon = svc.WEB / "favicon-black.ico"
            webview.start(icon=str(icon) if icon.exists() else None)
            httpd.shutdown()
            return 0
        except Exception as e:  # noqa: BLE001
            print(f"pywebview 不可用（{e}），改用 Edge 应用窗口…", file=sys.stderr)

    # 2) Edge 应用窗口（零安装、看起来就是桌面应用）
    proc = open_edge(url)
    if proc:
        print("已用 Edge 应用窗口打开；关闭窗口即退出。")
        try:
            proc.wait()
        except KeyboardInterrupt:
            proc.terminate()
        httpd.shutdown()
        return 0

    # 3) 浏览器兜底
    webbrowser.open(url)
    input("浏览器已打开。按 Enter 关闭本地服务…")
    httpd.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
