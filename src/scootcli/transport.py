"""HTTP transport for provider APIs: pure stdlib, interruptible, streaming-aware.

:class:`NativeTransport` speaks HTTP/1.1 over ``http.client``/``socket``/``ssl`` with no third-party
dependency and no ``curl``. Its contract is tiny: ``request`` returns ``(status, body)`` and
``stream_request`` yields body lines (SSE) ending with a ``HTTP_STATUS:<code>`` sentinel. Both honour a
``cancel_event`` and abort the instant ESC is pressed (the socket is closed from a watcher thread).

Connections are direct, or tunnelled through ``HTTPS_PROXY`` with ``CONNECT`` (no-auth or Basic);
localhost and ``NO_PROXY`` hosts always connect directly. Plain ``http://`` URLs (a local Ollama, for
instance) skip TLS.
"""

from __future__ import annotations

import base64
import codecs
import http.client
import json
import os
import socket
import ssl
import threading
import time
from typing import Iterator, List, Optional, Tuple
from urllib.parse import urlsplit

from .config import Config
from .errors import Interrupted, NetworkError, ProxyError, RequestTimeout, SSLError, TransportError

_STATUS_MARKER = "\nHTTP_STATUS:"

# Fail fast if we can't even connect to the proxy/host (seconds); separate from the overall/idle
# budget so a dead network is detected quickly while a slow-but-alive stream is allowed to continue.
_CONNECT_TIMEOUT = 30


def classify_native_error(exc: BaseException) -> TransportError:
    """Map a socket/ssl/http error to a typed transport error with an actionable hint."""
    if isinstance(exc, TransportError):
        return exc
    if isinstance(exc, ssl.SSLError):
        return SSLError(str(exc) or "TLS error", hint="TLS handshake failed; check the endpoint URL and CA certificates")
    if isinstance(exc, socket.timeout):
        return RequestTimeout("request timed out", hint="slow or blocked network")
    if isinstance(exc, socket.gaierror):
        return NetworkError("cannot resolve host", hint="no internet, DNS failure, or a typo in the base URL")
    if isinstance(exc, (ConnectionError, BrokenPipeError)):
        return NetworkError(str(exc) or "connection failed", hint="is the endpoint up? check the base URL and proxy")
    if isinstance(exc, OSError):
        return NetworkError(str(exc) or "network error", hint="check network/proxy")
    return TransportError(f"transport error: {exc}")


def _parse_proxy(proxy_url: Optional[str]) -> Optional[Tuple[str, int, Optional[str]]]:
    """Return ``(host, port, basic_auth_userpass_or_None)`` for a proxy URL, or ``None`` if unset."""
    if not proxy_url:
        return None
    parts = urlsplit(proxy_url if "://" in proxy_url else "http://" + proxy_url)
    host = parts.hostname
    if not host:
        return None
    port = parts.port or 8080
    basic = None
    if parts.username:
        basic = f"{parts.username}:{parts.password or ''}"
    return host, port, basic


def _split_url(url: str) -> Tuple[str, int, str]:
    """Return ``(host, port, path_with_query)`` for an http(s) URL."""
    parts = urlsplit(url)
    host = parts.hostname or ""
    port = parts.port or (443 if parts.scheme == "https" else 80)
    path = parts.path or "/"
    if parts.query:
        path += "?" + parts.query
    return host, port, path


def _is_tls(url: str) -> bool:
    return urlsplit(url).scheme.lower() != "http"


_LOCAL_HOSTS = ("localhost", "127.0.0.1", "::1", "0.0.0.0")


def proxy_bypassed(host: str, no_proxy: Optional[str] = None) -> bool:
    """Local hosts and anything listed in ``NO_PROXY`` (comma-separated, ``*`` for all) skip the proxy."""
    h = (host or "").lower()
    if h in _LOCAL_HOSTS or h.endswith(".localhost"):
        return True
    raw = no_proxy if no_proxy is not None else (os.environ.get("NO_PROXY") or os.environ.get("no_proxy") or "")
    for entry in raw.split(","):
        entry = entry.strip().lower().lstrip(".")
        if not entry:
            continue
        if entry == "*" or h == entry or h.endswith("." + entry):
            return True
    return False


