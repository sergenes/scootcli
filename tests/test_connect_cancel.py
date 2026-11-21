"""The connect phase is cancellable: an interrupt does not wait for the connect timeout."""

from __future__ import annotations

import socket
import threading
import time

import pytest

from scootcli.config import Config
from scootcli.errors import Interrupted, NetworkError
from scootcli.transport import NativeTransport


class _HangingConnect:
    """Stands in for ``socket.create_connection``: blocks like an unroutable host until released."""

    def __init__(self):
        self.release = threading.Event()
        self.started = threading.Event()
        self.sockets = []

    def __call__(self, address, timeout=None, **_kw):
        self.started.set()
        self.release.wait(30)
        a, b = socket.socketpair()
        b.close()
        self.sockets.append(a)
        return a


def _cancel_after(delay: float) -> threading.Event:
    cancel = threading.Event()
    threading.Timer(delay, cancel.set).start()
    return cancel


@pytest.mark.parametrize("url", ["http://203.0.113.1:9/x", "https://203.0.113.1/x"])
def test_interrupt_during_connect_returns_at_once(monkeypatch, url):
    hang = _HangingConnect()
    monkeypatch.setattr(socket, "create_connection", hang)
    cancel = _cancel_after(0.1)
    started = time.monotonic()
    with pytest.raises(Interrupted):
        NativeTransport(Config()).request("GET", url, None, cancel_event=cancel)
    assert time.monotonic() - started < 2.0
    hang.release.set()


def test_interrupt_during_streaming_connect_returns_at_once(monkeypatch):
    hang = _HangingConnect()
    monkeypatch.setattr(socket, "create_connection", hang)
    cancel = _cancel_after(0.1)
    started = time.monotonic()
    with pytest.raises(Interrupted):
        list(NativeTransport(Config()).stream_request("POST", "http://203.0.113.1:9/x", None, body={}, cancel_event=cancel))
    assert time.monotonic() - started < 2.0
    hang.release.set()


def test_interrupt_during_proxy_connect_returns_at_once(monkeypatch):
    hang = _HangingConnect()
    monkeypatch.setattr(socket, "create_connection", hang)
    cancel = _cancel_after(0.1)
    started = time.monotonic()
    with pytest.raises(Interrupted):
        NativeTransport(Config().override(proxy="http://203.0.113.2:3128")).request(
            "GET", "https://example.invalid/x", None, cancel_event=cancel)
    assert time.monotonic() - started < 2.0
    hang.release.set()


def test_socket_from_an_abandoned_connect_is_closed(monkeypatch):
    hang = _HangingConnect()
    monkeypatch.setattr(socket, "create_connection", hang)
    cancel = _cancel_after(0.05)
    with pytest.raises(Interrupted):
        NativeTransport(Config()).request("GET", "http://203.0.113.1:9/x", None, cancel_event=cancel)
    hang.release.set()
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline and not (hang.sockets and hang.sockets[0].fileno() == -1):
        time.sleep(0.01)
    assert hang.sockets and hang.sockets[0].fileno() == -1  # the late connection did not leak


def test_connect_failure_still_reports_a_network_error(monkeypatch):
    def refuse(address, timeout=None, **_kw):
        raise ConnectionRefusedError(61, "Connection refused")

    monkeypatch.setattr(socket, "create_connection", refuse)
    with pytest.raises(NetworkError):
        NativeTransport(Config()).request("GET", "http://203.0.113.1:9/x", None, cancel_event=threading.Event())


def test_connect_without_a_cancel_event_is_unchanged(monkeypatch):
    seen = {}

    def refuse(address, timeout=None, **_kw):
        seen["thread"] = threading.current_thread()
        raise ConnectionRefusedError(61, "Connection refused")

    monkeypatch.setattr(socket, "create_connection", refuse)
    with pytest.raises(NetworkError):
        NativeTransport(Config()).request("GET", "http://203.0.113.1:9/x", None)
    assert seen["thread"] is threading.current_thread()
