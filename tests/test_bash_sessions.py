import time
import sys

from fastapi.testclient import TestClient

from app.main import app


def _client(monkeypatch, tmp_path):
    monkeypatch.setattr("app.auth.SANDBOX_API_KEY", "")
    monkeypatch.setattr("app.security.WORKSPACE", tmp_path)
    monkeypatch.setattr("app.main.WORKSPACE", tmp_path)
    return TestClient(app)


def test_bash_exec_returns_session_and_command_ids(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)

    response = client.post("/bash/exec", json={"command": f'"{sys.executable}" --version'})

    assert response.status_code == 200
    body = response.json()
    assert body["session_id"].startswith("s_")
    assert body["command_id"].startswith("c_")
    assert body["status"] in {"running", "completed"}


def test_bash_output_reads_incrementally(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)

    created = client.post(
        "/bash/exec",
        json={"command": f'"{sys.executable}" -c "print(\'first\'); print(\'second\')"'},
    ).json()
    session_id = created["session_id"]

    body = _wait_for_completion(client, session_id)
    assert "first" in body["stdout"]
    assert "second" in body["stdout"]

    next_body = client.post(
        "/bash/output",
        json={
            "session_id": session_id,
            "offset": body["stdout_offset"],
            "stderr_offset": body["stderr_offset"],
        },
    ).json()
    assert next_body["stdout"] == ""
    assert next_body["stderr"] == ""


def test_bash_kill_stops_running_process(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)

    created = client.post(
        "/bash/exec",
        json={"command": f'"{sys.executable}" -c "import time; time.sleep(30)"'},
    ).json()

    response = client.post("/bash/kill", json={"session_id": created["session_id"]})

    assert response.status_code == 200
    assert response.json()["status"] == "killed"


def test_bash_hard_timeout_marks_command_timed_out(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)

    created = client.post(
        "/bash/exec",
        json={
            "command": f'"{sys.executable}" -c "import time; time.sleep(30)"',
            "hard_timeout": 0.1,
        },
    ).json()

    body = _wait_for_status(client, created["session_id"], "timed_out")

    assert body["exit_code"] is not None


def _wait_for_completion(client: TestClient, session_id: str) -> dict:
    return _wait_for_status(client, session_id, "completed")


def _wait_for_status(client: TestClient, session_id: str, status: str) -> dict:
    last_body = {}
    for _ in range(30):
        response = client.post("/bash/output", json={"session_id": session_id})
        assert response.status_code == 200
        last_body = response.json()
        if last_body["status"] == status:
            return last_body
        time.sleep(0.1)
    raise AssertionError(f"command did not reach {status}: {last_body}")
