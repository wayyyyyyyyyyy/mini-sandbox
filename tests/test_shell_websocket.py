import sys

import pytest
from fastapi.testclient import TestClient

from app.main import app


def _client(monkeypatch, tmp_path):
    monkeypatch.setattr("app.auth.SANDBOX_API_KEY", "")
    monkeypatch.setattr("app.auth.JWT_PUBLIC_KEY", "")
    monkeypatch.setattr("app.auth._tickets", {})
    monkeypatch.setattr("app.security.WORKSPACE", tmp_path)
    monkeypatch.setattr("app.main.WORKSPACE", tmp_path)
    return TestClient(app)


def test_shell_websocket_starts_session_and_streams_output(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)

    with client.websocket_connect("/shell/ws") as websocket:
        hello = websocket.receive_json()
        assert hello["type"] == "session"
        assert hello["session_id"].startswith("sh_")

        websocket.send_json(
            {
                "type": "input",
                "data": f'"{sys.executable}" -c "print(\'ws-ok\')"\n',
            }
        )

        message = _receive_until(websocket, "ws-ok")

    assert message["type"] == "output"
    assert "ws-ok" in message["data"]


def test_shell_websocket_accepts_ticket_query(monkeypatch, tmp_path):
    monkeypatch.setattr("app.auth.SANDBOX_API_KEY", "secret")
    monkeypatch.setattr("app.auth.JWT_PUBLIC_KEY", "")
    monkeypatch.setattr("app.auth._tickets", {})
    monkeypatch.setattr("app.security.WORKSPACE", tmp_path)
    monkeypatch.setattr("app.main.WORKSPACE", tmp_path)
    client = TestClient(app)

    created = client.post("/tickets", headers={"Authorization": "Bearer secret"})
    ticket = created.json()["data"]["ticket"]

    with client.websocket_connect(f"/shell/ws?ticket={ticket}") as websocket:
        hello = websocket.receive_json()

    assert hello["type"] == "session"
    assert hello["session_id"].startswith("sh_")


def test_shell_websocket_rejects_missing_credentials(monkeypatch, tmp_path):
    monkeypatch.setattr("app.auth.SANDBOX_API_KEY", "secret")
    monkeypatch.setattr("app.auth.JWT_PUBLIC_KEY", "")
    monkeypatch.setattr("app.auth._tickets", {})
    monkeypatch.setattr("app.security.WORKSPACE", tmp_path)
    monkeypatch.setattr("app.main.WORKSPACE", tmp_path)
    client = TestClient(app)

    with pytest.raises(Exception):
        with client.websocket_connect("/shell/ws"):
            pass


def test_shell_websocket_supports_ping_pong_and_resize(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)

    with client.websocket_connect("/shell/ws") as websocket:
        websocket.receive_json()
        websocket.send_json({"type": "pong", "data": {"timestamp": 123}})
        websocket.send_json({"type": "resize", "data": {"cols": 100, "rows": 30}})
        websocket.send_json(
            {
                "type": "input",
                "data": f'"{sys.executable}" -c "print(\'after-resize\')"\n',
            }
        )

        message = _receive_until(websocket, "after-resize")

    assert message["type"] == "output"


def _receive_until(websocket, text: str, max_messages: int = 20):
    for _ in range(max_messages):
        message = websocket.receive_json()
        if message.get("type") == "output" and text in message.get("data", ""):
            return message
    raise AssertionError(f"did not receive output containing {text!r}")
