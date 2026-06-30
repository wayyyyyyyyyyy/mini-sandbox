import sys

from fastapi.testclient import TestClient

from app.main import app


def _client(monkeypatch, tmp_path):
    monkeypatch.setattr("app.auth.SANDBOX_API_KEY", "")
    monkeypatch.setattr("app.security.WORKSPACE", tmp_path)
    monkeypatch.setattr("app.main.WORKSPACE", tmp_path)
    return TestClient(app)


def _data(response):
    assert response.status_code == 200
    return response.json()["data"]


def test_shell_exec_returns_single_terminal_output(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)

    body = _data(client.post("/shell/exec", json={"command": f'"{sys.executable}" -c "print(\'shell-ok\')"'}))

    assert body["session_id"].startswith("sh_")
    assert body["command"].endswith("print('shell-ok')\"")
    assert body["status"] == "completed"
    assert "shell-ok" in body["output"]
    assert "stdout" not in body
    assert "stderr" not in body
    assert body["exit_code"] == 0


def test_shell_session_preserves_cd_and_environment(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    (tmp_path / "work").mkdir()

    created = _data(client.post("/shell/sessions/create", json={"exec_dir": "."}))
    session_id = created["session_id"]

    first = _data(
        client.post(
            "/shell/exec",
            json={
                "id": session_id,
                "command": "cd work && export DEMO_FLAG=shell-demo",
            },
        )
    )
    second = _data(
        client.post(
            "/shell/exec",
            json={
                "id": session_id,
                "command": f'"{sys.executable}" -c "import os, pathlib; print(pathlib.Path.cwd().name); print(os.environ.get(\'DEMO_FLAG\'))"',
            },
        )
    )

    assert first["status"] == "completed"
    assert "work" in second["output"]
    assert "shell-demo" in second["output"]


def test_shell_async_wait_and_view_return_terminal_snapshot(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)

    started = _data(
        client.post(
            "/shell/exec",
            json={
                "command": f'"{sys.executable}" -c "import time; print(\'start\', flush=True); time.sleep(0.2); print(\'done\', flush=True)"',
                "async_mode": True,
            },
        )
    )

    assert started["status"] == "running"
    waited = _data(client.post("/shell/wait", json={"id": started["session_id"], "seconds": 2}))
    viewed = _data(client.post("/shell/view", json={"id": started["session_id"]}))

    assert waited["status"] == "completed"
    assert viewed["session_id"] == started["session_id"]
    assert viewed["status"] == "completed"
    assert "start" in viewed["output"]
    assert "done" in viewed["output"]


def test_shell_write_sends_input_to_interactive_process(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)

    started = _data(
        client.post(
            "/shell/exec",
            json={
                "command": f'"{sys.executable}" -u -c "print(input())"',
                "async_mode": True,
            },
        )
    )

    write_result = _data(
        client.post(
            "/shell/write",
            json={
                "id": started["session_id"],
                "input": "hello shell",
                "press_enter": True,
            },
        )
    )
    waited = _data(client.post("/shell/wait", json={"id": started["session_id"], "seconds": 2}))
    viewed = _data(client.post("/shell/view", json={"id": started["session_id"]}))

    assert write_result["status"] == "running"
    assert waited["status"] == "completed"
    assert "hello shell" in viewed["output"]


def test_shell_kill_stops_running_process(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)

    started = _data(
        client.post(
            "/shell/exec",
            json={
                "command": f'"{sys.executable}" -c "import time; time.sleep(30)"',
                "async_mode": True,
            },
        )
    )

    killed = _data(client.post("/shell/kill", json={"id": started["session_id"]}))
    viewed = _data(client.post("/shell/view", json={"id": started["session_id"]}))

    assert killed["status"] == "killed"
    assert viewed["status"] == "killed"


def test_shell_sessions_list_and_close(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    created = _data(client.post("/shell/sessions/create", json={"id": "custom-shell", "exec_dir": "."}))

    listed = _data(client.get("/shell/sessions"))
    response = client.delete(f"/shell/sessions/{created['session_id']}")

    assert created["session_id"] == "custom-shell"
    assert "custom-shell" in listed["sessions"]
    assert response.status_code == 200
    assert response.json()["data"]["success"] is True
