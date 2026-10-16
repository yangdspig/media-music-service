"""Exercise the gateway relay with real sockets and the packaged service script."""
import importlib.util
import os
from pathlib import Path
import socket
import subprocess
import threading
import time

import pytest

PACKAGE = Path(__file__).resolve().parents[1] / "fnos-fpk" / "fpk"


@pytest.fixture
def proxy():
    spec = importlib.util.spec_from_file_location("fnos_proxy", PACKAGE / "app" / "proxy.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_relay_preserves_keepalive_requests_and_half_close(proxy, monkeypatch):
    with socket.create_server(("127.0.0.1", 0)) as server:
        monkeypatch.setattr(proxy, "BACKEND", server.getsockname())
        browser, gateway = socket.socketpair()
        browser.settimeout(2)
        worker = threading.Thread(target=proxy.handle, args=(gateway,), daemon=True)
        worker.start()
        backend, _ = server.accept()
        backend.settimeout(2)
        try:
            for request in (b"GET /app/MUSICASSISTANT/ HTTP/1.1\r\n\r\n",
                            b"POST /app/MUSICASSISTANT/api/v1/downloads HTTP/1.1\r\nContent-Length: 4\r\n\r\n\x00\x01\xfe\xff"):
                browser.sendall(request)
                assert backend.recv(4096) == request
                response = b"HTTP/1.1 200 OK\r\nContent-Length: 4\r\n\r\n\x00\x01\xfe\xff"
                backend.sendall(response)
                assert browser.recv(4096) == response
            browser.shutdown(socket.SHUT_WR)
            assert backend.recv(1) == b""
            backend.sendall(b"final audio bytes")
            backend.shutdown(socket.SHUT_WR)
            assert browser.recv(4096) == b"final audio bytes"
            assert browser.recv(1) == b""
            worker.join(timeout=2)
            assert not worker.is_alive()
        finally:
            browser.close()
            backend.close()


def test_connection_timeout_does_not_limit_established_stream(proxy, monkeypatch):
    with socket.create_server(("127.0.0.1", 0)) as server:
        monkeypatch.setattr(proxy, "BACKEND", server.getsockname())
        monkeypatch.setattr(proxy, "CONNECT_TIMEOUT", 0.1)
        browser, gateway = socket.socketpair()
        browser.settimeout(2)
        worker = threading.Thread(target=proxy.handle, args=(gateway,), daemon=True)
        worker.start()
        backend, _ = server.accept()
        try:
            time.sleep(0.25)
            backend.sendall(b"late audio data")
            assert browser.recv(4096) == b"late audio data"
        finally:
            backend.close()
            browser.close()
            worker.join(timeout=2)


def test_backend_unavailable_returns_502(proxy, monkeypatch):
    with socket.create_server(("127.0.0.1", 0)) as unused:
        address = unused.getsockname()
    monkeypatch.setattr(proxy, "BACKEND", address)
    browser, gateway = socket.socketpair()
    browser.settimeout(2)
    worker = threading.Thread(target=proxy.handle, args=(gateway,), daemon=True)
    worker.start()
    try:
        response = browser.recv(4096)
        assert response.startswith(b"HTTP/1.1 502 Bad Gateway\r\n")
        assert b"8765" in response
        assert browser.recv(1) == b""
    finally:
        browser.close()
        worker.join(timeout=2)


def test_packaged_lifecycle_waits_for_socket_and_stops_only_proxy(tmp_path):
    target = tmp_path / "target"
    state = tmp_path / "var"
    target.mkdir()
    state.mkdir()
    (target / "proxy.py").write_bytes((PACKAGE / "app" / "proxy.py").read_bytes())
    env = {**os.environ, "TRIM_APPDEST": str(target), "TRIM_PKGVAR": str(state),
           "TRIM_TEMP_LOGFILE": str(tmp_path / "error.log")}

    def run(command):
        return subprocess.run(["bash", str(PACKAGE / "cmd" / "main"), command], env=env,
                              capture_output=True, text=True, timeout=10)

    assert run("status").returncode == 3
    try:
        result = run("start")
        assert result.returncode == 0, result.stderr
        assert (target / "app.sock").is_socket()
        pid = (state / "proxy.pid").read_text()
        assert run("status").returncode == 0
        assert run("start").returncode == 0
        assert (state / "proxy.pid").read_text() == pid
    finally:
        assert run("stop").returncode == 0
    assert run("status").returncode == 3
    assert not (target / "app.sock").exists()
    assert not (state / "proxy.pid").exists()


def test_packaged_start_reports_python_failure(tmp_path):
    target = tmp_path / "target"
    state = tmp_path / "var"
    target.mkdir()
    state.mkdir()
    (target / "proxy.py").write_text("raise RuntimeError('cannot start proxy')")
    error = tmp_path / "error.log"
    env = {**os.environ, "TRIM_APPDEST": str(target), "TRIM_PKGVAR": str(state),
           "TRIM_TEMP_LOGFILE": str(error)}
    result = subprocess.run(["bash", str(PACKAGE / "cmd" / "main"), "start"], env=env,
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 1
    assert error.read_text()
    assert not (state / "proxy.pid").exists()
