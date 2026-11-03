"""本地中转：让官方 N_m3u8DL-RE 能拉被指纹拦截的清单。

背景：surrit / nineyu 会拒绝官方 N_m3u8DL-RE 的请求（同一 Referer 下 curl、wreq 都是 200），
而 GD 自己的 wreq 客户端能过。于是起一个只监听 127.0.0.1 的中转：

    N_m3u8DL-RE → http://127.0.0.1:PORT/p/{会话}.m3u8
                  中转用 wreq 带上游 Referer/UA 取流，再把清单里的 URI 改写成中转地址

会话（上游地址 + Referer + UA）编码在 URL 里，服务端不存状态：进程重启后只要端口没变，
暂停的任务仍能续传。

实现只用标准库（http.server）监听，异步的 wreq 放在独立线程的事件循环里跑：
打包版没有 aiohttp，这样 Pack 可以原样丢进 features 目录使用。
"""

from __future__ import annotations

import asyncio
import base64
import json
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urljoin, urlparse, urlsplit

URI_ATTR_PATTERN = re.compile(r'URI="([^"]*)"')
RELAY_HOSTS = ("surrit.com", "nineyu.com")
PLAYLIST_PATH = re.compile(r"^/p/([A-Za-z0-9_\-=]+)\.m3u8$")
SEGMENT_PATH = re.compile(r"^/s/([A-Za-z0-9_\-=]+)$")
FETCH_ATTEMPTS = 2

_relayBase: str = ""
_server: ThreadingHTTPServer | None = None
_bridge: "_UpstreamBridge | None" = None


# ── 会话编解码（纯函数）──


def _b64encode(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode("utf-8")).decode("ascii").rstrip("=")


def _b64decode(text: str) -> str:
    padding = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + padding).decode("utf-8")


def encodeSession(url: str, referer: str, userAgent: str) -> str:
    payload = json.dumps(
        {"u": url, "r": referer, "a": userAgent}, separators=(",", ":"), ensure_ascii=False,
    )
    return _b64encode(payload)


def decodeSession(payload: str) -> dict:
    data = json.loads(_b64decode(payload))
    return {"url": data.get("u", ""), "referer": data.get("r", ""), "userAgent": data.get("a", "")}


def shouldRelay(url: str) -> bool:
    host = (urlparse(str(url or "").strip()).hostname or "").lower()
    return any(host == item or host.endswith(f".{item}") for item in RELAY_HOSTS)


def relayPlaylistUrl(base: str, payload: str) -> str:
    return f"{base}/p/{payload}.m3u8"


def relaySegmentUrl(base: str, payload: str, upstream: str) -> str:
    return f"{base}/s/{payload}?u={_b64encode(upstream)}"


# ── 清单改写（纯函数）──


def rewritePlaylist(text: str, baseUrl: str, makeUrl) -> str:
    """把清单里的相对/绝对 URI 全换成中转地址，注释与标签原样保留。"""
    lines: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            lines.append(line)
            continue
        if stripped.startswith("#"):
            lines.append(_rewriteTag(stripped, baseUrl, makeUrl))
            continue
        lines.append(makeUrl(urljoin(baseUrl, stripped)))
    return "\n".join(lines) + "\n"


def _rewriteTag(line: str, baseUrl: str, makeUrl) -> str:
    def replace(matched: re.Match) -> str:
        return f'URI="{makeUrl(urljoin(baseUrl, matched.group(1)))}"'

    return URI_ATTR_PATTERN.sub(replace, line)


def relayBase() -> str:
    return _relayBase


# ── 上游取流 ──


class _UpstreamBridge:
    """http.server 是同步的、wreq 是异步的：用一个常驻线程承载事件循环。"""

    def __init__(self) -> None:
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run, name="jav-relay-bridge", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    def call(self, coroutine, timeout: float = 60.0):
        return asyncio.run_coroutine_threadsafe(coroutine, self._loop).result(timeout)

    def close(self) -> None:
        if self._loop.is_closed():
            return
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(timeout=3)
        if not self._loop.is_running() and not self._loop.is_closed():
            self._loop.close()


async def _fetchAsync(url: str, referer: str, userAgent: str) -> tuple[int, bytes, str, str]:
    from app.client import buildClient

    headers = {"Referer": referer, "User-Agent": userAgent, "Accept": "*/*"}
    client = buildClient(headers=headers, userAgent=userAgent, timeout=30)
    try:
        response = await client.get(url)
        status = response.status.as_int() if hasattr(response.status, "as_int") else 200
        try:
            body = bytes(await response.bytes())
        except AttributeError:
            chunks = []
            async for chunk in response.stream():
                chunks.append(bytes(chunk))
            body = b"".join(chunks)

        contentType = "application/octet-stream"
        try:
            for key, value in response.headers:
                if bytes(key).decode().lower() == "content-type":
                    contentType = bytes(value).decode()
                    break
        except Exception:
            pass
        return status, body, str(response.url), contentType
    finally:
        client.close()


