import sys
from urllib.parse import parse_qs, urlparse

from fastapi.testclient import TestClient

from app.main import app, shell_sessions


def _client(monkeypatch, tmp_path, *, api_key: str = ""):
    monkeypatch.setattr("app.auth.SANDBOX_API_KEY", api_key)
    monkeypatch.setattr("app.auth.JWT_PUBLIC_KEY", "")
    monkeypatch.setattr("app.auth.TICKET_TTL_SECONDS", 30)
    monkeypatch.setattr("app.auth._tickets", {})
    monkeypatch.setattr("app.security.WORKSPACE", tmp_path)
    monkeypatch.setattr("app.main.WORKSPACE", tmp_path)
    monkeypatch.setattr("app.shell_sessions.WORKSPACE", tmp_path)
    _close_existing_shell_sessions()
    return TestClient(app)


def _data(response):
    assert response.status_code == 200
    return response.json()["data"]


def _close_existing_shell_sessions():
    for session_id in list(shell_sessions.list()):
        shell_sessions.close(session_id)
    with shell_sessions._lock:
        shell_sessions._sessions.clear()


def test_shell_session_stats_counts_sessions_without_mutating_them(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    first = _data(client.post("/shell/sessions/create", json={"id": "stats-one", "exec_dir": "."}))
    second = _data(client.post("/shell/sessions/create", json={"id": "stats-two", "exec_dir": "."}))

    stats = _data(client.get("/shell/sessions/stats"))
    listed = _data(client.get("/shell/sessions"))

    assert stats["total_sessions"] == 2
    assert stats["active_sessions"] == 0
    assert stats["idle_sessions"] == 2
    assert stats["max_sessions"] == shell_sessions.max_sessions
    assert stats["session_timeout"] == shell_sessions.idle_timeout_seconds
    assert stats["usage_ratio"] == 2 / shell_sessions.max_sessions
    assert set(listed["sessions"]) == {first["session_id"], second["session_id"]}


def test_shell_session_stats_tracks_running_session_as_active(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    started = _data(
        client.post(
            "/shell/exec",
            json={
                "id": "stats-running",
                "command": f'"{sys.executable}" -c "import time; time.sleep(5)"',
                "async_mode": True,
            },
        )
    )

    stats = _data(client.get("/shell/sessions/stats"))

    assert started["status"] == "running"
    assert stats["total_sessions"] == 1
    assert stats["active_sessions"] == 1
    assert stats["idle_sessions"] == 0


def test_shell_session_update_sets_no_change_timeout(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    created = _data(client.post("/shell/sessions/create", json={"id": "configurable-shell", "exec_dir": "."}))

    updated = _data(
        client.post(
            "/shell/sessions/update",
            json={"id": created["session_id"], "no_change_timeout": 7},
        )
    )
    listed = _data(client.get("/shell/sessions"))

    assert updated["session_id"] == created["session_id"]
    assert updated["no_change_timeout"] == 7
    assert listed["sessions"][created["session_id"]]["no_change_timeout"] == 7


def test_shell_session_update_rejects_unknown_session(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)

    response = client.post(
        "/shell/sessions/update",
        json={"id": "missing-shell", "no_change_timeout": 7},
    )

    assert response.status_code == 404
    assert response.json()["success"] is False


def test_shell_session_update_rejects_negative_no_change_timeout(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    created = _data(client.post("/shell/sessions/create", json={"id": "negative-timeout", "exec_dir": "."}))

    response = client.post(
        "/shell/sessions/update",
        json={"id": created["session_id"], "no_change_timeout": -1},
    )

    assert response.status_code == 422
    assert response.json()["success"] is False


def test_shell_terminal_url_creates_session_and_ticket_url_without_leaking_api_key(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path, api_key="secret")

    result = _data(client.get("/shell/terminal-url", headers={"Authorization": "Bearer secret"}))
    parsed = urlparse(result["url"])
    query = parse_qs(parsed.query)

    assert parsed.path == "/shell/ws"
    assert parsed.scheme in {"ws", "wss"}
    assert query["session_id"] == [result["session_id"]]
    assert query["ticket"]
    assert "secret" not in result["url"]
    assert result["expires_in"] == 30

    listed = _data(client.get("/shell/sessions", headers={"Authorization": "Bearer secret"}))
    assert result["session_id"] in listed["sessions"]

    with client.websocket_connect(f"{parsed.path}?{parsed.query}") as websocket:
        hello = websocket.receive_json()

    assert hello["type"] == "session"
    assert hello["session_id"] == result["session_id"]
