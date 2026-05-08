# -*- coding: utf-8 -*-
"""Interceptor - 网络请求拦截器

通过 BiDi network.addIntercept 实现请求拦截、修改、Mock。

核心概念
--------
BiDi 协议提供三个拦截阶段，分别用于不同场景：

- ``beforeRequestSent`` — 请求发出前。可修改 URL/Method/Headers/Body，
  也可直接 mock 或 fail。
- ``responseStarted`` — 服务器响应头到达后、响应体传输前。可修改
  status_code/Headers/reason_phrase。
- ``authRequired`` — HTTP 认证挑战（如 Basic Auth）。可提供凭证或取消。

两种使用模式
-----------
1. **回调模式** — 传入 handler 函数，每个被拦截的请求自动调用::

       def handler(req):
           if '/api' in req.url:
               req.mock('{"ok":true}', headers={"content-type": "application/json"})
           else:
               req.continue_request()

       page.intercept.start(handler, phases=['beforeRequestSent'])
       page.get('https://example.com')
       page.intercept.stop()

2. **队列模式** — handler=None，请求入队，用 wait() 手动取出::

       page.intercept.start(handler=None, phases=['beforeRequestSent'])
       # ... 触发请求 ...
       req = page.intercept.wait(timeout=5)
       req.continue_request()
       page.intercept.stop()

注意事项
--------
- 被拦截的请求 **必须** 调用 continue/fail/mock 之一，否则请求会永久挂起。
- 回调模式下若 handler 未处理请求，框架自动兜底（请求阶段 continue_request，
  响应阶段 continue_response）。
- 队列模式下必须手动处理每个 wait() 返回的请求。
"""

import base64
import time
import threading
import threading
from queue import Queue, Empty
from typing import Dict, Optional, List, Union

from .._bidi import network as bidi_network
from .._functions.settings import Settings
from .._functions.sleep import sleep as _sleep
from .._functions.queue_utils import queue_get as _queue_get
from .._bidi import session as bidi_session

import logging

logger = logging.getLogger("ruyipage")


def _normalize_headers(headers):
    """将 headers 统一为 BiDi 协议格式。

    BiDi 协议要求 headers 为
    ``[{"name": "X-Key", "value": {"type": "string", "value": "val"}}]``
    形式。本函数允许用户传入更简洁的 dict，自动完成转换。

    Args:
        headers: 请求头 / 响应头。支持三种输入：

            - ``None`` — 直接返回 None，表示不修改头。
            - ``dict`` — 简洁格式，如 ``{"X-Test": "yes", "User-Agent": "Bot"}``，
              自动转换为 BiDi list。
            - ``list`` — 已经是 BiDi 格式，原样返回。

    Returns:
        list 或 None: 转换后的 BiDi 格式列表。

    Examples::

        # dict → BiDi list
        _normalize_headers({"X-Test": "yes"})
        # => [{"name": "X-Test", "value": {"type": "string", "value": "yes"}}]

        # list 原样返回
        _normalize_headers([{"name": "X-Test", "value": {"type": "string", "value": "yes"}}])
        # => 原样返回

        # None 直接透传
        _normalize_headers(None)
        # => None
    """
    if headers is None:
        return None
    if isinstance(headers, dict):
        return [
            {"name": name, "value": {"type": "string", "value": str(value)}}
            for name, value in headers.items()
        ]
    return headers


