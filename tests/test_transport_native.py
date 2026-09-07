"""Network-free tests for the native (stdlib) transport + transport selection (PLAN §14 / backlog).

We test the pure helpers (URL/proxy parsing, header building, SSE line iteration, error mapping)
and the factory. The socket/TLS path itself is exercised against live APIs and cannot run offline.

Run: PYTHONPATH=src python3 tests/test_transport_native.py
"""

from __future__ import annotations

import socket
import ssl

from scootcli.config import Config
from scootcli.errors import (
    Interrupted,
    NetworkError,
    ProxyError,
    RequestTimeout,
    SSLError,
    TransportError,
)
from scootcli import transport as T


# ── URL + proxy parsing ─────────────────────────────────────────────────────────
def test_split_url_defaults_and_query():
    assert T._split_url("https://api.openai.com/v1/responses") == (
        "api.openai.com", 443, "/v1/responses")
    assert T._split_url("https://h.example.com:8443/x?y=1&z=2") == (
        "h.example.com", 8443, "/x?y=1&z=2")
    # No path → "/".
    assert T._split_url("https://h.example.com") == ("h.example.com", 443, "/")


def test_parse_proxy_variants():
    assert T._parse_proxy(None) is None
    assert T._parse_proxy("") is None
    assert T._parse_proxy("http://proxy.example.com:8080") == (
        "proxy.example.com", 8080, None)
    # Basic-auth creds embedded in the URL are extracted.
    assert T._parse_proxy("http://user:pass@proxy:3128") == ("proxy", 3128, "user:pass")
    # Scheme-less proxy is tolerated.
    assert T._parse_proxy("proxy.local:8080") == ("proxy.local", 8080, None)


# ── header + request framing ─────────────────────────────────────────────────────
def test_headers_include_auth_extra_and_body_meta():
    nt = T.NativeTransport(Config())
    body = b'{"a":1}'
    headers = dict(nt._headers("h.example.com", "jwt", "Bearer", body,
                               ["X-Custom: yes"]))
    assert headers["Host"] == "h.example.com"
    assert headers["Authorization"] == "Bearer jwt"
    assert headers["X-Custom"] == "yes"
    assert headers["Content-Type"] == "application/json"
    assert headers["Content-Length"] == str(len(body))
    assert headers["Connection"] == "close"


def test_headers_omit_body_meta_when_no_body():
    nt = T.NativeTransport(Config())
    headers = dict(nt._headers("h", "tok", "token", None, None))
    assert "Content-Length" not in headers
    assert headers["Authorization"] == "token tok"


# ── SSE line iteration + status sentinel ─────────────────────────────────────────
class _FakeResp:
    """Minimal object with a chunked read(n), like http.client.HTTPResponse."""

    def __init__(self, chunks):
        self._chunks = list(chunks)

    def read(self, n):
        return self._chunks.pop(0) if self._chunks else b""


def test_stream_lines_splits_and_appends_status():
    resp = _FakeResp([b"data: a\n\ndata: ", b"b\n", b"data: [DONE]\n"])
    lines = list(T.NativeTransport._stream_lines(resp, 200, cancel_event=None))
    assert lines == ["data: a", "", "data: b", "data: [DONE]", "HTTP_STATUS:200"]


def test_stream_lines_flushes_trailing_partial_line():
    resp = _FakeResp([b"partial no newline"])
    lines = list(T.NativeTransport._stream_lines(resp, 503, cancel_event=None))
    assert lines == ["partial no newline", "HTTP_STATUS:503"]


def test_stream_lines_honours_cancel():
    class _Ev:
        def is_set(self):
            return True

    resp = _FakeResp([b"data: x\n"])
    try:
        list(T.NativeTransport._stream_lines(resp, 200, cancel_event=_Ev()))
        assert False, "expected Interrupted"
    except Interrupted:
        pass


# ── error mapping ─────────────────────────────────────────────────────────────────
def test_classify_native_error_mapping():
    assert isinstance(T.classify_native_error(ssl.SSLError("boom")), SSLError)
    assert isinstance(T.classify_native_error(socket.timeout()), RequestTimeout)
    assert isinstance(T.classify_native_error(socket.gaierror()), NetworkError)
    assert isinstance(T.classify_native_error(ConnectionResetError()), NetworkError)
    assert isinstance(T.classify_native_error(OSError("x")), NetworkError)
    # A typed transport error passes through unchanged.
    pe = ProxyError("nope")
    assert T.classify_native_error(pe) is pe
    # Anything else → generic TransportError.
    assert isinstance(T.classify_native_error(ValueError("weird")), TransportError)
    # Hints are populated for the actionable ones.
    assert T.classify_native_error(socket.gaierror()).hint


# ── factory ─────────────────────────────────────────────────────────────────────
def test_make_transport_returns_native():
    assert isinstance(T.make_transport(Config()), T.NativeTransport)


def test_is_tls_by_scheme():
    assert T._is_tls("https://api.openai.com/v1/responses")
    assert not T._is_tls("http://localhost:11434/v1/responses")


if __name__ == "__main__":
    import types

    class _MP:
        def setattr(self, obj, name, value):
            setattr(obj, name, value)

    passed = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_") and isinstance(fn, types.FunctionType):
            if "monkeypatch" in fn.__code__.co_varnames:
                fn(_MP())
            else:
                fn()
            print(f"ok  {name}")
            passed += 1
    print(f"\n{passed} passed")



def test_proxy_bypass_for_local_and_no_proxy_hosts():
    assert T.proxy_bypassed("localhost", "")
    assert T.proxy_bypassed("127.0.0.1", "")
    assert T.proxy_bypassed("ollama.localhost", "")
    assert not T.proxy_bypassed("api.openai.com", "")
    assert T.proxy_bypassed("api.openai.com", "*")
    assert T.proxy_bypassed("api.openai.com", "internal.example, .openai.com")
    assert T.proxy_bypassed("api.openai.com", "openai.com")
    assert not T.proxy_bypassed("api.openai.com", "notopenai.com")


# ── 0.9.0: prompt delivery, intact characters, and a cancel that unblocks (R10, R14) ─
class _Read1Resp:
    """Like HTTPResponse: ``read1`` returns what is available; ``read(n)`` would block for more."""

    def __init__(self, chunks):
        self._chunks = list(chunks)

    def read1(self, n):
        return self._chunks.pop(0) if self._chunks else b""

    def read(self, n):
        raise AssertionError("read(n) blocks until n bytes arrive; the stream must use read1")


def test_stream_lines_uses_available_data_and_keeps_split_utf8():
    resp = _Read1Resp([b"data: hello\n", b"data: \xe2\x9c", b"\x94 done\n", b"data: caf\xc3", b"\xa9"])
    lines = list(T.NativeTransport._stream_lines(resp, 200, cancel_event=None))
    assert lines == ["data: hello", "data: ✔ done", "data: café", "HTTP_STATUS:200"]


def test_close_unblocks_a_thread_stuck_in_recv():
    import socket
    import threading

    a, b = socket.socketpair()
    got = []

    def _reader():
        try:
            got.append(a.recv(10))
        except OSError as exc:
            got.append(exc)

    t = threading.Thread(target=_reader, daemon=True)
    t.start()
    import time
    time.sleep(0.1)
    T.NativeTransport._close({"sock": a})  # what the ESC watcher calls
    t.join(timeout=2)
    assert not t.is_alive(), "recv stayed blocked after the watcher closed the socket"
    b.close()


def test_close_also_closes_the_response():
    closed = []

    class _Resp:
        def close(self):
            closed.append(True)

    T.NativeTransport._close({"resp": _Resp()})
    assert closed == [True]