def _fetch(url: str, referer: str, userAgent: str) -> tuple[int, bytes, str, str]:
    """带一次重试：代理抖动时 5xx 常见，直接失败会把分段下载拖死。"""
    lastError: Exception | None = None
    for attempt in range(FETCH_ATTEMPTS):
        if _bridge is None:
            raise RuntimeError("中转未启动")
        try:
            result = _bridge.call(_fetchAsync(url, referer, userAgent))
        except Exception as error:
            lastError = error
        else:
            if result[0] < 500:
                return result
            lastError = RuntimeError(f"upstream {result[0]}")
        if attempt + 1 < FETCH_ATTEMPTS:
            time.sleep(0.3)
    raise lastError or RuntimeError("上游请求失败")


# ── HTTP 服务 ──


class _RelayHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "GhostDownloaderRelay"

    def log_message(self, *args) -> None:
        pass

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlsplit(self.path)
        matched = PLAYLIST_PATH.match(parsed.path)
        if matched:
            self._servePlaylist(matched.group(1))
            return
        matched = SEGMENT_PATH.match(parsed.path)
        if matched:
            self._serveSegment(matched.group(1), parse_qs(parsed.query).get("u", [""])[0])
            return
        self._send(404, b"not found", "text/plain")

    def _servePlaylist(self, payload: str) -> None:
        try:
            session = decodeSession(payload)
        except Exception:
            self._send(400, b"bad session", "text/plain")
            return
        try:
            status, body, finalUrl, _ = _fetch(session["url"], session["referer"], session["userAgent"])
        except Exception as error:
            self._send(502, str(error).encode("utf-8", "ignore"), "text/plain")
            return
        if status != 200:
            self._send(status, f"upstream {status}".encode(), "text/plain")
            return

        text = body.decode("utf-8", "ignore")
        rewritten = rewritePlaylist(
            text, finalUrl, lambda target: relaySegmentUrl(_relayBase, payload, target),
        )
        self._send(200, rewritten.encode("utf-8"), "application/vnd.apple.mpegurl")

    def _serveSegment(self, payload: str, raw: str) -> None:
        try:
            session = decodeSession(payload)
            upstream = _b64decode(raw)
        except Exception:
            self._send(400, b"bad request", "text/plain")
            return
        try:
            status, body, finalUrl, contentType = _fetch(
                upstream, session["referer"], session["userAgent"],
            )
        except Exception as error:
            self._send(502, str(error).encode("utf-8", "ignore"), "text/plain")
            return
        if status != 200:
            self._send(status, f"upstream {status}".encode(), "text/plain")
            return

        # 变体清单同样要改写，否则二进制拿到的相对分片地址会指向中转根路径
        if body.lstrip()[:7] == b"#EXTM3U":
            text = body.decode("utf-8", "ignore")
            rewritten = rewritePlaylist(
                text, finalUrl, lambda target: relaySegmentUrl(_relayBase, payload, target),
            )
            self._send(200, rewritten.encode("utf-8"), "application/vnd.apple.mpegurl")
            return
        self._send(200, body, contentType)

    def _send(self, status: int, body: bytes, contentType: str) -> None:
        try:
            self.send_response(status)
            self.send_header("Content-Type", contentType)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass


def _start(preferredPort: int) -> str:
    global _server, _bridge
    if _bridge is None:
        _bridge = _UpstreamBridge()

    lastError: Exception | None = None
    for port in (int(preferredPort or 0), 0):
        if port < 0:
            continue
        try:
            server = ThreadingHTTPServer(("127.0.0.1", port), _RelayHandler)
        except OSError as error:
            lastError = error
            continue
        server.daemon_threads = True
        _server = server
        threading.Thread(target=server.serve_forever, name="jav-relay", daemon=True).start()
        return f"http://127.0.0.1:{server.server_address[1]}"

    raise RuntimeError(f"无法启动本地中转：{lastError}")


async def ensureStarted(preferredPort: int = 0) -> str:
    """启动（或复用）中转服务，返回形如 http://127.0.0.1:PORT 的基地址。"""
    global _relayBase
    if _relayBase and _server is not None:
        return _relayBase
    _relayBase = _start(preferredPort)
    return _relayBase


async def stop() -> None:
    global _relayBase, _server, _bridge
    server, _server = _server, None
    bridge, _bridge = _bridge, None
    _relayBase = ""
    if server is not None:
        server.shutdown()
        server.server_close()
    if bridge is not None:
        bridge.close()
