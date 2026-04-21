# -*- coding: utf-8 -*-
"""杂项工具函数"""

import re
import time
import socket

from .sleep import sleep as _sleep


def wait_until(condition, timeout=10, interval=0.3):
    """等待条件满足

    Args:
        condition: 条件函数
        timeout: 超时（秒）
        interval: 检查间隔（秒）

    Returns:
        条件函数的返回值，超时返回 None
    """
    end_time = time.time() + timeout
    while time.time() < end_time:
        try:
            result = condition()
            if result:
                return result
        except Exception:
            pass
        _sleep(interval)
    return None


def is_port_open(host, port, timeout=2):
    """检查端口是否开放"""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        sock.connect((host, int(port)))
        sock.close()
        return True
    except Exception:
        return False


def find_free_port(start=9222, end=9322):
    """查找可用端口"""
    for port in range(start, end):
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.bind(('127.0.0.1', port))
            sock.close()
            return port
        except OSError:
            continue
    raise RuntimeError('在端口范围 {}-{} 中找不到可用端口'.format(start, end))


def clean_text(text):
    """清理文本（去除多余空白）"""
    if not text:
        return ''
    return re.sub(r'\s+', ' ', text).strip()


def make_valid_filename(name, max_length=50):
    """生成合法文件名"""
    name = re.sub(r'[\\/:*?"<>|]', '', name)
    return name[:max_length]
