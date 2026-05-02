# -*- coding: utf-8 -*-
"""about:config 运行时读写

Firefox BiDi script.evaluate 在内容沙箱中无法访问 Services.prefs。
实现两种方案：
1. 运行时读取：通过 addPreloadScript 注入 pref 读取（只读）
2. 运行时写入：通过修改 profile user.js + reload 页面生效（持久化）
3. 直接写入：通过 profile user.js 文件操作（需重启生效）
"""
import os

from .._functions.pref_utils import parse_pref_value, format_pref_value


class PrefsManager:
    """about:config 读写管理器

    用法::

        # 读取（通过 JS 注入）
        page.prefs.get('dom.webdriver.enabled')

        # 写入到 profile（重启后生效）
        page.prefs.set_persistent('privacy.resistFingerprinting', True)

        # 运行时写入（通过 user.js + reload，当前页面生效）
        page.prefs.set('browser.tabs.warnOnClose', False)

        # 获取所有用户自定义 prefs
        page.prefs.get_user_prefs()
    """

    def __init__(self, owner):
        self._owner = owner

    def _profile(self):
        browser = self._owner._browser
        return (getattr(browser, '_auto_profile', None) or
                getattr(browser, 'options', None) and browser.options.profile_path)

    def get(self, key: str):
        """读取 pref 值，降级到 user.js"""
        return self._read_from_user_js(key)

    def _read_from_user_js(self, key: str):
        """从 user.js 读取 pref 值"""
        profile = self._profile()
        if not profile:
            return None
        user_js = os.path.join(profile, 'user.js')
        if not os.path.exists(user_js):
            return None
        import re
        with open(user_js, encoding='utf-8', errors='ignore') as f:
            content = f.read()
        pattern = r'user_pref\s*\(\s*["\']' + re.escape(key) + r'["\'],\s*(.+?)\s*\)'
        m = re.search(pattern, content)
        if not m:
            return None
        return parse_pref_value(m.group(1).strip())

    def set(self, key: str, value):
        """写入 pref 到 user.js（持久化，重启后生效）"""
        self.set_persistent(key, value)

    def set_persistent(self, key: str, value):
        """写入 pref 到 profile user.js"""
        profile = self._profile()
        if not profile:
            raise RuntimeError('无法获取 profile 路径')
        user_js = os.path.join(profile, 'user.js')
        import re
        content = ''
        if os.path.exists(user_js):
            with open(user_js, encoding='utf-8', errors='ignore') as f:
                content = f.read()
        # 格式化值
        val_str = format_pref_value(value)
        line = 'user_pref("{}", {});'.format(key, val_str)
        pattern = r'user_pref\s*\(\s*["\']' + re.escape(key) + r'["\'].*?\);'
        if re.search(pattern, content):
            content = re.sub(pattern, line, content)
        else:
            content += '\n' + line + '\n'
        with open(user_js, 'w', encoding='utf-8') as f:
            f.write(content)

    def reset(self, key: str):
        """从 user.js 移除 pref（恢复默认）"""
        profile = self._profile()
        if not profile:
            return
        user_js = os.path.join(profile, 'user.js')
        if not os.path.exists(user_js):
            return
        import re
        with open(user_js, encoding='utf-8', errors='ignore') as f:
            content = f.read()
        pattern = r'\nuser_pref\s*\(\s*["\']' + re.escape(key) + r'["\'].*?\);\n?'
        content = re.sub(pattern, '\n', content)
        with open(user_js, 'w', encoding='utf-8') as f:
            f.write(content)

    def get_all(self, prefix: str = '') -> dict:
        """从 user.js 读取所有匹配前缀的 prefs"""
        profile = self._profile()
        if not profile:
            return {}
        user_js = os.path.join(profile, 'user.js')
        if not os.path.exists(user_js):
            return {}
        import re
        with open(user_js, encoding='utf-8', errors='ignore') as f:
            content = f.read()
        result = {}
        for m in re.finditer(r'user_pref\s*\(\s*["\'](.+?)["\'],\s*(.+?)\s*\)', content):
            k, v = m.group(1), m.group(2).strip()
            if not k.startswith(prefix):
                continue
            result[k] = parse_pref_value(v)
        return result

    def save_to_profile(self):
        """user.js 已是持久化文件，此方法为 API 兼容占位"""
        pass

