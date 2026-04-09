# -*- coding: utf-8 -*-
"""Firefox Remote Agent 连接管理

Firefox Remote Agent 通过 --remote-debugging-port 暴露两个端点：
  - HTTP  http://host:port/json          → 获取 WebSocket URL
  - WS    ws://host:port/session         → BiDi over WebSocket

本模块负责：
1. 探测 Firefox 是否已就绪（HTTP /json 轮询）
2. 获取 BiDi WebSocket URL
3. 启动 Firefox 进程（如需要）
4. 自动端口分配
"""

import json
import socket
import subprocess
import time
import logging

logger = logging.getLogger("ruyipage")


def _probe_ws_url(ws_url, timeout=3):
    """尝试建立一次 WebSocket 握手，确认给定 BiDi URL 可用。"""
    import websocket

    ws = None
    try:
        ws = websocket.create_connection(
            ws_url, timeout=timeout, suppress_origin=True, enable_multithread=True
        )
        return True
    except Exception:
        return False
    finally:
        if ws:
            try:
                ws.close()
            except Exception:
                pass


def find_free_port(start=9222, end=9322):
    """在 [start, end) 范围内找一个空闲端口"""
    for port in range(start, end):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    raise RuntimeError("找不到空闲端口 [{}, {})".format(start, end))


def is_port_open(host, port, timeout=1.0):
    """检测端口是否可连接"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(timeout)
        try:
            s.connect((host, port))
            return True
        except (ConnectionRefusedError, socket.timeout, OSError):
            return False


def get_bidi_ws_url(host, port, timeout=30):
    """从 Firefox Remote Agent HTTP 端点获取 BiDi WebSocket URL

    Firefox 146+ 在 /json 返回：
      {"webSocketDebuggerUrl": "ws://host:port/session", ...}

    Args:
        host: 主机地址
        port: 远程调试端口
        timeout: 等待超时（秒）

    Returns:
        str: WebSocket URL，如 'ws://127.0.0.1:9222/session'
    """
    import urllib.request
    import urllib.error

    direct_ws = "ws://{}:{}".format(host, port)
    session_ws = "ws://{}:{}/session".format(host, port)

    # 先优先探测直连根路径。AdsPower / FlowerBrowser 暴露的 Firefox BiDi
    # 常见为 ws://host:port，而不是标准 Firefox Remote Agent 的 /session。
    if _probe_ws_url(direct_ws, timeout=3):
        return direct_ws

    deadline = time.time() + timeout
    url = "http://{}:{}/json".format(host, port)

    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=3) as resp:
                data = json.loads(resp.read().decode())
                # Firefox 返回顶层对象，含 webSocketDebuggerUrl
                if isinstance(data, dict):
                    ws = data.get("webSocketDebuggerUrl", "")
                    if ws:
                        return ws
                # 某些版本返回列表（CDP 兼容格式）
                if isinstance(data, list) and data:
                    ws = data[0].get("webSocketDebuggerUrl", "")
                    if ws:
                        return ws
        except (urllib.error.URLError, OSError, json.JSONDecodeError):
            pass
        time.sleep(0.5)

    # 降级：优先返回仍可握手的地址。
    if _probe_ws_url(session_ws, timeout=3):
        return session_ws

    return direct_ws


def wait_for_firefox(host, port, timeout=30):
    """等待 Firefox Remote Agent 就绪

    Args:
        host: 主机
        port: 端口
        timeout: 超时（秒）

    Returns:
        bool: 是否就绪
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        if is_port_open(host, port, timeout=1.0):
            return True
        time.sleep(0.3)
    return False


def launch_firefox(cmd, env=None):
    """启动 Firefox 进程

    Args:
        cmd: 命令行列表
        env: 环境变量字典（None 继承当前环境）

    Returns:
        subprocess.Popen 实例
    """
    import os

    kwargs = {
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
    }
    if env:
        kwargs["env"] = env

    # Windows 下隐藏控制台窗口
    if hasattr(subprocess, "CREATE_NO_WINDOW"):
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW

    logger.debug("启动 Firefox: %s", " ".join(cmd))
    return subprocess.Popen(cmd, **kwargs)