class InterceptedRequest(object):
    """被拦截的请求对象。

    这是 network 拦截阶段暴露给用户的高层请求对象。

    你通常会在两类场景里接触它：

    1. ``req = page.intercept.wait()`` — 队列模式手动处理
    2. ``page.intercept.start(handler=my_handler)`` — 回调模式自动处理

    请求阶段属性（beforeRequestSent 阶段始终可用）：

    ==================  ====================================================
    属性                说明
    ==================  ====================================================
    ``request_id``      请求唯一 ID，可关联 DataCollector
    ``url``             请求 URL，如 ``https://api.example.com/data``
    ``method``          请求方法：``GET`` / ``POST`` / ``PUT`` 等
    ``headers``         请求头字典 ``{name: value}``
    ``body``            请求体字符串（POST/PUT），GET 请求为 ``None``
    ``phase``           拦截阶段标识
    ``handled``         是否已调用 continue/fail/mock
    ==================  ====================================================

    响应阶段属性（仅 responseStarted 阶段可用，其他阶段为 None）：

    =====================  =================================================
    属性                   说明
    =====================  =================================================
    ``response_status``    响应状态码，如 ``200`` / ``404`` / ``500``
    ``response_headers``   响应头字典 ``{name: value}``
    ``is_response_phase``  当前是否处于 responseStarted 阶段
    =====================  =================================================

    响应体属性（需要启用 ``collect_response=True``）：

    ==================  ====================================================
    属性                说明
    ==================  ====================================================
    ``response_body``   响应体字符串，自动等待 + 解码
    ==================  ====================================================

    操作方法：

    =========================  =============================================
    方法                       适用阶段
    =========================  =============================================
    ``continue_request()``     beforeRequestSent — 放行/修改请求
    ``fail()``                 beforeRequestSent — 中止请求
    ``mock()``                 beforeRequestSent — 返回模拟响应
    ``continue_response()``    responseStarted — 放行/修改响应
    ``continue_with_auth()``   authRequired — 处理 HTTP 认证
    =========================  =============================================

    Examples::

        # 回调模式：修改请求头（dict 简洁格式）
        def handler(req):
            req.continue_request(headers={"X-Token": "abc123"})

        # 回调模式：Mock 响应
        def handler(req):
            if '/api/user' in req.url:
                req.mock(
                    '{"name": "test"}',
                    status_code=200,
                    headers={"content-type": "application/json"},
                )
            else:
                req.continue_request()

        # 队列模式：读取 POST 请求体
        req = page.intercept.wait(timeout=5)
        print(req.method, req.url, req.body)
        req.continue_request()

        # 响应阶段：读取原始响应信息
        def handler(req):
            print(f"状态码: {req.response_status}")
            print(f"Content-Type: {req.response_headers.get('content-type')}")
            req.continue_response()

        # 一步读取响应体（需要 collect_response=True）
        page.intercept.start_requests(collect_response=True)
        req = page.intercept.wait(timeout=5)
        req.continue_request()
        print(req.response_body)  # 自动等待响应完成 + 解码
    """

    def __init__(self, params, driver, collector=None, response_collector=None):
        self._driver = driver
        self._params = params
        self._request = params.get("request", {})
        self._collector = collector
        self._response_collector = response_collector
        self._handled = False
        self._interceptor = None

        self._request_id: str = self._request.get("request", "")
        self._url: str = self._request.get("url", "")
        self._method: str = self._request.get("method", "")
        self._headers: Dict[str, str] = {
            h["name"]: h["value"].get("value", "")
            if isinstance(h.get("value"), dict)
            else str(h.get("value", ""))
            for h in self._request.get("headers", [])
        }
        self._body: Optional[str] = None
        self._phase: Optional[str] = (
            params.get("intercepts", [None])[0] if params.get("intercepts") else None
        )

        # 响应信息（responseStarted 阶段可用）
        self._response_raw = params.get("response", {})
        self._response_status: Optional[int] = (
            self._response_raw.get("status") if self._response_raw else None
        )
        self._response_headers: Optional[Dict[str, str]] = None
        if self._response_raw and self._response_raw.get("headers"):
            self._response_headers = {
                h["name"]: h["value"].get("value", "")
                if isinstance(h.get("value"), dict)
                else str(h.get("value", ""))
                for h in self._response_raw.get("headers", [])
            }

    # ------------------------------------------------------------------
    # 请求阶段属性
    # ------------------------------------------------------------------

    @property
    def request_id(self) -> str:
        """请求唯一 ID（字符串）。

        每个 HTTP 请求在其生命周期内拥有唯一 ID，可用于：

        - 与 ``DataCollector.get(request_id)`` 关联读取请求体 / 响应体
        - 在日志中追踪同一请求的完整生命周期

        Returns:
            str: 请求唯一标识，如 ``"12"``。

        Examples::

            req = page.intercept.wait(timeout=5)
            print(req.request_id)   # "12"
            req.continue_request()

            # 与 DataCollector 联动
            data = collector.get(req.request_id, data_type="response")
        """
        return self._request_id

    @property
    def url(self) -> str:
        """当前请求的完整 URL。

        Returns:
            str: 如 ``"https://api.example.com/data?page=1"``。

        Examples::

            def handler(req):
                if '/api/data' in req.url:
                    req.mock('{"ok":true}')
                else:
                    req.continue_request()
        """
        return self._url

    @property
    def method(self) -> str:
        """HTTP 请求方法。

        Returns:
            str: 大写的方法名，如 ``"GET"``、``"POST"``、``"PUT"``、``"DELETE"``。

        Examples::

            def handler(req):
                if req.method == "POST" and '/api' in req.url:
                    print(f"拦截到 POST 请求: {req.url}")
                req.continue_request()
        """
        return self._method

    @property
    def headers(self) -> Dict[str, str]:
        """请求头字典。

        已整理成 ``{name: value}`` 形式，key 保持原始大小写。

        Returns:
            dict: 如 ``{"Content-Type": "application/json", "Accept": "*/*"}``。

        Examples::

            def handler(req):
                ua = req.headers.get("User-Agent", "")
                print(f"User-Agent: {ua}")
                if "X-Auth-Token" in req.headers:
                    print("已携带认证头")
                req.continue_request()
        """
        return self._headers

    @property
    def phase(self) -> Optional[str]:
        """当前拦截阶段标识（intercept ID）。

        Returns:
            str 或 None: 拦截器 ID。可用于判断请求来自哪个拦截器。
        """
        return self._phase

    # ------------------------------------------------------------------
    # 响应阶段属性（仅 responseStarted 阶段可用）
    # ------------------------------------------------------------------

    @property
    def is_response_phase(self) -> bool:
        """当前是否处于 responseStarted 拦截阶段。

        Returns:
            bool: ``True`` 表示当前拦截发生在响应到达后，
            ``response_status`` 和 ``response_headers`` 可用。

        Examples::

            def handler(req):
                if req.is_response_phase:
                    print(f"响应到达，状态码: {req.response_status}")
                    req.continue_response()
                else:
                    req.continue_request()
        """
        return bool(self._response_raw)

    @property
    def response_status(self) -> Optional[int]:
        """响应状态码（仅 responseStarted 阶段可用）。

        在 ``beforeRequestSent`` 阶段调用时返回 ``None``，因为此时
        服务器尚未返回响应。

        Returns:
            int 或 None: 如 ``200``、``404``、``500``。

        Examples::

            # 在 responseStarted 阶段读取原始状态码
            def handler(req):
                if req.response_status == 500:
                    print(f"服务器错误: {req.url}")
                req.continue_response()

            page.intercept.start_responses(handler)
        """
        return self._response_status

    @property
    def response_headers(self) -> Optional[Dict[str, str]]:
        """响应头字典（仅 responseStarted 阶段可用）。

        已整理成 ``{name: value}`` 形式，key 保持原始大小写。
        在 ``beforeRequestSent`` 阶段调用时返回 ``None``。

        Returns:
            dict 或 None: 如 ``{"content-type": "application/json", "x-request-id": "abc"}``。

        Examples::

            def handler(req):
                if req.response_headers:
                    ct = req.response_headers.get("content-type", "")
                    print(f"响应类型: {ct}")
                req.continue_response()

            page.intercept.start_responses(handler)
        """
        return self._response_headers

    # ------------------------------------------------------------------
    # 请求体 / 响应体
    # ------------------------------------------------------------------

    def _extract_body_from_value(self, body) -> Optional[str]:
        """把 BiDi bytes value 结构转成字符串。"""
        return self._decode_body_value(body)

    def _decode_body_value(self, body) -> Optional[str]:
        if body is None:
            return None

        if isinstance(body, str):
            return body

        if not isinstance(body, dict):
            return str(body)

        body_type = body.get("type")
        value = body.get("value")
        if value is None:
            return None

        if body_type == "string":
            return str(value)

        if body_type == "base64":
            try:
                raw_bytes = base64.b64decode(value)
            except Exception:
                logger.debug("base64 解码失败，返回 None")
                return None
            # Firefox BiDi 返回的是已解压数据（浏览器 HTTP 层已处理
            # Content-Encoding: br/gzip/deflate），这里只需文本解码。
            try:
                return raw_bytes.decode("utf-8")
            except UnicodeDecodeError:
                return raw_bytes.decode("utf-8", errors="replace")

        return str(value)

    def _load_body(self) -> Optional[str]:
        body = self._extract_body_from_value(self._request.get("body"))
        if body is None:
            body = self._extract_body_from_value(self._params.get("body"))
        if body is not None:
            return body

        if not self._collector or not self.request_id:
            return None

        try:
            data = self._collector.get(self.request_id, data_type="request")
        except Exception as e:
            logger.debug("获取请求体失败: %s", e)
            return None

        decoded = self._decode_body_value(getattr(data, "bytes", None))
        if decoded is not None:
            return decoded

        decoded = self._decode_body_value(getattr(data, "base64", None))
        if decoded is not None:
            return decoded

        raw = getattr(data, "raw", None)
        if isinstance(raw, dict):
            for key in ("data", "body", "value"):
                decoded = self._decode_body_value(raw.get(key))
                if decoded is not None:
                    return decoded
            decoded = self._decode_body_value(raw)
            if decoded is not None:
                return decoded
        elif raw is not None:
            return str(raw)

        return None

    @property
    def body(self) -> Optional[str]:
        """请求体字符串。

        适用于 POST/PUT 等携带请求体的方法。GET 请求通常返回 ``None``。

        内部会依次尝试：
        1. 从 BiDi 事件 params 中直接读取
        2. 从 DataCollector 中按 request_id 读取

        Returns:
            str 或 None: 请求体内容，如 ``'{"keyword":"ruyi","page":2}'``。

        Examples::

            def handler(req):
                if req.method == "POST":
                    print(f"请求体: {req.body}")
                    # => '{"keyword":"ruyi","page":2}'
                req.continue_request()

            page.intercept.start_requests(handler)
        """
        if self._body is None:
            self._body = self._load_body()
        return self._body

    @property
    def response_body(self) -> Optional[str]:
        """响应体字符串（便捷属性）。

        委托给 ``get_response_body()``，使用默认超时
        （``Settings.response_body_timeout``，默认 10 秒）。

        如需自定义超时，请直接调用::

            body = req.get_response_body(timeout=30)

        **前提**：启动拦截时必须传入 ``collect_response=True``，否则始终返回 ``None``。

        Returns:
            str 或 None: 解码后的响应体字符串。若未启用 ``collect_response``、
            响应超时或获取失败，返回 ``None``。

        Examples::

            # 一步到位拿响应体（替代 listen + collector 三合一编排）
            page.intercept.start_requests(collect_response=True)
            page.run_js("fetch('/api/data').catch(()=>null); return true;", as_expr=False)
            req = page.intercept.wait(timeout=5)
            req.continue_request()

            import json
            data = json.loads(req.response_body)
            print(data)
            # {"status": "ok", "items": [...]}

            page.intercept.stop()
        """
        return self.get_response_body()

    def get_response_body(self, timeout=None) -> Optional[str]:
        """读取响应体，等待最多 ``timeout`` 秒。

        与 ``response_body`` 属性功能相同，但允许自定义超时时长。
        内部使用指数退避轮询 ``responseCompleted`` 事件对应的
        DataCollector 数据。

        **前提**：启动拦截时必须传入 ``collect_response=True``，
        否则始终返回 ``None``。

        Args:
            timeout: 最大等待秒数。``None`` 时使用
                ``Settings.response_body_timeout``（默认 10 秒）。

        Returns:
            str 或 None: 解码后的响应体字符串。

        Examples::

            # 默认超时
            body = req.get_response_body()

            # 为已知慢接口设置更长超时
            body = req.get_response_body(timeout=30)

            # 全局修改默认超时
            from ruyipage import Settings
            Settings.response_body_timeout = 20
        """
        if not self._response_collector or not self.request_id:
            return self._fallback_fetch_body()
        if timeout is None:
            timeout = Settings.response_body_timeout

        deadline = time.monotonic() + timeout
        interval = 0.1  # 起步短间隔，快速响应已就绪的数据
        last_error = None

        while True:
            try:
                data = self._response_collector.get(self.request_id, data_type="response")
                if data.has_data:
                    decoded = self._decode_body_value(data.base64)
                    if decoded is not None:
                        return decoded
                    decoded = self._decode_body_value(data.bytes)
                    if decoded is not None:
                        return decoded
                    # base64/bytes 都存在但 value 缺失；
                    # 可能是数据尚未完全落盘，继续轮询而不是立即放弃。
                last_error = None
            except Exception as exc:
                last_error = exc
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            _sleep(min(interval, remaining))
            interval = min(interval * 1.5, 0.5)  # 指数退避，上限 0.5s

        if last_error is not None:
            logger.debug("get_response_body 超时，最后异常: %s", last_error)

        # DataCollector 未采集到数据时（Firefox 147 对 Content-Encoding: br
        # 响应存在此问题），对 GET 请求用 JS fetch 降级。
        return self._fallback_fetch_body()

    def _fallback_fetch_body(self) -> Optional[str]:
        """当 DataCollector 拿不到数据时，对 GET 请求用页面 fetch 重放读取。"""
        if self._method != "GET" or not self._url:
            return None
        page = getattr(self._interceptor, '_owner', None) if self._interceptor else None
        if not page:
            return None
        try:
            result = page.run_js(
                'return fetch(arguments[0], {credentials: "include"})'
                '.then(r => r.text())',
                self._url,
                timeout=15,
            )
            if result and isinstance(result, str):
                return result
        except Exception as e:
            logger.debug("JS fetch 降级读取失败: %s", e)
        return None

    # ------------------------------------------------------------------
    # 状态
    # ------------------------------------------------------------------

    @property
    def handled(self):
        """当前请求是否已被处理。

        一旦调用过 ``continue_request()``、``continue_response()``、
        ``fail()``、``mock()`` 或 ``continue_with_auth()`` 之一，
        此属性变为 ``True``，后续重复调用会被静默忽略。

        Returns:
            bool: ``True`` 表示已处理。
        """
        return self._handled

    # ------------------------------------------------------------------
    # 操作方法
    # ------------------------------------------------------------------

    def continue_request(
        self,
        url: Optional[str] = None,
        method: Optional[str] = None,
        headers: Optional[Union[Dict[str, str], List[dict]]] = None,
        body=None,
    ):
        """放行请求，可选修改请求参数。

        **适用阶段**：``beforeRequestSent``

        不传任何参数等价于"直接放行、不做修改"。

        Args:
            url: 替换目标 URL。传 ``None`` 保持原 URL。

                示例::

                    # 把请求重定向到另一个接口
                    req.continue_request(url="https://backup-api.com/data")

            method: 替换请求方法。传 ``None`` 保持原方法。

                示例::

                    req.continue_request(method="POST")

            headers: 替换请求头。支持两种格式，传 ``None`` 保持原请求头。

                **dict 格式**（推荐，简洁）::

                    req.continue_request(headers={
                        "X-Token": "abc123",
                        "User-Agent": "RuyiPage/1.0",
                    })

                **BiDi list 格式**（原始，向后兼容）::

                    req.continue_request(headers=[
                        {"name": "X-Token", "value": {"type": "string", "value": "abc123"}},
                    ])

            body: 替换请求体。传 ``None`` 保持原请求体。

                示例::

                    req.continue_request(body={
                        "type": "string",
                        "value": '{"modified": true}',
                    })

        Examples::

            # 最简单：无修改放行
            req.continue_request()

            # 注入自定义请求头
            def handler(req):
                req.continue_request(headers={"X-Auth": "Bearer token123"})

            # 重定向请求到另一个 URL
            def handler(req):
                if '/old-api' in req.url:
                    req.continue_request(url=req.url.replace('/old-api', '/new-api'))
                else:
                    req.continue_request()
        """
        if self._handled:
            return
        self._handled = True
        bidi_network.continue_request(
            self._driver,
            self.request_id,
            url=url,
            method=method,
            headers=_normalize_headers(headers),
            body=body,
        )

    def fail(self):
        """中止请求，前端将收到网络错误。

        **适用阶段**：``beforeRequestSent``

        调用后浏览器直接丢弃该请求，前端 ``fetch()`` 会 reject，
        ``XMLHttpRequest`` 会触发 ``onerror``。

        Examples::

            # 阻止所有图片请求
            def handler(req):
                if req.url.endswith(('.png', '.jpg', '.gif')):
                    req.fail()
                else:
                    req.continue_request()

            page.intercept.start_requests(handler)

            # 测试前端错误处理
            def handler(req):
                if '/api/data' in req.url:
                    req.fail()
                else:
                    req.continue_request()

            page.intercept.start_requests(handler)
            result = page.run_js('''
                return fetch('/api/data')
                    .then(() => 'success')
                    .catch(e => 'blocked:' + e.name);
            ''', as_expr=False)
            print(result)  # "blocked:TypeError"
        """
        if self._handled:
            return
        self._handled = True
        bidi_network.fail_request(self._driver, self.request_id)

    def continue_with_auth(self, action="default", username=None, password=None):
        """处理 HTTP 认证挑战（Basic Auth / Digest Auth 等）。

        **适用阶段**：``authRequired``

        Args:
            action: 认证处理动作。

                - ``'default'`` — 交给浏览器默认处理（通常弹出登录对话框）。
                - ``'cancel'`` — 取消认证，服务器将返回 401。
                - ``'provideCredentials'`` — 使用指定用户名密码应答认证。

            username: 用户名。仅当 ``action='provideCredentials'`` 时使用。
            password: 密码。仅当 ``action='provideCredentials'`` 时使用。

        Examples::

            # 自动应答 Basic Auth
            def handler(req):
                req.continue_with_auth(
                    action="provideCredentials",
                    username="admin",
                    password="secret",
                )

            page.intercept.start(handler, phases=["authRequired"])
            page.get("https://httpbin.org/basic-auth/admin/secret")

            # 取消认证
            def handler(req):
                req.continue_with_auth(action="cancel")
        """
        if self._handled:
            return
        self._handled = True

        credentials = None
        if action == "provideCredentials":
            credentials = {
                "type": "password",
                "username": username or "",
                "password": password or "",
            }

        bidi_network.continue_with_auth(
            self._driver,
            self.request_id,
            action=action,
            credentials=credentials,
        )

    def continue_response(
        self,
        headers: Optional[Union[Dict[str, str], List[dict]]] = None,
        reason_phrase: Optional[str] = None,
        status_code: Optional[int] = None,
    ):
        """放行响应，可选修改响应状态码和头。

        **适用阶段**：``responseStarted``

        不传任何参数等价于"直接放行、不做修改"。

        Args:
            headers: 替换响应头。支持 dict 或 BiDi list 两种格式，传 ``None`` 保持原头。

                示例::

                    # dict 格式（推荐）
                    req.continue_response(headers={
                        "X-Custom": "injected",
                        "Cache-Control": "no-cache",
                    })

            reason_phrase: 替换 HTTP 状态文本。传 ``None`` 保持原值。

                常见值：``"OK"``、``"Not Found"``、``"Internal Server Error"``。

            status_code: 替换 HTTP 状态码。传 ``None`` 保持原值。

                常见值：``200``、``201``、``403``、``404``、``500``。

        Examples::

            # 把所有 API 响应改为 299
            def handler(req):
                if '/api' in req.url:
                    req.continue_response(status_code=299, reason_phrase="Modified")
                else:
                    req.continue_response()

            page.intercept.start_responses(handler)

            # 读取原始状态码 → 修改 → 放行
            def handler(req):
                print(f"原始状态码: {req.response_status}")
                if req.response_status == 403:
                    req.continue_response(status_code=200, reason_phrase="OK")
                else:
                    req.continue_response()
        """
        if self._handled:
            return
        self._handled = True
        bidi_network.continue_response(
            self._driver,
            self.request_id,
            headers=_normalize_headers(headers),
            reason_phrase=reason_phrase,
            status_code=status_code,
        )

    def mock(
        self,
        body="",
        status_code: int = 200,
        headers: Optional[Union[Dict[str, str], List[dict]]] = None,
        reason_phrase: str = "OK",
    ):
        """不访问真实后端，直接返回模拟响应。

        **适用阶段**：``beforeRequestSent``

        Args:
            body: 响应体内容。接受 ``str`` 或 ``bytes``。

                示例::

                    req.mock('{"status":"ok","data":[1,2,3]}')
                    req.mock(b'\\x89PNG...')  # 二进制内容

            status_code: HTTP 状态码。默认 ``200``。

                常见值：``200``、``201``、``404``、``500``。

            headers: 响应头。支持 dict 或 BiDi list 两种格式。
                传 ``None`` 时默认返回 ``content-type: text/plain``。

                示例::

                    # dict 格式（推荐）
                    req.mock('{"ok":true}', headers={
                        "content-type": "application/json",
                        "access-control-allow-origin": "*",
                    })

            reason_phrase: HTTP 状态文本。默认 ``"OK"``。

                常见值：``"OK"``、``"Created"``、``"Not Found"``。

        .. note::

            跨域场景下 mock 响应必须带 ``access-control-allow-origin`` 头，
            否则浏览器会拦截响应，前端 ``fetch()`` 将看到 ``NetworkError``。

        Examples::

            # Mock JSON 响应
            def handler(req):
                if '/api/user' in req.url:
                    req.mock(
                        '{"id":1,"name":"test"}',
                        headers={
                            "content-type": "application/json",
                            "access-control-allow-origin": "*",
                        },
                    )
                else:
                    req.continue_request()

            page.intercept.start_requests(handler)

            # Mock 404 响应
            def handler(req):
                req.mock("Not Found", status_code=404, reason_phrase="Not Found")

            # Mock 空响应（204 No Content）
            def handler(req):
                req.mock("", status_code=204, reason_phrase="No Content")
        """
        if self._handled:
            return
        self._handled = True
        import base64

        if isinstance(body, str):
            body_bytes = body.encode("utf-8")
        else:
            body_bytes = body
        encoded = base64.b64encode(body_bytes).decode("ascii")
        resp_headers = _normalize_headers(headers) or [
            {"name": "content-type", "value": {"type": "string", "value": "text/plain"}}
        ]
        bidi_network.provide_response(
            self._driver,
            self.request_id,
            body={"type": "base64", "value": encoded},
            headers=resp_headers,
            status_code=status_code,
            reason_phrase=reason_phrase,
        )

    def __repr__(self):
        return "<InterceptedRequest {} {}>".format(self.method, self.url[:60])


