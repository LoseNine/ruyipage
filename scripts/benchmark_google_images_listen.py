# -*- coding: utf-8 -*-
"""Benchmark Google Images listen + scroll performance.

This script is intentionally outside the normal test suite. It is a diagnostic
tool for separating Firefox launch cost, BiDi command latency, scroll cost,
network listener wait time, and response body read time.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import platform
import queue
import shutil
import socket
import statistics
import sys
import tempfile
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from ruyipage import FirefoxOptions, FirefoxPage  # noqa: E402


DEFAULT_GOOGLE_IMAGES_URL = (
    "https://www.google.com/search?"
    + urlencode({"q": "ruyi", "tbm": "isch", "udm": "2", "hl": "zh-cn"})
)
GOOGLE_TARGET_PREFIX = "https://www.google.com/search?vet="


def now_ms() -> float:
    return time.perf_counter() * 1000.0


def elapsed_ms(start: float) -> float:
    return round(now_ms() - start, 3)


def timed_call(func, *args, **kwargs):
    start = now_ms()
    value = func(*args, **kwargs)
    return value, elapsed_ms(start)


def percentile(values, pct):
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return round(ordered[0], 3)
    rank = (len(ordered) - 1) * (pct / 100.0)
    lower = int(rank)
    upper = min(lower + 1, len(ordered) - 1)
    weight = rank - lower
    return round(ordered[lower] * (1.0 - weight) + ordered[upper] * weight, 3)


def summarize(rows, field):
    values = [float(row[field]) for row in rows if row.get(field) not in ("", None)]
    if not values:
        return {}
    return {
        "count": len(values),
        "min": round(min(values), 3),
        "p50": percentile(values, 50),
        "p90": percentile(values, 90),
        "p95": percentile(values, 95),
        "max": round(max(values), 3),
        "mean": round(statistics.fmean(values), 3),
    }


def find_free_port(start=9801, end=9999):
    for port in range(start, end):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            try:
                sock.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    raise RuntimeError("No free local port found")


def get_process_snapshot():
    """Return lightweight system metrics without adding required dependencies."""
    snap = {
        "platform": platform.platform(),
        "cpu_count": os.cpu_count(),
        "process_count": None,
        "firefox_process_count": None,
    }
    if sys.platform != "win32":
        return snap

    try:
        import subprocess

        cmd = [
            "powershell",
            "-NoProfile",
            "-Command",
            (
                "$all=Get-Process; "
                "$ff=$all | Where-Object { $_.ProcessName -like '*firefox*' }; "
                "[pscustomobject]@{"
                "process_count=$all.Count;"
                "firefox_process_count=$ff.Count;"
                "firefox_ws_mb=[math]::Round((($ff | Measure-Object WorkingSet64 -Sum).Sum/1MB),1)"
                "} | ConvertTo-Json -Compress"
            ),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
        if result.returncode == 0 and result.stdout.strip():
            snap.update(json.loads(result.stdout))
    except Exception:
        pass
    return snap


class InfiniteScrollServer:
    def __init__(self, host="127.0.0.1", port=None, delay_ms=50, payload_kb=16):
        self.host = host
        self.port = port or find_free_port()
        self.delay_ms = int(delay_ms)
        self.payload_kb = int(payload_kb)
        self.api_hits = 0
        self._lock = threading.Lock()
        self._server = None
        self._thread = None

    @property
    def base_url(self):
        return "http://{}:{}".format(self.host, self.port)

    @property
    def page_url(self):
        return self.base_url + "/fixture"

    @property
    def target_prefix(self):
        return self.base_url + "/api/items"

    def start(self):
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, fmt, *args):
                return

            def _headers(self, status=200, content_type="text/html; charset=utf-8"):
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Cache-Control", "no-store")
                self.send_header("Pragma", "no-cache")
                self.send_header("Expires", "0")
                self.end_headers()

            def do_GET(self):
                parsed = urlparse(self.path)
                if parsed.path == "/fixture":
                    self._headers()
                    self.wfile.write(owner.fixture_html().encode("utf-8"))
                    return
                if parsed.path == "/api/items":
                    time.sleep(owner.delay_ms / 1000.0)
                    qs = parse_qs(parsed.query)
                    batch = qs.get("batch", ["0"])[0]
                    with owner._lock:
                        owner.api_hits += 1
                    padding = "x" * max(0, owner.payload_kb * 1024)
                    body = {
                        "batch": batch,
                        "items": [
                            {"title": "item-{}-{}".format(batch, i), "site": "example.com"}
                            for i in range(8)
                        ],
                        "padding": padding,
                    }
                    self._headers(content_type="application/json; charset=utf-8")
                    self.wfile.write(json.dumps(body).encode("utf-8"))
                    return
                self._headers(404, "text/plain; charset=utf-8")
                self.wfile.write(b"not found")

        self._server = ThreadingHTTPServer((self.host, self.port), Handler)
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            name="ruyipage-benchmark-fixture",
            daemon=True,
        )
        self._thread.start()
        return self

    def stop(self):
        if self._server:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
        if self._thread:
            self._thread.join(timeout=2)
            self._thread = None

    def fixture_html(self):
        return """<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>ruyipage infinite scroll benchmark</title>
  <style>
    body { margin: 0; font-family: sans-serif; }
    #items { padding: 24px; }
    .item { height: 180px; border-bottom: 1px solid #ccc; }
    #sentinel { height: 600px; }
  </style>
</head>
<body>
  <div id="items"></div>
  <div id="sentinel"></div>
  <script>
    let batch = 0;
    let loading = false;
    const items = document.getElementById("items");
    function appendRows(prefix) {
      for (let i = 0; i < 10; i++) {
        const div = document.createElement("div");
        div.className = "item";
        div.textContent = prefix + " row " + i;
        items.appendChild(div);
      }
    }
    appendRows("initial");
    async function loadMore() {
      if (loading) return;
      loading = true;
      const started = performance.now();
      const url = "/api/items?run_id=fixture&batch=" + (++batch) + "&nonce=" + Date.now();
      try {
        const response = await fetch(url, {cache: "no-store"});
        await response.text();
        appendRows("batch " + batch);
        window.__lastFetchMs = performance.now() - started;
      } finally {
        loading = false;
      }
    }
    window.addEventListener("scroll", () => {
      if (window.innerHeight + window.scrollY >= document.documentElement.scrollHeight - 1400) {
        loadMore();
      }
    }, {passive: true});
  </script>
</body>
</html>"""


def make_options(args, worker_id):
    opts = FirefoxOptions()
    opts.set_auto_port(9600 + worker_id * 10)
    opts.headless(args.headless)
    opts.set_window_size(args.width, args.height)
    opts.enable_action_visual(False)
    opts.set_load_mode("none")
    opts.set_timeouts(base=args.bidi_timeout, page_load=args.page_timeout, script=args.bidi_timeout)
    if args.browser_path:
        opts.set_browser_path(args.browser_path)
    if args.profile_root:
        profile = Path(args.profile_root) / "worker-{}".format(worker_id)
        profile.mkdir(parents=True, exist_ok=True)
        opts.set_user_dir(str(profile))
    if args.disable_gpu:
        opts.set_pref("layers.acceleration.disabled", True)
        opts.set_pref("gfx.webrender.force-disabled", True)
    return opts


def run_bidi_wheel(page, delta_y):
    return page._driver._browser_driver.run(
        "input.performActions",
        {
            "context": page._context_id,
            "actions": [
                {
                    "type": "wheel",
                    "id": "wheel0",
                    "actions": [
                        {
                            "type": "scroll",
                            "x": 400,
                            "y": 300,
                            "deltaX": 0,
                            "deltaY": int(delta_y),
                        }
                    ],
                }
            ],
        },
    )


def js_scroll_script(delta_y):
    return """
(() => {
  const before = window.scrollY;
  const t0 = performance.now();
  window.scrollBy(0, %d);
  const innerMs = performance.now() - t0;
  return {
    inner_ms: innerMs,
    before_y: before,
    after_y: window.scrollY,
    scroll_height: document.documentElement.scrollHeight,
    ready_state: document.readyState
  };
})()
""" % int(delta_y)


def trigger_scroll(page, mode, delta_y):
    if mode == "js":
        result, ms = timed_call(page.run_js, js_scroll_script(delta_y), as_expr=True)
        return ms, result if isinstance(result, dict) else {}
    if mode == "bidi-wheel":
        _, ms = timed_call(run_bidi_wheel, page, delta_y)
        return ms, {}
    if mode == "actions":
        _, ms = timed_call(page.actions.scroll(0, int(delta_y)).perform)
        return ms, {}
    raise ValueError("Unsupported scroll mode: {}".format(mode))


def is_target_packet(packet, target_prefix):
    if not packet:
        return False
    if packet.is_failed:
        return False
    if packet.method and packet.method.upper() != "GET":
        return False
    if packet.status and int(packet.status) != 200:
        return False
    return bool(packet.url and packet.url.startswith(target_prefix))


def wait_for_target_packet(page, target_prefix, timeout, drain_limit=8):
    deadline = time.perf_counter() + timeout
    non_target_count = 0
    first_packet = None

    while time.perf_counter() < deadline and non_target_count < drain_limit:
        remaining = max(0.01, deadline - time.perf_counter())
        packet = page.listen.wait(timeout=min(remaining, 0.5))
        if not packet:
            continue
        if first_packet is None:
            first_packet = packet
        if is_target_packet(packet, target_prefix):
            return packet, non_target_count
        non_target_count += 1

    return None, non_target_count if first_packet else 0


def worker_run(args, worker_count, worker_id, start_barrier, result_queue, mode, url, target_prefix):
    page = None
    launch_ms = None
    profile_tmp = None
    rows = []
    error = None
    try:
        if not args.profile_root:
            profile_root = tempfile.mkdtemp(prefix="ruyipage_bench_{}_".format(worker_id))
            profile_tmp = profile_root
            args_for_worker = argparse.Namespace(**vars(args))
            args_for_worker.profile_root = profile_root
        else:
            args_for_worker = args

        opts = make_options(args_for_worker, worker_id)
        page, launch_ms = timed_call(FirefoxPage, opts)

        try:
            page.set_cache_behavior("bypass")
        except Exception:
            pass

        _, nav_ms = timed_call(page.get, url, "none", args.page_timeout)
        if args.warmup_sleep:
            time.sleep(args.warmup_sleep)

        for _ in range(args.warmup_evals):
            try:
                page.run_js("1", timeout=args.bidi_timeout)
            except Exception:
                pass

        listen_start_ms = ""
        if args.listen:
            listen_targets = target_prefix if args.listen_targets == "target" else True
            _, listen_start_ms = timed_call(
                page.listen.start,
                listen_targets,
                False,
                "GET",
                args.collect_response,
            )

        start_barrier.wait(timeout=max(30.0, args.page_timeout + args.bidi_timeout))

        for round_id in range(args.rounds):
            row = {
                "mode": mode,
                "source": args.source,
                "workers": worker_count,
                "worker_id": worker_id,
                "round": round_id,
                "launch_ms": launch_ms if round_id == 0 else "",
                "navigate_ms": nav_ms if round_id == 0 else "",
                "listen_start_ms": listen_start_ms if round_id == 0 else "",
                "noop_run_js_ms": "",
                "scroll_ms": "",
                "js_inner_scroll_ms": "",
                "wait_ms": "",
                "body_read_ms": "",
                "target_hit": 0,
                "non_target_packets": 0,
                "body_bytes": "",
                "status": "",
                "url": "",
                "error": "",
            }
            try:
                _, noop_ms = timed_call(page.run_js, "1", timeout=args.bidi_timeout)
                row["noop_run_js_ms"] = noop_ms

                scroll_ms, js_result = trigger_scroll(page, mode, args.delta_y)
                row["scroll_ms"] = scroll_ms
                if js_result.get("inner_ms") is not None:
                    row["js_inner_scroll_ms"] = round(float(js_result["inner_ms"]), 3)

                if args.listen:
                    start = now_ms()
                    packet, non_target_count = wait_for_target_packet(
                        page,
                        target_prefix,
                        args.wait_timeout,
                        args.drain_limit,
                    )
                    row["wait_ms"] = elapsed_ms(start)
                    row["non_target_packets"] = non_target_count

                    if packet:
                        row["target_hit"] = 1
                        row["status"] = packet.status
                        row["url"] = packet.url
                        if args.read_body == "matched-only":
                            start = now_ms()
                            text = packet.text
                            row["body_read_ms"] = elapsed_ms(start)
                            row["body_bytes"] = len(text.encode("utf-8")) if text else 0

                if args.post_scroll_sleep:
                    time.sleep(args.post_scroll_sleep)
            except Exception as exc:
                row["error"] = "{}: {}".format(type(exc).__name__, exc)
            rows.append(row)

        stop_ms = ""
        if args.listen:
            try:
                _, stop_ms = timed_call(page.listen.stop)
            except Exception:
                stop_ms = "error"
        for row in rows:
            row["listen_stop_ms"] = stop_ms if row["round"] == args.rounds - 1 else ""

    except Exception:
        error = traceback.format_exc()
    finally:
        if page is not None:
            try:
                page.quit()
            except Exception:
                pass
        if profile_tmp is not None:
            shutil.rmtree(profile_tmp, ignore_errors=True)

    result_queue.put({"worker_id": worker_id, "rows": rows, "error": error})


def write_outputs(rows, output_prefix):
    out = Path(output_prefix)
    out.parent.mkdir(parents=True, exist_ok=True)
    csv_path = out.with_suffix(".csv")
    json_path = out.with_suffix(".json")

    fieldnames = [
        "mode",
        "source",
        "workers",
        "worker_id",
        "round",
        "launch_ms",
        "navigate_ms",
        "listen_start_ms",
        "noop_run_js_ms",
        "scroll_ms",
        "js_inner_scroll_ms",
        "wait_ms",
        "body_read_ms",
        "listen_stop_ms",
        "target_hit",
        "non_target_packets",
        "body_bytes",
        "status",
        "url",
        "error",
    ]
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    summary = {
        "row_count": len(rows),
        "system": get_process_snapshot(),
        "fields": {
            name: summarize(rows, name)
            for name in [
                "launch_ms",
                "navigate_ms",
                "listen_start_ms",
                "noop_run_js_ms",
                "scroll_ms",
                "js_inner_scroll_ms",
                "wait_ms",
                "body_read_ms",
                "listen_stop_ms",
            ]
        },
        "target_hits": sum(int(row.get("target_hit") or 0) for row in rows),
        "errors": [row for row in rows if row.get("error")],
    }
    with json_path.open("w", encoding="utf-8") as f:
        json.dump({"summary": summary, "rows": rows}, f, ensure_ascii=False, indent=2)
    return csv_path, json_path, summary


def parse_workers(value):
    if "," in value:
        return [int(v.strip()) for v in value.split(",") if v.strip()]
    return [int(value)]


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", choices=["google", "local"], default="google")
    parser.add_argument("--url", default=DEFAULT_GOOGLE_IMAGES_URL)
    parser.add_argument("--target-prefix", default=GOOGLE_TARGET_PREFIX)
    parser.add_argument("--workers", default="1,2,4,8")
    parser.add_argument("--rounds", type=int, default=5)
    parser.add_argument(
        "--scroll-mode",
        choices=["js", "bidi-wheel", "actions", "all"],
        default="all",
    )
    parser.add_argument("--delta-y", type=int, default=12000)
    parser.add_argument("--wait-timeout", type=float, default=1.0)
    parser.add_argument("--drain-limit", type=int, default=8)
    parser.add_argument("--read-body", choices=["matched-only", "none"], default="matched-only")
    parser.add_argument("--listen-targets", choices=["target", "all"], default="target")
    parser.add_argument("--collect-response", action="store_true", default=True)
    parser.add_argument("--no-collect-response", dest="collect_response", action="store_false")
    parser.add_argument("--listen", action="store_true", default=True)
    parser.add_argument("--no-listen", dest="listen", action="store_false")
    parser.add_argument("--headless", action="store_true")
    parser.add_argument("--browser-path")
    parser.add_argument("--profile-root")
    parser.add_argument("--output-prefix", default="artifacts/google_images_listen_benchmark")
    parser.add_argument("--width", type=int, default=1280)
    parser.add_argument("--height", type=int, default=900)
    parser.add_argument("--bidi-timeout", type=float, default=30.0)
    parser.add_argument("--page-timeout", type=float, default=30.0)
    parser.add_argument("--warmup-evals", type=int, default=5)
    parser.add_argument("--warmup-sleep", type=float, default=2.0)
    parser.add_argument("--post-scroll-sleep", type=float, default=0.0)
    parser.add_argument("--disable-gpu", action="store_true")
    parser.add_argument("--local-delay-ms", type=int, default=50)
    parser.add_argument("--local-payload-kb", type=int, default=16)
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    worker_groups = parse_workers(args.workers)
    modes = ["js", "bidi-wheel", "actions"] if args.scroll_mode == "all" else [args.scroll_mode]

    fixture = None
    if args.source == "local":
        fixture = InfiniteScrollServer(
            delay_ms=args.local_delay_ms,
            payload_kb=args.local_payload_kb,
        ).start()
        args.url = fixture.page_url
        args.target_prefix = fixture.target_prefix
        print("Local fixture:", args.url)
        print("Local target:", args.target_prefix)
    else:
        print("Google URL:", args.url)
        print("Target prefix:", args.target_prefix)

    all_rows = []
    try:
        for mode in modes:
            for workers in worker_groups:
                print("\n=== mode={} workers={} rounds={} ===".format(mode, workers, args.rounds))
                start_barrier = threading.Barrier(workers)
                result_queue = queue.Queue()
                with ThreadPoolExecutor(max_workers=workers) as pool:
                    futures = [
                        pool.submit(
                            worker_run,
                            args,
                            workers,
                            worker_id,
                            start_barrier,
                            result_queue,
                            mode,
                            args.url,
                            args.target_prefix,
                        )
                        for worker_id in range(workers)
                    ]
                    for future in as_completed(futures):
                        future.result()

                errors = []
                while not result_queue.empty():
                    result = result_queue.get()
                    all_rows.extend(result["rows"])
                    if result["error"]:
                        errors.append(result["error"])
                if errors:
                    print("Worker errors:")
                    for err in errors:
                        print(err)

                group_rows = [
                    row
                    for row in all_rows
                    if row["mode"] == mode and int(row["workers"]) == workers
                ]
                print("scroll_ms", summarize(group_rows, "scroll_ms"))
                print("wait_ms", summarize(group_rows, "wait_ms"))
                print("body_read_ms", summarize(group_rows, "body_read_ms"))

        csv_path, json_path, summary = write_outputs(all_rows, args.output_prefix)
        print("\nWrote:", csv_path)
        print("Wrote:", json_path)
        print(json.dumps(summary["fields"], ensure_ascii=False, indent=2))
    finally:
        if fixture:
            fixture.stop()


if __name__ == "__main__":
    main()
