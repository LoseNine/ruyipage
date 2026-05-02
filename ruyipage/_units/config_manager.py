# -*- coding: utf-8 -*-
"""about:config 控制系统

支持三种配置文件 + 运行时修改：
  - user.js      : 用户级持久化，启动时覆盖 prefs.js
  - prefs.js     : Firefox 自动维护的实际值（只读）
  - policies.json: 企业策略，最高优先级

生效时机：
  - user.js / policies.json : 需重启（或 reload 部分 pref）
  - BiDi preload script      : 下次导航立即生效（JS 层覆盖）
  - Marionette               : 运行时读取（只读）

多实例隔离：每个 Firefox 实例使用独立 --profile 目录。
"""

import os
import re
import json
import logging

from .._functions.pref_utils import parse_pref_value as _parse, format_pref_value as _fmt

logger = logging.getLogger('ruyipage')

# ── user.js / prefs.js 读写 ───────────────────────────────────────────────

class _JsPrefsFile:
    """user.js 或 prefs.js 的读写封装（格式相同）"""

    def __init__(self, path: str):
        self.path = path

    def read_all(self) -> dict:
        if not os.path.exists(self.path):
            return {}
        with open(self.path, encoding='utf-8', errors='ignore') as f:
            content = f.read()
        result = {}
        for m in re.finditer(
                r'user_pref\s*\(\s*["\'](.+?)["\'],\s*(.+?)\s*\)', content):
            result[m.group(1)] = _parse(m.group(2))
        return result

    def read(self, key: str):
        if not os.path.exists(self.path):
            return None
        with open(self.path, encoding='utf-8', errors='ignore') as f:
            content = f.read()
        m = re.search(
            r'user_pref\s*\(\s*["\']' + re.escape(key) + r'["\'],\s*(.+?)\s*\)',
            content)
        return _parse(m.group(1)) if m else None

    def write(self, key: str, value):
        os.makedirs(os.path.dirname(self.path) or '.', exist_ok=True)
        content = ''
        if os.path.exists(self.path):
            with open(self.path, encoding='utf-8', errors='ignore') as f:
                content = f.read()
        line = 'user_pref("{}", {});'.format(key, _fmt(value))
        pat = r'user_pref\s*\(\s*["\']' + re.escape(key) + r'["\'].*?\);'
        if re.search(pat, content):
            content = re.sub(pat, line, content)
        else:
            content = content.rstrip('\n') + '\n' + line + '\n'
        with open(self.path, 'w', encoding='utf-8') as f:
            f.write(content)

    def write_many(self, prefs: dict):
        for k, v in prefs.items():
            self.write(k, v)

    def remove(self, key: str):
        if not os.path.exists(self.path):
            return
        with open(self.path, encoding='utf-8', errors='ignore') as f:
            content = f.read()
        pat = r'\nuser_pref\s*\(\s*["\']' + re.escape(key) + r'["\'].*?\);\n?'
        content = re.sub(pat, '\n', content)
        with open(self.path, 'w', encoding='utf-8') as f:
            f.write(content)

    def read_prefix(self, prefix: str) -> dict:
        return {k: v for k, v in self.read_all().items()
                if k.startswith(prefix)}


# ── policies.json 读写 ────────────────────────────────────────────────────

class _PoliciesFile:
    """policies.json 读写封装

    路径：<profile>/../distribution/policies.json
    或系统级：/etc/firefox/policies/policies.json（Linux）
    """

    def __init__(self, profile_path: str):
        # 优先写 profile 同级的 distribution 目录
        self.path = os.path.join(
            os.path.dirname(profile_path), 'distribution', 'policies.json')

    def read(self) -> dict:
        if not os.path.exists(self.path):
            return {}
        with open(self.path, encoding='utf-8') as f:
            return json.load(f)

    def write(self, policies: dict):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        existing = self.read()
        # 深度合并
        _deep_merge(existing, policies)
        with open(self.path, 'w', encoding='utf-8') as f:
            json.dump(existing, f, indent=2, ensure_ascii=False)

    def set_pref(self, key: str, value):
        """通过 policies.json Preferences 字段锁定 pref"""
        self.write({'policies': {'Preferences': {key: {'Value': value,
                                                        'Status': 'locked'}}}})

    def lock_pref(self, key: str, value):
        """锁定 pref（用户无法修改）"""
        self.set_pref(key, value)

    def unlock_pref(self, key: str):
        """解锁 pref"""
        data = self.read()
        prefs = data.get('policies', {}).get('Preferences', {})
        prefs.pop(key, None)
        with open(self.path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)


def _deep_merge(base: dict, override: dict):
    for k, v in override.items():
        if k in base and isinstance(base[k], dict) and isinstance(v, dict):
            _deep_merge(base[k], v)
        else:
            base[k] = v


# ── 主控制器 ──────────────────────────────────────────────────────────────