class NativeTransport:
    """Pure-stdlib HTTP transport with cooperative cancellation."""

    def __init__(self, config: Config):
        self.config = config

    # ── public API ───────────────────────────────────────────────────────────────
    def request(self, method, url, auth_token, auth_scheme="Bearer", body=None,
                extra_headers=None, cancel_event=None) -> "tuple[int, str]":
        holder: dict = {}
        stop = threading.Event()
        self._start_watch(holder, cancel_event, stop)
        try:
            resp = self._connect_and_request(
                method, url, auth_token, auth_scheme, body, extra_headers, holder)
            data = resp.read()
            return resp.status, data.decode("utf-8", "replace").strip()
        except Interrupted:
            raise
        except BaseException as exc:  # noqa: BLE001 - normalise to typed errors
            if cancel_event is not None and cancel_event.is_set():
                raise Interrupted("request cancelled by user")
            raise classify_native_error(exc)
        finally:
            stop.set()
            self._close(holder)

    def stream_request(self, method, url, auth_token, auth_scheme="Bearer", body=None,
                       extra_headers=None, cancel_event=None) -> Iterator[str]:
        holder: dict = {}
        stop = threading.Event()
        self._start_watch(holder, cancel_event, stop)
        try:
            resp = self._connect_and_request(
                method, url, auth_token, auth_scheme, body, extra_headers, holder)
            yield from self._stream_lines(resp, resp.status, cancel_event)
        except Interrupted:
            raise
        except GeneratorExit:
            raise
        except BaseException as exc:  # noqa: BLE001
            if cancel_event is not None and cancel_event.is_set():
                raise Interrupted("request cancelled by user")
            raise classify_native_error(exc)
        finally:
            stop.set()
            self._close(holder)

    # ── request/response plumbing ────────────────────────────────────────────────
    def _connect_and_request(self, method, url, auth_token, auth_scheme, body, extra_headers, holder):
        host, port, path = _split_url(url)
        sock = self._open_socket(host, port, _is_tls(url), holder)
        holder["sock"] = sock
        sock.settimeout(self.config.timeout)
        body_bytes = json.dumps(body).encode("utf-8") if body is not None else None
        headers = self._headers(host, auth_token, auth_scheme, body_bytes, extra_headers)
        self._write_request(sock, method, path, headers, body_bytes)
        resp = http.client.HTTPResponse(sock, method=method.upper())
        holder["resp"] = resp
        resp.begin()
        return resp

    def _headers(self, host, auth_token, auth_scheme, body_bytes, extra_headers) -> "list[tuple]":
        headers = [
            ("Host", host),
            ("Accept", "*/*"),
            ("Connection", "close"),  # server closes at end → clean EOF for read()/SSE
        ]
        if auth_token:
            headers.append(("Authorization", f"{auth_scheme} {auth_token}"))
        for raw in extra_headers or []:
            key, _, value = raw.partition(":")
            headers.append((key.strip(), value.strip()))
        if body_bytes is not None:
            headers.append(("Content-Type", "application/json"))
            headers.append(("Content-Length", str(len(body_bytes))))
        return headers

    @staticmethod
    def _write_request(sock, method, path, headers, body_bytes) -> None:
        lines = [f"{method.upper()} {path} HTTP/1.1"]
        lines += [f"{k}: {v}" for k, v in headers]
        data = ("\r\n".join(lines) + "\r\n\r\n").encode("latin-1")
        if body_bytes:
            data += body_bytes
        sock.sendall(data)

    @staticmethod
    def _stream_lines(resp, status: int, cancel_event) -> Iterator[str]:
        """Yield SSE body lines as they arrive, then a trailing ``HTTP_STATUS:<code>`` sentinel.

        ``read1`` hands over whatever bytes are already available, so a short event is delivered
        at once instead of waiting for a full buffer; the incremental decoder keeps a multi-byte
        character split across two reads intact.
        """
        read = getattr(resp, "read1", None) or resp.read
        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        buf = ""
        while True:
            if cancel_event is not None and cancel_event.is_set():
                raise Interrupted("request cancelled by user")
            chunk = read(65536)
            if not chunk:
                break
            buf += decoder.decode(chunk)
            while "\n" in buf:
                line, buf = buf.split("\n", 1)
                yield line.rstrip("\r")
        buf += decoder.decode(b"", final=True)
        if buf:
            yield buf.rstrip("\r")
        yield f"{_STATUS_MARKER.strip()}{status}"

    # ── connection + proxy tunnelling ────────────────────────────────────────────
    def _open_socket(self, host: str, port: int, tls: bool, holder: dict):
        proxy = _parse_proxy(self.config.proxy)
        if proxy is None or proxy_bypassed(host):
            raw = socket.create_connection((host, port), timeout=_CONNECT_TIMEOUT)
        else:
            raw = self._open_tunnel(host, port, proxy, holder)
        holder["sock"] = raw
        if not tls:
            return raw
        return ssl.create_default_context().wrap_socket(raw, server_hostname=host)

    def _open_tunnel(self, host, port, proxy, holder):
        """``CONNECT`` through the proxy (no-auth or Basic) and return the raw socket."""
        phost, pport, basic = proxy
        raw = socket.create_connection((phost, pport), timeout=_CONNECT_TIMEOUT)
        holder["sock"] = raw
        authorization = None
        if basic:
            authorization = "Basic " + base64.b64encode(basic.encode("utf-8")).decode("ascii")
        self._send_connect(raw, host, port, authorization)
        status, _auth_headers = self._read_connect_response(raw)
        if status == 200:
            return raw
        raw.close()
        hint = ("the proxy wants credentials: put them in HTTPS_PROXY as http://user:pass@host:port"
                if status == 407 else "check HTTPS_PROXY")
        raise ProxyError(f"proxy CONNECT failed (HTTP {status})", hint=hint)

    @staticmethod
    def _send_connect(raw, host, port, authorization: Optional[str]) -> None:
        lines = [f"CONNECT {host}:{port} HTTP/1.1", f"Host: {host}:{port}"]
        if authorization:
            lines.append(f"Proxy-Authorization: {authorization}")
        lines.append("Proxy-Connection: keep-alive")
        raw.sendall(("\r\n".join(lines) + "\r\n\r\n").encode("latin-1"))

    @staticmethod
    def _read_connect_response(raw) -> "tuple[int, list]":
        """Read a CONNECT response's status + ``Proxy-Authenticate`` headers, consuming any body."""
        data = b""
        while b"\r\n\r\n" not in data:
            chunk = raw.recv(1)
            if not chunk:
                break
            data += chunk
        head, _, _ = data.partition(b"\r\n\r\n")
        text = head.decode("latin-1")
        status_line, _, header_block = text.partition("\r\n")
        pieces = status_line.split(" ", 2)
        status = int(pieces[1]) if len(pieces) > 1 and pieces[1].isdigit() else 0
        auth: List[str] = []
        length = 0
        for line in header_block.split("\r\n"):
            key, _, value = line.partition(":")
            k = key.strip().lower()
            if k == "proxy-authenticate":
                auth.append(value.strip())
            elif k == "content-length":
                try:
                    length = int(value.strip())
                except ValueError:
                    length = 0
        if length:  # drain the response body so the socket is clean
            remaining = length
            while remaining > 0:
                chunk = raw.recv(min(remaining, 4096))
                if not chunk:
                    break
                remaining -= len(chunk)
        return status, auth

    # ── cancellation ─────────────────────────────────────────────────────────────
    @staticmethod
    def _start_watch(holder: dict, cancel_event, stop: threading.Event):
        """Shut the connection the moment ESC is pressed, unblocking any in-flight recv."""
        if cancel_event is None:
            return None

        def _watch() -> None:
            while not stop.is_set():
                if cancel_event.is_set():
                    NativeTransport._close(holder)
                    return
                time.sleep(0.05)

        thread = threading.Thread(target=_watch, daemon=True)
        thread.start()
        return thread

    @staticmethod
    def _close(holder: dict) -> None:
        """Shut down, then close. ``close()`` alone does not wake a thread blocked in ``recv`` while
        the response's file object still holds the socket; ``shutdown`` does, on every platform."""
        sock = holder.get("sock")
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                sock.close()
            except OSError:
                pass
        resp = holder.get("resp")
        if resp is not None:
            try:
                resp.close()
            except OSError:
                pass


def make_transport(config: Config) -> NativeTransport:
    """Factory kept for symmetry with the provider factory; there is one transport."""
    return NativeTransport(config)
