import queue
import sys
import threading

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


def test_shell_websocket_keeps_a_persistent_interactive_shell(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    if sys.platform == "win32":
        set_command = "set SANDBOX_WS_FLAG=sticky\n"
        read_command = "echo %SANDBOX_WS_FLAG%\n"
    else:
        set_command = "SANDBOX_WS_FLAG=sticky\n"
        read_command = "echo $SANDBOX_WS_FLAG\n"

    with client.websocket_connect("/shell/ws") as websocket:
        websocket.receive_json()
        websocket.send_json({"type": "input", "data": set_command})
        websocket.send_json({"type": "input", "data": read_command})

        message = _receive_until(websocket, "sticky")

    assert message["type"] == "output"


def test_shell_websocket_closes_session_on_disconnect(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)

    with client.websocket_connect("/shell/ws") as websocket:
        hello = websocket.receive_json()
        session_id = hello["session_id"]

    response = client.post("/shell/view", json={"id": session_id})

    assert response.status_code == 200
    assert response.json()["data"]["status"] == "closed"


def _receive_until(websocket, text: str, max_messages: int = 20):
    for _ in range(max_messages):
        message = _receive_json_with_timeout(websocket)
        if message.get("type") == "output" and text in message.get("data", ""):
            return message
    raise AssertionError(f"did not receive output containing {text!r}")


def _receive_json_with_timeout(websocket, timeout: float = 1):
    result = queue.Queue(maxsize=1)

    def receive():
        try:
            result.put(websocket.receive_json())
        except BaseException as exc:
            result.put(exc)

    thread = threading.Thread(target=receive, daemon=True)
    thread.start()
    try:
        item = result.get(timeout=timeout)
    except queue.Empty as exc:
        raise AssertionError("timed out waiting for websocket message") from exc
    if isinstance(item, BaseException):
        raise item
    return item