class ConfigManager:
    """about:config 完整控制系统

    API::

        cfg = ConfigManager(profile_path='/tmp/ff_profile')

        # 读取（优先级：Marionette > user.js > prefs.js）
        cfg.get('dom.webdriver.enabled')

        # 写入 user.js（持久化，重启生效）
        cfg.set('privacy.resistFingerprinting', True)

        # 批量写入
        cfg.set_many({'dom.webdriver.enabled': False,
                      'media.peerconnection.enabled': False})

        # 写入 policies.json（最高优先级，锁定）
        cfg.lock('dom.webdriver.enabled', False)

        # 读取 prefs.js（Firefox 实际运行值，只读）
        cfg.get_actual('dom.webdriver.enabled')

        # 读取所有用户自定义 prefs
        cfg.get_all(prefix='dom.')

        # 重置（从 user.js 移除）
        cfg.reset('privacy.resistFingerprinting')

        # 运行时生效（写入 user.js + 触发页面 reload）
        cfg.apply_now('dom.webdriver.enabled', False, page=page)

        # 多实例隔离：每个实例独立 profile
        cfg.isolate()  # 返回新的临时 profile 路径
    """

    def __init__(self, profile_path: str = None, marionette_port: int = 2828):
        self._profile = profile_path
        self._marionette_port = marionette_port
        self._user_js = _JsPrefsFile(
            os.path.join(profile_path, 'user.js')) if profile_path else None
        self._prefs_js = _JsPrefsFile(
            os.path.join(profile_path, 'prefs.js')) if profile_path else None
        self._policies = _PoliciesFile(profile_path) if profile_path else None
        self._marionette = None

    def _get_marionette(self):
        if self._marionette is None:
            from .._adapter.marionette import MarionetteClient
            self._marionette = MarionetteClient(port=self._marionette_port)
        return self._marionette

    # ── 读取 ──────────────────────────────────────────────────────────────

    def get(self, key: str):
        """读取 pref（Marionette → user.js → prefs.js → None）"""
        # 1. Marionette 运行时值（最准确）
        try:
            m = self._get_marionette()
            if m.is_available():
                val = m.get_pref(key)
                if val is not None:
                    return val
        except Exception as e:
            logger.debug("Marionette 读取失败，降级到 user.js: %s", e)
        # 2. user.js
        if self._user_js:
            val = self._user_js.read(key)
            if val is not None:
                return val
        # 3. prefs.js（Firefox 实际值）
        if self._prefs_js:
            return self._prefs_js.read(key)
        return None

    def get_actual(self, key: str):
        """从 prefs.js 读取 Firefox 实际运行值（只读）"""
        return self._prefs_js.read(key) if self._prefs_js else None

    def get_all(self, prefix: str = '') -> dict:
        """读取所有 user.js 中匹配前缀的 prefs"""
        if not self._user_js:
            return {}
        return self._user_js.read_prefix(prefix)

    # ── 写入 ──────────────────────────────────────────────────────────────

    def set(self, key: str, value):
        """写入 user.js（持久化，重启后生效）"""
        if not self._user_js:
            raise RuntimeError('未设置 profile 路径')
        self._user_js.write(key, value)

    def set_many(self, prefs: dict):
        """批量写入 user.js"""
        if not self._user_js:
            raise RuntimeError('未设置 profile 路径')
        self._user_js.write_many(prefs)

    def reset(self, key: str):
        """从 user.js 移除 pref（恢复默认）"""
        if self._user_js:
            self._user_js.remove(key)

    def lock(self, key: str, value):
        """通过 policies.json 锁定 pref（最高优先级，用户无法修改）"""
        if not self._policies:
            raise RuntimeError('未设置 profile 路径')
        self._policies.lock_pref(key, value)

    def unlock(self, key: str):
        """解锁 policies.json 中的 pref"""
        if self._policies:
            self._policies.unlock_pref(key)

    def apply_now(self, key: str, value, page=None):
        """运行时生效：写入 user.js 后触发页面 reload

        注意：只有部分 pref 支持 reload 后生效，
        需要重启的 pref（如 network.proxy.*）需调用 restart()。

        Args:
            key: pref 名称
            value: pref 值
            page: FirefoxPage 实例（用于触发 reload）
        """
        self.set(key, value)
        if page is not None:
            try:
                page.refresh()
            except Exception as e:
                logger.debug('apply_now reload 失败: %s', e)

    def apply_many_now(self, prefs: dict, page=None):
        """批量运行时生效"""
        self.set_many(prefs)
        if page is not None:
            try:
                page.refresh()
            except Exception as e:
                logger.debug('apply_many_now reload 失败: %s', e)

    # ── 多实例隔离 ────────────────────────────────────────────────────────

    def isolate(self, base_dir: str = None) -> str:
        """创建独立的临时 profile 目录（多实例隔离）

        将当前 user.js 内容复制到新 profile，
        返回新 profile 路径供 FirefoxOptions.set_profile() 使用。

        Args:
            base_dir: 临时目录父路径，None 使用系统临时目录

        Returns:
            新 profile 路径
        """
        import tempfile, shutil
        new_profile = tempfile.mkdtemp(prefix='ruyipage_', dir=base_dir)
        if self._profile and os.path.exists(self._profile):
            for fname in ('user.js',):
                src = os.path.join(self._profile, fname)
                if os.path.exists(src):
                    shutil.copy2(src, os.path.join(new_profile, fname))
        logger.debug('隔离 profile: %s', new_profile)
        return new_profile

    # ── 信息查询 ──────────────────────────────────────────────────────────

    def diff(self) -> dict:
        """对比 user.js 与 prefs.js 的差异

        Returns:
            {key: {'user': val, 'actual': val}} 仅包含有差异的项
        """
        user = self._user_js.read_all() if self._user_js else {}
        actual = self._prefs_js.read_all() if self._prefs_js else {}
        result = {}
        for k in set(list(user.keys()) + list(actual.keys())):
            u, a = user.get(k), actual.get(k)
            if u != a:
                result[k] = {'user': u, 'actual': a}
        return result
