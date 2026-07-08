import threading

import pytest
from fastapi.testclient import TestClient
from websockets.sync.server import serve

from app.main import app


def _client(monkeypatch, tmp_path):
    monkeypatch.setattr("app.auth.SANDBOX_API_KEY", "")
    monkeypatch.setattr("app.auth.JWT_PUBLIC_KEY", "")
    monkeypatch.setattr("app.security.WORKSPACE", tmp_path)
    monkeypatch.setattr("app.main.WORKSPACE", tmp_path)
    return TestClient(app)


def _start_websocket_target(handler):
    server = serve(handler, "127.0.0.1", 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def test_proxy_websocket_forwards_text_messages_path_and_query(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    seen = {}

    def handler(websocket):
        seen["path"] = websocket.request.path
        message = websocket.recv()
        websocket.send(f"echo:{message}")

    server, thread = _start_websocket_target(handler)
    try:
        port = server.socket.getsockname()[1]
        with client.websocket_connect(f"/proxy/{port}/ws/echo?token=abc") as websocket:
            websocket.send_text("hello")
            reply = websocket.receive_text()
    finally:
        server.shutdown()
        thread.join(timeout=5)

    assert reply == "echo:hello"
    assert seen["path"] == "/ws/echo?token=abc"


def test_proxy_websocket_forwards_binary_messages(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)

    def handler(websocket):
        message = websocket.recv()
        websocket.send(message + b"-ok")

    server, thread = _start_websocket_target(handler)
    try:
        port = server.socket.getsockname()[1]
        with client.websocket_connect(f"/proxy/{port}/binary") as websocket:
            websocket.send_bytes(b"payload")
            reply = websocket.receive_bytes()
    finally:
        server.shutdown()
        thread.join(timeout=5)

    assert reply == b"payload-ok"


def test_proxy_websocket_rejects_invalid_port(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)

    with pytest.raises(Exception):
        with client.websocket_connect("/proxy/70000/ws"):
            pass


def test_proxy_websocket_rejects_missing_credentials(monkeypatch, tmp_path):
    monkeypatch.setattr("app.auth.SANDBOX_API_KEY", "secret")
    monkeypatch.setattr("app.auth.JWT_PUBLIC_KEY", "")
    monkeypatch.setattr("app.security.WORKSPACE", tmp_path)
    monkeypatch.setattr("app.main.WORKSPACE", tmp_path)
    client = TestClient(app)

    with pytest.raises(Exception):
        with client.websocket_connect("/proxy/9/ws"):
            pass


def test_proxy_websocket_closes_when_upstream_is_unavailable(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)

    with pytest.raises(Exception):
        with client.websocket_connect("/proxy/9/ws"):
            pass
