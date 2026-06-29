import sys

import pytest
from fastapi.testclient import TestClient

from app.main import app


def _client(monkeypatch, tmp_path):
    monkeypatch.setattr("app.auth.SANDBOX_API_KEY", "")
    monkeypatch.setattr("app.security.WORKSPACE", tmp_path)
    monkeypatch.setattr("app.main.WORKSPACE", tmp_path)
    return TestClient(app)


def test_bash_create_session_returns_ready_session(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)

    response = client.post("/bash/sessions/create", json={"exec_dir": "."})

    assert response.status_code == 200
    body = response.json()
    data = _unwrap(body)
    assert data["session_id"].startswith("s_")
    assert data["status"] == "ready"
    assert data["working_dir"]
    assert data["created_at"]
    assert data["last_used_at"]
    assert data["command_count"] == 0


def test_bash_sessions_list_session_state_not_command_state(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    created = client.post("/bash/sessions/create", json={}).json()
    session = _unwrap(created)

    response = client.get("/bash/sessions")

    assert response.status_code == 200
    body = response.json()
    sessions = _unwrap(body, "sessions")
    listed = {item["session_id"]: item for item in sessions}
    assert listed[session["session_id"]]["status"] == "ready"
    assert "stdout" not in listed[session["session_id"]]
    assert "stderr" not in listed[session["session_id"]]


def test_bash_exec_reuses_session_but_creates_new_commands(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    session = _unwrap(client.post("/bash/sessions/create", json={}).json())

    first = _unwrap(
        client.post(
            "/bash/exec",
            json={
                "session_id": session["session_id"],
                "command": f'"{sys.executable}" -c "print(1)"',
            },
        ).json()
    )
    second = _unwrap(
        client.post(
            "/bash/exec",
            json={
                "session_id": session["session_id"],
                "command": f'"{sys.executable}" -c "print(2)"',
            },
        ).json()
    )

    assert first["session_id"] == session["session_id"]
    assert second["session_id"] == session["session_id"]
    assert first["command_id"] != second["command_id"]


def test_bash_output_returns_incremental_output_and_command_info(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    started = _unwrap(
        client.post(
            "/bash/exec",
            json={
                "command": f'"{sys.executable}" -c "print(\'contract-output\')"',
                "timeout": 0.001,
            },
        ).json()
    )

    response = client.post(
        "/bash/output",
        json={
            "session_id": started["session_id"],
            "command_id": started["command_id"],
            "offset": 0,
            "stderr_offset": 0,
            "wait": True,
            "wait_timeout": 5,
        },
    )

    assert response.status_code == 200
    body = _unwrap(response.json())
    assert "contract-output" in body["stdout"]
    assert body["offset"] >= len("contract-output\n")
    assert body["command_info"]["command_id"] == started["command_id"]
    assert body["command_info"]["status"] in {"running", "completed"}


def test_bash_exec_sync_mode_waits_for_fast_command(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)

    response = client.post(
        "/bash/exec",
        json={
            "command": f'"{sys.executable}" -c "print(\'fast-done\')"',
            "timeout": 5,
            "hard_timeout": 5,
        },
    )

    assert response.status_code == 200
    body = _unwrap(response.json())
    assert body["status"] == "completed"
    assert body["exit_code"] == 0
    assert "fast-done" in body["stdout"]


def test_bash_close_session_marks_session_closed(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    session = _unwrap(client.post("/bash/sessions/create", json={}).json())

    response = client.post(f"/bash/sessions/{session['session_id']}/close")

    assert response.status_code == 200
    sessions = _unwrap(client.get("/bash/sessions").json(), "sessions")
    listed = {item["session_id"]: item for item in sessions}
    assert listed[session["session_id"]]["status"] == "closed"


def _unwrap(body: dict, key: str | None = None):
    data = body.get("data", body)
    if key is not None and isinstance(data, dict):
        return data[key]
    return data
