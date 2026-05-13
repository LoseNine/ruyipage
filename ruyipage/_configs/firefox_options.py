# -*- coding: utf-8 -*-
"""Firefox 浏览器启动配置"""

import os
import re
import sys


class FirefoxOptions(object):
    """Firefox 浏览器启动选项

    用法::

        opts = FirefoxOptions()
        opts.set_port(9222)
        opts.headless()
        opts.set_proxy('http://127.0.0.1:7890')
        opts.set_fpfile('/path/to/fingerprint.json')
        opts.set_window_size(1920, 1080)
        page = FirefoxPage(opts)

    新手最常改的基础项：
        1. 浏览器程序路径
           例如 Firefox 安装在非默认位置时，用 ``set_browser_path()``。
        2. 用户目录 / profile 目录
           例如想复用自己已有登录态、Cookie、扩展时，用 ``set_user_dir()``。
        3. 端口
           例如同机多开时，用 ``set_port()``。
    """

    def __init__(self):
        # 浏览器可执行文件路径
        if sys.platform == "win32":
            self._browser_path = r"C:\Program Files\Mozilla Firefox\firefox.exe"
        elif sys.platform == "darwin":
            self._browser_path = "/Applications/Firefox.app/Contents/MacOS/firefox"
        else:
            self._browser_path = "firefox"

        self._address = "127.0.0.1"
        self._port = 9222
        self._profile_path = None
        self._arguments = []
        self._preferences = {}
        self._headless = False
        self._download_path = "."
        self._load_mode = "normal"  # 'normal', 'eager', 'none'
        self._timeouts = {
            "base": 10,
            "page_load": 30,
            "script": 30,
        }
        self._existing_only = False
        self._close_on_exit = True
        self._retry_times = 10
        self._retry_interval = 2.0
        self._proxy = None
        self._auto_port = False
        self._user_context = None  # 容器标签页
        self._fpfile = None  # 指纹配置文件路径
        self._private_mode = False  # Firefox 私密浏览模式
        self._user_prompt_handler = None  # session.UserPromptHandler
        self._xpath_picker_enabled = False  # 页面 XPath 选择浮窗
        self._action_visual_enabled = False  # 鼠标行为可视化调试
        self._human_algorithm = "bezier"  # 拟人鼠标轨迹算法
        self._trace_enabled = False  # debug trace 记录
        self._failure_snapshot_enabled = False  # 失败自动诊断快照
        self._snapshot_dir = None  # 诊断快照保存目录
        # 某些 Firefox / 指纹浏览器 / 个别机器环境在带 --marionette
        # 启动时会直接崩溃或闪退，导致后续 BiDi 端口连接失败。
        # 默认保持启用以兼容历史行为，但允许用户显式关闭。
        self._marionette_enabled = True  # 是否启用 Marionette 启动通道

    # ===== 属性读取 =====

    @property
    def browser_path(self):
        return self._browser_path

    @property
    def address(self):
        return "{}:{}".format(self._address, self._port)

    @property
    def host(self):
        return self._address

    @property
    def port(self):
        return self._port

    @property
    def profile_path(self):
        return self._profile_path

    @property
    def arguments(self):
        return self._arguments[:]

    @property
    def preferences(self):
        return self._preferences.copy()

    @property
    def is_headless(self):
        return self._headless

    @property
    def download_path(self):
        return self._download_path

    @property
    def load_mode(self):
        return self._load_mode

    @property
    def timeouts(self):
        return self._timeouts.copy()

    @property
    def is_existing_only(self):
        return self._existing_only

    @property
    def retry_times(self):
        return self._retry_times

    @property
    def close_on_exit_enabled(self):
        """Python 进程退出时是否自动关闭浏览器。"""
        return self._close_on_exit

    @property
    def retry_interval(self):
        return self._retry_interval

    @property
    def proxy(self):
        return self._proxy

    @property
    def auto_port(self):
        return self._auto_port

    @property
    def fpfile(self):
        """指纹配置文件路径"""
        return self._fpfile

    @property
    def is_private_mode(self):
        """是否启用 Firefox 私密浏览模式。"""
        return self._private_mode

    @property
    def user_prompt_handler(self):
        """session 级默认用户提示框处理策略。"""
        return (
            dict(self._user_prompt_handler)
            if isinstance(self._user_prompt_handler, dict)
            else None
        )

    @property
    def xpath_picker_enabled(self):
        """是否在启动时自动注入 XPath 选择浮窗。"""
        return self._xpath_picker_enabled

    @property
    def action_visual_enabled(self):
        """是否启用鼠标行为可视化调试模式。"""
        return self._action_visual_enabled

    @property
    def human_algorithm(self):
        """默认拟人鼠标轨迹算法。"""
        return self._human_algorithm

    @property
    def trace_enabled(self):
        """是否启用 debug trace 记录。"""
        return self._trace_enabled

    @property
    def failure_snapshot_enabled(self):
        """是否启用失败自动诊断快照。"""
        return self._failure_snapshot_enabled

    @property
    def snapshot_dir(self):
        """诊断快照保存目录。"""
        return self._snapshot_dir

    @property
    def marionette_enabled(self):
        """是否启用 Marionette 启动通道。"""
        return self._marionette_enabled

    # ===== 链式设置方法 =====

    def set_browser_path(self, path):
        """设置浏览器可执行文件路径。

        Args:
            path: Firefox 可执行文件路径。
                常见值：
                Windows: ``r'C:\\Program Files\\Mozilla Firefox\\firefox.exe'``
                macOS: ``'/Applications/Firefox.app/Contents/MacOS/firefox'``
                Linux: ``'/usr/bin/firefox'`` or a directory containing ``firefox``

        Returns:
            self: 原配置对象，便于链式调用。

        适用场景：
            - Firefox 安装在非默认目录
            - 同时存在多个 Firefox 版本，想指定其中一个
            - 便携版 Firefox 需要显式指定 exe 路径
        """
        path = os.path.expanduser(str(path))
        if os.path.isdir(path):
            exe_name = "firefox.exe" if sys.platform == "win32" else "firefox"
            path = os.path.join(path, exe_name)
        self._browser_path = path
        return self

    def set_address(self, address):
        """设置连接地址（host:port 或仅 host）

        Args:
            address: '127.0.0.1:9222' 或 '127.0.0.1'

        Returns:
            self
        """
        if ":" in str(address):
            parts = str(address).rsplit(":", 1)
            self._address = parts[0]
            self._port = int(parts[1])
        else:
            self._address = str(address)
        return self

    def set_port(self, port):
        """设置远程调试端口

        Args:
            port: 端口号

        Returns:
            self
        """
        self._port = int(port)
        return self

    def set_profile(self, path):
        """设置 Firefox 配置文件路径。

        Args:
            path: profile 目录路径。
                这就是很多人常说的 userdir / 用户目录。
                常见值：
                Windows: ``r'D:\firefox_profile'``
                Linux/macOS: ``'/Users/name/firefox_profile'``

        Returns:
            self: 原配置对象，便于链式调用。

        适用场景：
            - 想复用已有登录状态、Cookie、本地存储
            - 想让浏览器持久保存数据，而不是每次用临时目录
            - 想加载已安装到该 profile 的扩展、证书、首选项

        说明：
            - 如果不设置，ruyipage 会自动创建一个临时 profile。
            - 临时 profile 适合一次性测试，但关闭后通常会被清理。
            - 想长期复用浏览器数据时，建议显式设置这个路径。
        """
        self._profile_path = path
        return self

    def set_user_dir(self, path):
        """设置用户目录（userdir）。

        这是 ``set_profile()`` 的新手友好别名。

        Args:
            path: 用户目录 / profile 目录路径。
                常见值：``r'D:\\my_firefox_userdir'``。

        Returns:
            self: 原配置对象，便于链式调用。

        适用场景：
            - 你只知道“我要设置 userdir”，但不关心 Firefox 内部叫 profile
            - 教程和快速开始里希望用更直白的名字
        """
        return self.set_profile(path)

    def set_argument(self, arg, value=None):
        """添加启动参数

        Args:
            arg: 参数名，如 '--width'
            value: 参数值，如 '1920'

        Returns:
            self
        """
        if value is not None:
            self._arguments.append("{}={}".format(arg, value))
        else:
            if arg not in self._arguments:
                self._arguments.append(arg)
        return self

    def remove_argument(self, arg):
        """移除启动参数

        Args:
            arg: 参数名

        Returns:
            self
        """
        self._arguments = [
            a for a in self._arguments if a != arg and not a.startswith(arg + "=")
        ]
        return self

    def set_pref(self, key, value):
        """设置 Firefox 首选项（about:config）

        Args:
            key: 首选项名称
            value: 首选项值

        Returns:
            self
        """
        self._preferences[key] = value
        return self

    def set_user_prompt_handler(self, handler):
        """设置 session 级默认用户提示框处理策略。

        Args:
            handler: dict，键可包含 alert/beforeUnload/confirm/default/file/prompt
                     值必须为 'accept' / 'dismiss' / 'ignore'

        Returns:
            self
        """
        self._user_prompt_handler = dict(handler) if handler else None
        return self

    def headless(self, on_off=True):
        """设置无头模式

        Args:
            on_off: True 启用无头，False 禁用

        Returns:
            self
        """
        self._headless = on_off
        return self

    def set_proxy(self, proxy):
        """设置代理

        Args:
            proxy: 代理地址，如 'http://127.0.0.1:7890' 或 'socks5://127.0.0.1:1080'

        Returns:
            self
        """
        self._proxy = proxy
        return self

    def set_download_path(self, path):
        """设置下载路径

        Args:
            path: 下载目录路径

        Returns:
            self
        """
        self._download_path = os.path.abspath(path)
        return self

    def set_load_mode(self, mode):
        """设置加载模式

        Args:
            mode: 'normal' 完全加载 / 'eager' DOMContentLoaded / 'none' 不等待

        Returns:
            self
        """
        if mode not in ("normal", "eager", "none"):
            raise ValueError("load_mode 必须是 'normal', 'eager' 或 'none'")
        self._load_mode = mode
        return self

    def set_timeouts(self, base=None, page_load=None, script=None):
        """设置各种超时时间（秒）

        Args:
            base: 基础超时（元素查找等）
            page_load: 页面加载超时
            script: 脚本执行超时

        Returns:
            self
        """
        if base is not None:
            self._timeouts["base"] = base
        if page_load is not None:
            self._timeouts["page_load"] = page_load
        if script is not None:
            self._timeouts["script"] = script
        return self

    def existing_only(self, on_off=True):
        """仅连接已有浏览器，不启动新的

        Args:
            on_off: True 仅连接

        Returns:
            self
        """
        self._existing_only = on_off
        return self

    def close_on_exit(self, on_off=True):
        """设置 Python 进程退出时是否自动关闭浏览器。

        Args:
            on_off: ``True`` 表示当前 Python 程序退出时自动关闭由 ruyipage
                    启动的浏览器；``False`` 表示仅断开连接，不主动关闭浏览器。

        Returns:
            self

        说明：
            - 默认值为 ``True``，更符合“脚本结束即收尾”的直觉。
            - 对 ``existing_only(True)`` 接管的外部浏览器，此选项不会强制杀掉
              外部进程；退出时仍只做断开连接，避免误关用户自己打开的浏览器。
            - 对 ruyipage 自动创建的临时 profile，若执行完整关闭，会一并清理
              该临时目录。
        """
        self._close_on_exit = bool(on_off)
        return self

    def set_auto_port(self, on_off=True):
        """自动寻找可用端口

        Args:
            on_off: True 自动端口 / int 指定范围起始端口

        Returns:
            self
        """
        self._auto_port = on_off
        return self

    def set_retry(self, times=None, interval=None):
        """设置连接重试

        Args:
            times: 重试次数
            interval: 重试间隔（秒）

        Returns:
            self
        """
        if times is not None:
            self._retry_times = times
        if interval is not None:
            self._retry_interval = interval
        return self

    def set_fpfile(self, path):
        """设置指纹配置文件路径

        指纹配置文件用于浏览器指纹伪装，通过 --fpfile 参数传递给 Firefox。

        Args:
            path: 指纹配置文件的路径

        Returns:
            self
        """
        self._fpfile = path
        return self

    def smart_fingerprint(self, **kwargs):
        """一站式智能指纹配置（链式调用入口）。

        基于 ``firefox-fingerprintBrowser`` 内核与 ruyipage 内置的 22 套
        Windows 真机硬件特征 + 30 国语言映射，自动完成：

        1. 出口 IP / 地理位置探测（5 个数据源回退，可选 IPv6 富化）；
        2. 国家校验（``require_country`` 不匹配直接抛 ``CountryMismatchError``）；
        3. 随机抽取硬件指纹 + 拼装 Firefox 151 ±2 UA + 随机 canvas 种子；
        4. 写入符合内核 ``key:value`` 字段顺序的 ``fpfile.txt``；
        5. 自动配置当前 ``FirefoxOptions``：proxy / userdir / fpfile / 窗口大小。

        所有关键字参数透传到 :func:`ruyipage.apply_smart_fingerprint`，常用
        参数包括 ``proxy_host`` / ``proxy_port`` / ``proxy_user`` / ``proxy_pwd``
        / ``require_country`` / ``base_dir`` / ``logger`` 等。

        Returns:
            FingerprintContext: 指纹上下文。可调用 ``ctx.apply_emulation(page)``
            注入 BiDi 仿真覆盖层，或 ``ctx.summary()`` 输出单行日志。

        Raises:
            CountryMismatchError: 出口 IP 国家与 ``require_country`` 不一致。
            GeoError: 5 个 geo 数据源全部失败。
            FingerprintConfigError: 内置 JSON 数据文件损坏。

        Example::

            opts = FirefoxOptions().set_port(9222)
            opts.set_browser_path(r"C:/Program Files/Mozilla Firefox/firefox.exe")
            ctx = opts.smart_fingerprint(
                proxy_host="proxy.example.com", proxy_port=8080,
                proxy_user="u", proxy_pwd="p",
                require_country="US",
                logger=print,
            )
            page = FirefoxPage(opts)
            ctx.apply_emulation(page)
        """
        # Lazy import 避免与 ruyipage.__init__ 的循环依赖。
        from .._fingerprint import apply_smart_fingerprint
        return apply_smart_fingerprint(self, **kwargs)

    def private_mode(self, on_off=True):
        """设置 Firefox 私密浏览模式。

        Args:
            on_off: ``True`` 启用私密模式，``False`` 关闭。

        Returns:
            self

        说明：
            - 启用后会在启动命令中加入 ``-private``。
            - 这与临时 profile / user context 不同，属于 Firefox 原生私密浏览模式。
        """
        self._private_mode = bool(on_off)
        return self

    def enable_xpath_picker(self, on_off=True):
        """设置是否在页面中启用 XPath 选择浮窗。

        Args:
            on_off: ``True`` 启用，``False`` 关闭。

        Returns:
            self

        说明：
            - 启用后会在页面右下角注入一个半透明磨砂玻璃浮窗。
            - 点击页面元素时会锁定并显示元素名、文本、绝对/相对 XPath、中心点坐标。
            - 点击浮窗中的“解锁”后，才会重新允许选择下一个元素。
        """
        self._xpath_picker_enabled = bool(on_off)
        return self

    def enable_action_visual(self, on_off=True):
        """设置是否启用鼠标行为可视化调试模式。

        Args:
            on_off: ``True`` 启用，``False`` 关闭。

        Returns:
            self

        说明：
            - 启用后页面上会显示实时鼠标坐标指示器。
            - 拟人化移动时渲染贝塞尔曲线轨迹。
            - 点击位置显示扩散圆环 + 十字准星动画。
            - 键盘输入在右上角短暂显示按键文字。
        """
        self._action_visual_enabled = bool(on_off)
        return self

    def set_human_algorithm(self, name="bezier"):
        """设置默认拟人鼠标轨迹算法。

        Args:
            name: 轨迹算法名。
                当前支持：
                - ``"bezier"``：当前默认算法，轨迹更平滑，支持 ``style`` 变体
                - ``"windmouse"``：模拟风力 + 重力拖拽的轨迹，路径更飘逸

        Returns:
            self

        说明：
            - 默认值为 ``"bezier"``，兼容已有行为。
            - 该设置会作为 ``page.actions.human_move()`` /
              ``page.actions.human_click()`` 的默认算法。
            - 单次调用时可通过 ``algorithm=...`` 覆盖这里的默认值。
        """
        value = str(name or "bezier").strip().lower()
        if value not in ("bezier", "windmouse"):
            raise ValueError('human_algorithm 必须是 "bezier" 或 "windmouse"')
        self._human_algorithm = value
        return self

    def enable_trace(self, on_off=True):
        """启用 debug trace 记录。

        开启后，所有 BiDi 命令、事件、网络活动将记录到内存环形缓冲区，
        可通过 ``page.trace.summary()`` 或 ``page.trace.dump_json()`` 查看。

        Args:
            on_off: ``True`` 启用，``False`` 关闭。默认关闭。

        Returns:
            self

        说明：
            - 关闭状态下零开销（仅一次属性检查 ~10ns/命令）。
            - 缓冲区大小通过 ``Settings.trace_max_entries`` 控制（默认 1000）。
        """
        self._trace_enabled = bool(on_off)
        return self

    def enable_failure_snapshot(self, on_off=True):
        """启用自动化失败时的诊断快照。

        开启后，元素查找失败、页面加载超时、JS 异常等操作失败时，
        框架自动收集截图、DOM、URL 和最近网络请求记录，
        并附加到异常对象的 ``.diagnostics`` 属性上。

        Args:
            on_off: ``True`` 启用，``False`` 关闭。默认关闭。

        Returns:
            self

        说明：
            - 配合 ``set_snapshot_dir()`` 可自动保存快照文件到磁盘。
            - 收集过程每步独立容错，某步失败不影响其他信息的收集。
        """
        self._failure_snapshot_enabled = bool(on_off)
        return self

    def enable_marionette(self, on_off=True):
        """设置是否启用 Firefox Marionette 通道。

        Args:
            on_off: ``True`` 启用，``False`` 关闭。

        Returns:
            self

        说明：
            - 默认值为 ``True``，保持现有兼容行为。
            - 若某些 Firefox / 指纹浏览器在带 ``--marionette`` 时崩溃，
              可显式关闭：``FirefoxOptions().enable_marionette(False)``。
            - 关闭后不会在启动命令里加入 ``--marionette``，也不会向
              profile 写入 ``marionette.enabled=true``。
        """
        self._marionette_enabled = bool(on_off)
        return self

    def set_snapshot_dir(self, path):
        """设置诊断快照的保存目录。

        当自动化失败且 ``enable_failure_snapshot(True)`` 时，
        截图、DOM、trace 等文件将保存到此目录。

        Args:
            path: 目录路径，如 ``'./ruyipage_snapshots'``。
                传入 None 则不保存文件（仅附加到异常对象）。

        Returns:
            self
        """
        import os
        self._snapshot_dir = os.path.abspath(path) if path else None
        return self

    def _get_proxy_auth_credentials(self):
        """从 fpfile 中读取代理认证用户名密码。"""
        auth = self._read_httpauth_from_fpfile(self._fpfile)
        if not auth:
            return None

        username = auth.get("username")
        password = auth.get("password")
        if username is None and password is None:
            return None

        return {
            "username": username or "",
            "password": password or "",
        }

    def _read_httpauth_from_fpfile(self, path):
        """从 fpfile 中读取代理认证字段。"""
        if not path:
            return {}

        fpfile_path = os.path.abspath(path)
        if not os.path.exists(fpfile_path):
            raise FileNotFoundError("fpfile 不存在: {}".format(fpfile_path))

        result = {}
        pattern = re.compile(
            r"^\s*(httpauth\.(?:username|password))\s*[:=]\s*(.*?)\s*$"
        )
        with open(fpfile_path, "r", encoding="utf-8") as f:
            for raw_line in f:
                line = raw_line.strip()
                if not line or line.startswith("#") or line.startswith("//"):
                    continue
                match = pattern.match(line)
                if not match:
                    continue
                key, value = match.groups()
                if key == "httpauth.username":
                    result["username"] = value
                elif key == "httpauth.password":
                    result["password"] = value
        return result

    def set_window_size(self, width, height):
        """设置浏览器窗口大小

        通过 --width 和 --height 启动参数设置窗口初始大小。

        Args:
            width: 窗口宽度（像素）
            height: 窗口高度（像素）

        Returns:
            self
        """
        # 先移除已有的 width/height 参数
        self._arguments = [
            a
            for a in self._arguments
            if not a.startswith("--width=") and not a.startswith("--height=")
        ]
        self._arguments.append("--width={}".format(int(width)))
        self._arguments.append("--height={}".format(int(height)))
        return self

    def quick_start(
        self,
        *,
        browser_path=None,
        user_dir=None,
        proxy=None,
        close_on_exit=True,
        private=False,
        headless=False,
        xpath_picker=False,
        action_visual=False,
        human_algorithm="bezier",
        window_size=(1280, 800),
        timeout_base=10,
        timeout_page_load=30,
        timeout_script=30,
        trace=False,
        failure_snapshot=False,
        snapshot_dir=None,
        marionette=True,
    ):
        """小白友好的一键启动预设。

        该方法会一次性设置常用参数，便于快速开始。
        这是给”先跑起来再深入”的使用场景准备的快捷入口。

        Args:
            browser_path: Firefox 可执行文件路径。
                适用于 Firefox 安装在非默认目录时。
            user_dir: 用户目录 / profile 目录。
                适用于希望复用登录态、Cookie、扩展时。
            proxy: 代理地址。
                例如 ``"http://127.0.0.1:7890"`` 或
                ``"socks5://127.0.0.1:1080"``。
            close_on_exit: Python 程序退出时是否自动关闭浏览器。
                默认 ``True``，适合脚本跑完自动收尾。
            private: 是否启用 Firefox 私密浏览模式。
            headless: 是否无头
            xpath_picker: 是否启用页面 XPath 选择浮窗
            action_visual: 是否启用鼠标行为可视化调试模式
            human_algorithm: 默认拟人鼠标轨迹算法。
                可选 ``"bezier"`` 或 ``"windmouse"``。
            window_size: 窗口大小 (width, height)
            timeout_base: 基础超时
            timeout_page_load: 页面加载超时
            timeout_script: 脚本执行超时
            trace: 是否启用 debug trace 记录
            failure_snapshot: 是否启用失败自动诊断快照
            snapshot_dir: 诊断快照保存目录
            marionette: 是否启用 Firefox Marionette 通道

        Returns:
            self

        典型用法::

            opts = FirefoxOptions().set_port(9222).quick_start(
                browser_path=r”D:\\FirefoxPortable\\firefox.exe”,
                user_dir=r”D:\\my_firefox_userdir”,
                headless=False,
            )
            page = FirefoxPage(opts)
        """
        if browser_path:
            self.set_browser_path(browser_path)
        if user_dir:
            self.set_user_dir(user_dir)
        if proxy:
            self.set_proxy(proxy)
        self.close_on_exit(close_on_exit)
        self.private_mode(private)
        self.headless(headless)
        self.enable_xpath_picker(xpath_picker)
        self.enable_action_visual(action_visual)
        self.set_human_algorithm(human_algorithm)
        if window_size and len(window_size) == 2:
            self.set_window_size(window_size[0], window_size[1])
        self.set_timeouts(
            base=timeout_base,
            page_load=timeout_page_load,
            script=timeout_script,
        )
        self.enable_trace(trace)
        self.enable_failure_snapshot(failure_snapshot)
        self.enable_marionette(marionette)
        if snapshot_dir:
            self.set_snapshot_dir(snapshot_dir)
        return self

    def build_command(self):
        """构建 Firefox 启动命令行

        Returns:
            命令参数列表
        """
        cmd = [self._browser_path]

        cmd.append("--remote-debugging-port={}".format(self._port))
        cmd.append("--no-remote")
        # Marionette 不是 BiDi 主链路必需项；若某些环境带该参数会闪退，
        # 可通过 enable_marionette(False) 关闭，仅保留 remote-debugging-port。
        if self._marionette_enabled:
            cmd.append("--marionette")

        if self._profile_path:
            cmd.append("--profile")
            cmd.append(self._profile_path)

        if self._headless:
            cmd.append("--headless")

        if self._private_mode:
            cmd.append("-private")

        if self._fpfile:
            # Firefox 的自定义指纹参数要求使用 --fpfile=<path> 形式。
            cmd.append("--fpfile={}".format(self._fpfile))

        for arg in self._arguments:
            cmd.append(arg)

        return cmd

    def write_prefs_to_profile(self):
        """将首选项和代理设置写入 profile 的 user.js

        如果设置了 preferences 或 proxy，需要写入 user.js 文件
        """
        if not self._profile_path:
            return

        prefs = dict(self._preferences)

        # 自动化推荐设置
        prefs.setdefault("remote.prefs.recommended", True)
        prefs.setdefault("datareporting.policy.dataSubmissionEnabled", False)
        prefs.setdefault("toolkit.telemetry.reportingpolicy.firstRun", False)
        prefs.setdefault("browser.shell.checkDefaultBrowser", False)
        prefs.setdefault("browser.startup.homepage_override.mstone", "ignore")
        prefs.setdefault("browser.tabs.warnOnClose", False)
        prefs.setdefault("browser.warnOnQuit", False)
        # 仅在显式启用时才写入该 pref，避免某些环境因 Marionette 启动异常
        # 而在浏览器尚未建立 BiDi 连接前就崩溃/闪退。
        if self._marionette_enabled:
            prefs.setdefault("marionette.enabled", True)

        # 下载设置
        if self._download_path:
            prefs["browser.download.dir"] = self._download_path
            prefs["browser.download.folderList"] = 2
            prefs["browser.download.useDownloadDir"] = True

        # 代理设置
        if self._proxy:
            proxy = self._proxy
            if "://" in proxy:
                scheme, addr = proxy.split("://", 1)
            else:
                scheme, addr = "http", proxy

            host, port = addr.rsplit(":", 1) if ":" in addr else (addr, "8080")

            if scheme.startswith("socks"):
                prefs["network.proxy.type"] = 1
                prefs["network.proxy.socks"] = host
                prefs["network.proxy.socks_port"] = int(port)
                prefs["network.proxy.socks_version"] = 5 if "5" in scheme else 4
            else:
                prefs["network.proxy.type"] = 1
                prefs["network.proxy.http"] = host
                prefs["network.proxy.http_port"] = int(port)
                prefs["network.proxy.ssl"] = host
                prefs["network.proxy.ssl_port"] = int(port)
                prefs.setdefault("signon.autologin.proxy", True)
                prefs.setdefault("network.auth.subresource-http-auth-allow", 2)

        if not prefs:
            return

        os.makedirs(self._profile_path, exist_ok=True)
        user_js_path = os.path.join(self._profile_path, "user.js")

        lines = []
        for key, value in prefs.items():
            if isinstance(value, bool):
                val_str = "true" if value else "false"
            elif isinstance(value, int):
                val_str = str(value)
            elif isinstance(value, str):
                val_str = '"{}"'.format(value.replace("\\", "\\\\").replace('"', '\\"'))
            else:
                val_str = '"{}"'.format(value)
            lines.append('user_pref("{}", {});'.format(key, val_str))

        with open(user_js_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
