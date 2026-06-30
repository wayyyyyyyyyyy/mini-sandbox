import sys

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.bash_sessions import BashSessionManager
from app.file_watch import FileWatchManager
from app.main import app
from app.shell_sessions import ShellSessionManager


def _client(monkeypatch, tmp_path):
    monkeypatch.setattr("app.auth.SANDBOX_API_KEY", "")
    monkeypatch.setattr("app.security.WORKSPACE", tmp_path)
    monkeypatch.setattr("app.main.WORKSPACE", tmp_path)
    return TestClient(app)


def test_bash_session_manager_enforces_session_limit(tmp_path):
    manager = BashSessionManager(max_sessions=1)
    manager.create_session(session_id="one", exec_dir=tmp_path)

    with pytest.raises(HTTPException) as exc:
        manager.create_session(session_id="two", exec_dir=tmp_path)

    assert exc.value.status_code == 429
    assert "bash session limit exceeded" in exc.value.detail


def test_shell_session_manager_enforces_session_limit(tmp_path):
    manager = ShellSessionManager(max_sessions=1)
    manager.create_session(session_id="one", exec_dir=tmp_path)

    with pytest.raises(HTTPException) as exc:
        manager.create_session(session_id="two", exec_dir=tmp_path)

    assert exc.value.status_code == 429
    assert "shell session limit exceeded" in exc.value.detail


def test_file_watch_manager_enforces_watcher_limit(tmp_path):
    manager = FileWatchManager(max_watchers=1)
    manager.create(root=tmp_path, recursive=True, exclude=[], include_patterns=[])

    with pytest.raises(HTTPException) as exc:
        manager.create(root=tmp_path, recursive=True, exclude=[], include_patterns=[])

    assert exc.value.status_code == 429
    assert "file watcher limit exceeded" in exc.value.detail


def test_shell_session_manager_can_cleanup_idle_sessions(tmp_path, monkeypatch):
    manager = ShellSessionManager(idle_timeout_seconds=1)
    session = manager.create_session(session_id="idle", exec_dir=tmp_path)
    session.last_used_at = session.last_used_at.replace(year=2000)

    closed = manager.cleanup_idle_sessions()

    assert closed == ["idle"]
    assert manager.list() == {}


def test_shell_output_buffer_is_truncated(monkeypatch, tmp_path):
    monkeypatch.setattr("app.shell_sessions.MAX_SHELL_OUTPUT_CHARS", 20)
    client = _client(monkeypatch, tmp_path)

    response = client.post(
        "/shell/exec",
        json={"command": f'"{sys.executable}" -c "print(\'x\' * 80)"'},
    )

    assert response.status_code == 200
    output = response.json()["data"]["output"]
    assert len(output) <= 20
    assert output.endswith("x\r\n") or output.endswith("x\n")