class Interceptor(object):
    """网络拦截管理器。

    通过 ``page.intercept`` 访问。提供对 HTTP 请求和响应的完全控制能力，
    包括修改、阻止、Mock 和认证处理。

    快速开始::

        # 回调模式：拦截并 Mock
        def handler(req):
            if '/api' in req.url:
                req.mock('{"mocked": true}', headers={"content-type": "application/json"})
            else:
                req.continue_request()

        page.intercept.start_requests(handler)
        page.get('https://example.com')
        page.intercept.stop()

    便捷方法::

        # 仅拦截请求阶段
        page.intercept.start_requests(handler)

        # 仅拦截响应阶段（默认启用 collect_response）
        page.intercept.start_responses(handler)

    队列模式::

        page.intercept.start_requests()
        # ... 触发网络请求 ...
        req = page.intercept.wait(timeout=5)
        print(req.method, req.url)
        req.continue_request()
        page.intercept.stop()

    采集模式（一步读取响应体）::

        page.intercept.start_requests(collect_response=True)
        # ... 触发网络请求 ...
        req = page.intercept.wait(timeout=5)
        req.continue_request()
        body = req.response_body  # 自动等待 + 解码
        page.intercept.stop()     # 自动清理内部 DataCollector
    """

    def __init__(self, owner):
        self._owner = owner
        self._active = False
        self._intercept_id = None
        self._subscription_id = None
        self._request_collector = None
        self._response_collector = None
        self._retired_collectors = []
        self._retired_subscriptions = []
        self._handler = None
        self._queue = Queue()

    def _cleanup_retired_resources(self):
        """清理上一轮 stop() 之后延迟保留的资源。

        设计原因：
            - ``page.intercept.wait()`` 返回的 ``InterceptedRequest`` 可能在
              ``page.intercept.stop()`` 之后才去读取 ``body`` / ``response_body``。
            - 如果 stop() 立刻 remove collector，外部已拿到的 req 就无法再读取。
            - 对 ``response_body`` 而言，除了 collector，相关 network 订阅也必须暂时
              保留，否则 ``responseCompleted`` 事件不会继续进入 collector。
            - 因此这里把这些资源的真正清理延后到下一次 start() 前统一执行。
        """
        pending_subscriptions = self._retired_subscriptions
        self._retired_subscriptions = []
        for subscription in pending_subscriptions:
            if not subscription:
                continue
            try:
                bidi_session.unsubscribe(
                    self._owner._driver._browser_driver,
                    subscription=subscription,
                )
            except Exception:
                pass

        pending_collectors = self._retired_collectors
        self._retired_collectors = []
        for collector in pending_collectors:
            if not collector:
                continue
            try:
                collector.remove()
            except Exception:
                pass

    @property
    def active(self):
        """当前是否正在拦截。

        Returns:
            bool: ``True`` 表示拦截已启动且未停止。
        """
        return self._active

    def start(self, handler=None, url_patterns=None, phases=None, collect_response=False):
        """开始拦截网络请求 / 响应。

        Args:
            handler: 回调函数，签名为 ``handler(req: InterceptedRequest)``。

                - 传入函数 — 每个被拦截的请求自动调用该函数。
                - 传入 ``None`` — 队列模式，请求入队，用 ``wait()`` 手动取出。

                示例::

                    def handler(req):
                        if '/api' in req.url:
                            req.mock('{"ok":true}')
                        else:
                            req.continue_request()

            url_patterns: URL 过滤模式列表。传 ``None`` 拦截所有请求。

                支持两种格式::

                    # 字符串匹配
                    [{"type": "string", "pattern": "api/data"}]

                    # 通配符匹配
                    [{"type": "pattern", "protocol": "https", "pathname": "/api/*"}]

            phases: 拦截阶段列表。默认 ``['beforeRequestSent']``。

                可选值：

                - ``'beforeRequestSent'`` — 请求发出前
                - ``'responseStarted'`` — 响应头到达后
                - ``'authRequired'`` — HTTP 认证挑战

                可同时传多个阶段::

                    phases=['beforeRequestSent', 'responseStarted']

            collect_response: 是否自动收集响应体。默认 ``False``。

                启用后，框架内部自动创建 ``DataCollector``，你可以通过
                ``InterceptedRequest.response_body`` 一步读取解码后的响应体，
                无需手动编排 ``page.network.add_data_collector()``。

                ``stop()`` 时自动清理。

                示例::

                    page.intercept.start_requests(collect_response=True)
                    req = page.intercept.wait(timeout=5)
                    req.continue_request()
                    print(req.response_body)  # 自动等待 + 解码
                    page.intercept.stop()

        Returns:
            Interceptor: 自身，便于链式调用。

        Examples::

            # 基础用法：拦截所有请求
            page.intercept.start(handler)
            page.get("https://example.com")
            page.intercept.stop()

            # 仅拦截特定 URL
            page.intercept.start(handler, url_patterns=[
                {"type": "string", "pattern": "/api/"},
            ])

            # 同时拦截请求和响应阶段
            page.intercept.start(handler, phases=["beforeRequestSent", "responseStarted"])

            # 队列模式 + 响应体采集
            page.intercept.start(collect_response=True)
        """
        if self._active:
            self.stop()

        self._cleanup_retired_resources()

        if phases is None:
            phases = ["beforeRequestSent"]

        self._handler = handler
        self._queue = Queue()
        self._request_collector = None
        self._response_collector = None

        if "beforeRequestSent" in phases:
            try:
                self._request_collector = self._owner.network.add_data_collector(
                    ["beforeRequestSent"],
                    data_types=["request"],
                )
            except Exception as e:
                logger.debug("启动请求数据收集器失败: %s", e)
                self._request_collector = None

        if collect_response:
            try:
                self._response_collector = self._owner.network.add_data_collector(
                    ["responseCompleted"],
                    data_types=["response"],
                )
            except Exception as e:
                logger.debug("启动响应数据收集器失败: %s", e)
                self._response_collector = None

        # 注册拦截
        result = bidi_network.add_intercept(
            self._owner._driver._browser_driver,
            phases=phases,
            url_patterns=url_patterns,
            contexts=[self._owner._context_id],
        )
        self._intercept_id = result.get("intercept")

        # 订阅事件
        events = []
        if "beforeRequestSent" in phases:
            events.append("network.beforeRequestSent")
        if "responseStarted" in phases:
            events.append("network.responseStarted")
        if "authRequired" in phases:
            events.append("network.authRequired")
        if collect_response and "network.responseCompleted" not in events:
            # DataCollector 采集响应体依赖 responseCompleted 阶段的数据落盘。
            # 仅创建 collector 还不够，当前 session 也必须订阅该事件，
            # 否则 req.response_body 会一直拿不到内容。
            events.append("network.responseCompleted")

        if events:
            sub = bidi_session.subscribe(
                self._owner._driver._browser_driver,
                events,
                contexts=[self._owner._context_id],
            )
            self._subscription_id = sub.get("subscription")

        drv = self._owner._driver
        if "beforeRequestSent" in phases:
            drv.set_global_callback("network.beforeRequestSent", self._on_intercept)
        if "responseStarted" in phases:
            drv.set_global_callback("network.responseStarted", self._on_response_intercept)
        if "authRequired" in phases:
            drv.set_global_callback("network.authRequired", self._on_auth)

        self._active = True
        return self

    def start_requests(self, handler=None, url_patterns=None, collect_response=False):
        """仅拦截 ``beforeRequestSent`` 阶段（请求发出前）。

        等价于 ``start(handler, phases=['beforeRequestSent'], ...)``。

        Args:
            handler: 回调函数，签名为 ``handler(req: InterceptedRequest)``。
                传 ``None`` 为队列模式。
            url_patterns: URL 过滤模式列表。传 ``None`` 拦截所有。
            collect_response: 是否自动收集响应体。默认 ``False``。
                启用后可通过 ``req.response_body`` 读取。

        Returns:
            Interceptor: 自身。

        Examples::

            # 回调模式
            def handler(req):
                print(f"{req.method} {req.url}")
                req.continue_request()
            page.intercept.start_requests(handler)

            # 队列模式 + 收集响应体
            page.intercept.start_requests(collect_response=True)
            req = page.intercept.wait(timeout=5)
            req.continue_request()
            print(req.response_body)
        """
        return self.start(
            handler=handler, url_patterns=url_patterns, phases=["beforeRequestSent"],
            collect_response=collect_response,
        )

    def start_responses(self, handler=None, url_patterns=None, collect_response=True):
        """仅拦截 ``responseStarted`` 阶段（响应头到达后）。

        等价于 ``start(handler, phases=['responseStarted'], ...)``。

        默认启用 ``collect_response=True``，因为拦截响应阶段的用户
        通常都需要读取响应体。

        Args:
            handler: 回调函数，签名为 ``handler(req: InterceptedRequest)``。
                传 ``None`` 为队列模式。
                在此阶段，``req.response_status`` 和 ``req.response_headers`` 可用。
            url_patterns: URL 过滤模式列表。传 ``None`` 拦截所有。
            collect_response: 是否自动收集响应体。默认 ``True``。

        Returns:
            Interceptor: 自身。

        Examples::

            # 读取并打印每个响应的状态码和 Content-Type
            def handler(req):
                print(f"[{req.response_status}] {req.url}")
                ct = req.response_headers.get("content-type", "")
                print(f"  Content-Type: {ct}")
                req.continue_response()

            page.intercept.start_responses(handler)
            page.get("https://example.com")
            page.intercept.stop()

            # 修改响应状态码
            def handler(req):
                req.continue_response(status_code=200, reason_phrase="OK")

            page.intercept.start_responses(handler)
        """
        return self.start(
            handler=handler, url_patterns=url_patterns, phases=["responseStarted"],
            collect_response=collect_response,
        )

    def stop(self):
        """停止拦截，清理所有资源。

        移除 BiDi 拦截注册、取消事件订阅，并延迟清理内部 DataCollector。
        调用后 ``active`` 属性变为 ``False``。

        说明：
            - 为了支持 ``req = page.intercept.wait()`` 后，先 ``req.continue_*()``、
              再 ``page.intercept.stop()``、最后读取 ``req.response_body`` 的用法，
              已返回给用户的 collector 不会在 stop() 当场 remove。
            - 这些 collector 会在下一次 ``start()`` 前统一清理。

        可安全重复调用（幂等）。

        Returns:
            Interceptor: 自身。

        Examples::

            page.intercept.start_requests(handler)
            page.get("https://example.com")
            page.intercept.stop()

            # 在 finally 块中安全调用
            try:
                page.intercept.start_requests(handler)
                # ...
            finally:
                page.intercept.stop()  # 安全，即使未启动也不会报错
        """
        if not self._active:
            return
        self._active = False

        if self._intercept_id:
            try:
                bidi_network.remove_intercept(
                    self._owner._driver._browser_driver, self._intercept_id
                )
            except Exception:
                pass
            self._intercept_id = None

        if self._subscription_id:
            if self._response_collector:
                self._retired_subscriptions.append(self._subscription_id)
            else:
                try:
                    bidi_session.unsubscribe(
                        self._owner._driver._browser_driver,
                        subscription=self._subscription_id,
                    )
                except Exception:
                    pass
            self._subscription_id = None

        if self._request_collector:
            self._retired_collectors.append(self._request_collector)
            self._request_collector = None

        if self._response_collector:
            self._retired_collectors.append(self._response_collector)
            self._response_collector = None

        drv = self._owner._driver
        for ev in (
            "network.beforeRequestSent",
            "network.responseStarted",
            "network.authRequired",
        ):
            drv.remove_global_callback(ev)

        return self

    def wait(self, timeout=10):
        """等待下一个被拦截的请求（队列模式专用）。

        仅在 ``handler=None``（队列模式）时有效。阻塞当前线程直到
        有被拦截的请求入队，或超时返回 ``None``。

        Args:
            timeout: 最大等待时间（秒）。默认 ``10``。

        Returns:
            InterceptedRequest 或 None: 被拦截的请求对象。超时返回 ``None``。

        .. warning::

            队列模式下 **必须** 对每个 ``wait()`` 返回的请求调用
            ``continue_request()`` / ``continue_response()`` / ``fail()`` 之一，
            否则该请求会永久挂起。

        Examples::

            page.intercept.start(handler=None, phases=["beforeRequestSent"])
            page.run_js("fetch('/api/data').catch(()=>null); return true;", as_expr=False)

            req = page.intercept.wait(timeout=5)
            if req:
                print(f"捕获: {req.method} {req.url}")
                req.continue_request()
            else:
                print("超时未捕获到请求")

            page.intercept.stop()
        """
        try:
            return _queue_get(self._queue, timeout=timeout)
        except Empty:
            return None

    def _on_intercept(self, params):
        if not self._active:
            return
        if not params.get("intercepts") and not params.get("isBlocked"):
            return
        req = InterceptedRequest(
            params,
            self._owner._driver._browser_driver,
            collector=self._request_collector,
            response_collector=self._response_collector,
        )
        req._interceptor = self

        # trace: 记录请求拦截
        _tracer = getattr(self._owner._driver._browser_driver, '_tracer', None)
        if _tracer and _tracer.enabled:
            _tracer.record("net_intercept", "beforeRequestSent",
                           {"url": (req.url or "")[:200], "method": req.method},
                           context_id=getattr(self._owner, '_context_id', None))

        if self._handler:
            try:
                self._handler(req)
            except Exception as e:
                logger.warning("拦截回调异常: %s", e)
                if _tracer and _tracer.enabled:
                    _tracer.record("error", "intercept_handler",
                                   {"error": str(e)[:200], "url": (req.url or "")[:200]},
                                   context_id=getattr(self._owner, '_context_id', None),
                                   status="error")
            if not req.handled:
                req.continue_request()
        else:
            self._queue.put(req)

    def _on_response_intercept(self, params):
        if not self._active:
            return
        if not params.get("intercepts") and not params.get("isBlocked"):
            return
        req = InterceptedRequest(
            params,
            self._owner._driver._browser_driver,
            collector=self._request_collector,
            response_collector=self._response_collector,
        )
        req._interceptor = self

        # trace: 记录响应拦截
        _tracer = getattr(self._owner._driver._browser_driver, '_tracer', None)
        if _tracer and _tracer.enabled:
            _tracer.record("net_intercept", "responseStarted",
                           {"url": (req.url or "")[:200], "method": req.method},
                           context_id=getattr(self._owner, '_context_id', None))

        if self._handler:
            # responseStarted 场景里，用户回调很容易在 continue_response() 后
            # 继续同步读取 req.response_body。该属性会等待 responseCompleted，
            # 如果仍在当前事件消费线程内执行，就会把后续 network.responseCompleted
            # 事件自己堵住，表现为页面后续资源加载被串行卡住。
            #
            # 这里改为独立短线程执行用户回调：
            # 1. 不阻塞 BrowserBiDiDriver 的事件消费线程
            # 2. 允许 responseCompleted 事件及时到达 DataCollector
            # 3. 仍保留"未处理则自动 continue_response()"的兜底语义

            def _run_handler():
                try:
                    self._handler(req)
                except Exception as e:
                    logger.warning("响应拦截回调异常: %s", e)
                    if _tracer and _tracer.enabled:
                        _tracer.record("error", "response_intercept_handler",
                                       {"error": str(e)[:200], "url": (req.url or "")[:200]},
                                       context_id=getattr(self._owner, '_context_id', None),
                                       status="error")
                if not req.handled:
                    req.continue_response()

            threading.Thread(
                target=_run_handler,
                name="ruyipage-response-intercept",
                daemon=True,
            ).start()
        else:
            self._queue.put(req)

    def _on_auth(self, params):
        if not self._active:
            return
        if not params.get("intercepts") and not params.get("isBlocked"):
            return
        req = InterceptedRequest(
            params,
            self._owner._driver._browser_driver,
            collector=self._request_collector,
            response_collector=self._response_collector,
        )
        req._interceptor = self

        # trace: 记录认证拦截
        _tracer = getattr(self._owner._driver._browser_driver, '_tracer', None)
        if _tracer and _tracer.enabled:
            _tracer.record("net_intercept", "authRequired",
                           {"url": (req.url or "")[:200]},
                           context_id=getattr(self._owner, '_context_id', None))

        if self._handler:
            try:
                self._handler(req)
            except Exception as e:
                logger.warning("认证拦截回调异常: %s", e)
                if _tracer and _tracer.enabled:
                    _tracer.record("error", "auth_intercept_handler",
                                   {"error": str(e)[:200]},
                                   context_id=getattr(self._owner, '_context_id', None),
                                   status="error")
            if not req.handled:
                bidi_network.continue_with_auth(
                    self._owner._driver._browser_driver,
                    req.request_id,
                    action="default",
                )
        else:
            self._queue.put(req)
