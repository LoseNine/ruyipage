# -*- coding: utf-8 -*-
"""测试体系专用本地 HTTP server。"""

import base64
import json
import time as _time
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

from ruyipage._functions.tools import find_free_port


class TestServer(object):
    """用于 tests/ 的轻量本地 HTTP server。"""

    __test__ = False

    def __init__(self, host="127.0.0.1", port=None):
        self.host = host
        self.port = port or find_free_port(9400, 9800)
        self._server = None
        self._thread = None

    def start(self):
        if self._server:
            return self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format, *args):
                return

            def _cors_headers(self):
                return [
                    ("Access-Control-Allow-Origin", "*"),
                    ("Access-Control-Allow-Methods", "GET, POST, OPTIONS"),
                    (
                        "Access-Control-Allow-Headers",
                        "Content-Type, X-Ruyi-Demo, User-Agent",
                    ),
                ]

            def _write_text(self, status, body, headers=None):
                body_bytes = body.encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Content-Length", str(len(body_bytes)))
                if headers:
                    for name, value in headers:
                        self.send_header(name, value)
                self.end_headers()
                self.wfile.write(body_bytes)

            def _write_json(self, status, payload, headers=None):
                body = json.dumps(payload, ensure_ascii=False)
                body_bytes = body.encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body_bytes)))
                if headers:
                    for name, value in headers:
                        self.send_header(name, value)
                self.end_headers()
                self.wfile.write(body_bytes)

            def do_GET(self):
                parsed = urlparse(self.path)
                path = parsed.path

                if path == "/set-cookie":
                    self._write_text(
                        200,
                        "cookie set ok",
                        headers=[
                            ("Set-Cookie", "server_cookie=server_value; Path=/"),
                            ("Set-Cookie", "session_id=abc123; Path=/; HttpOnly"),
                        ],
                    )
                    return

                if path == "/get-cookie":
                    cookie_header = self.headers.get("Cookie", "")
                    self._write_text(200, cookie_header or "")
                    return

                if path == "/api/data":
                    self._write_json(
                        200,
                        {"status": "ok", "data": {"message": "来自测试服务器的数据"}},
                        headers=self._cors_headers(),
                    )
                    return

                if path == "/api/headers":
                    headers = {key: value for key, value in self.headers.items()}
                    self._write_json(200, headers, headers=self._cors_headers())
                    return

                if path == "/api/collector":
                    headers = {key: value for key, value in self.headers.items()}
                    self._write_json(
                        200,
                        {
                            "status": "ok",
                            "source": "collector",
                            "message": "stable response body for data collector",
                            "headers": headers,
                        },
                        headers=self._cors_headers(),
                    )
                    return

                if path == "/api/slow":
                    query = parse_qs(parsed.query)
                    delay = float(query.get("delay", ["2"])[0])
                    payload = json.dumps(
                        {"status": "ok", "delayed": delay}, ensure_ascii=False
                    ).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json; charset=utf-8")
                    self.send_header("Content-Length", str(len(payload)))
                    for name, value in self._cors_headers():
                        self.send_header(name, value)
                    self.end_headers()
                    self.wfile.flush()
                    # 先发送 header 再延迟发送 body，
                    # 模拟响应体传输缓慢的场景
                    _time.sleep(delay)
                    self.wfile.write(payload)
                    self.wfile.flush()
                    return

                if path == "/api/error":
                    self._write_json(
                        500,
                        {"status": "error", "message": "server error"},
                        headers=self._cors_headers(),
                    )
                    return

                if path == "/api/auth":
                    auth_header = self.headers.get("Authorization", "")
                    expected = "Basic " + base64.b64encode(b"user:pass").decode("ascii")
                    if auth_header != expected:
                        self.send_response(401)
                        self.send_header("WWW-Authenticate", 'Basic realm="RuyiTest"')
                        self.send_header("Content-Length", "0")
                        self.end_headers()
                        return

                    self._write_json(
                        200,
                        {"status": "ok", "auth": True, "user": "user"},
                        headers=self._cors_headers(),
                    )
                    return

                if path == "/nav/basic":
                    body = """<!DOCTYPE html><html><head><meta charset='utf-8'><title>Nav Basic</title></head><body><h1 id='nav-basic'>Nav Basic</h1></body></html>"""
                    body_bytes = body.encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Content-Length", str(len(body_bytes)))
                    self.end_headers()
                    self.wfile.write(body_bytes)
                    return

                if path == "/nav/history":
                    body = """<!DOCTYPE html><html><head><meta charset='utf-8'><title>Nav History</title></head><body><h1 id='nav-history'>Nav History</h1></body></html>"""
                    body_bytes = body.encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Content-Length", str(len(body_bytes)))
                    self.end_headers()
                    self.wfile.write(body_bytes)
                    return

                if path == "/storage/page":
                    body = """<!DOCTYPE html><html><head><meta charset='utf-8'><title>Storage Page</title></head><body><h1 id='storage-page'>Storage Page</h1></body></html>"""
                    body_bytes = body.encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Content-Length", str(len(body_bytes)))
                    self.end_headers()
                    self.wfile.write(body_bytes)
                    return

                self._write_text(404, "not found")

            def do_OPTIONS(self):
                self.send_response(204)
                for name, value in self._cors_headers():
                    self.send_header(name, value)
                self.send_header("Content-Length", "0")
                self.end_headers()

            def do_POST(self):
                parsed = urlparse(self.path)
                path = parsed.path
                length = int(self.headers.get("Content-Length", "0") or "0")
                raw_body = self.rfile.read(length) if length > 0 else b""
                body_text = raw_body.decode("utf-8", errors="replace")

                if path == "/api/echo":
                    self._write_json(
                        200,
                        {
                            "status": "ok",
                            "method": "POST",
                            "body": body_text,
                            "content_type": self.headers.get("Content-Type", ""),
                        },
                        headers=self._cors_headers(),
                    )
                    return

                self._write_text(404, "not found", headers=self._cors_headers())

        self._server = ThreadingHTTPServer((self.host, self.port), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self

    def stop(self):
        if not self._server:
            return
        self._server.shutdown()
        self._server.server_close()
        self._server = None
        self._thread = None

    def get_url(self, path="/"):
        if not path.startswith("/"):
            path = "/" + path
        return "http://{}:{}{}".format(self.host, self.port, path)
