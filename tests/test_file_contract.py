import base64

import pytest
from fastapi.testclient import TestClient

from app.main import app


def _client(monkeypatch, tmp_path):
    monkeypatch.setattr("app.auth.SANDBOX_API_KEY", "")
    monkeypatch.setattr("app.security.WORKSPACE", tmp_path)
    monkeypatch.setattr("app.main.WORKSPACE", tmp_path)
    return TestClient(app)


def test_file_read_supports_line_ranges(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    (tmp_path / "notes.txt").write_text("zero\none\ntwo\nthree\n", encoding="utf-8")

    response = client.post(
        "/file/read",
        json={
            "path": "notes.txt",
            "start_line": 1,
            "end_line": 3,
        },
    )

    assert response.status_code == 200
    body = response.json()["data"]
    assert body["content"] == "one\ntwo\n"
    assert body["line_count"] == 2


def test_file_write_supports_append_and_newline_controls(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    (tmp_path / "log.txt").write_text("first", encoding="utf-8")

    response = client.post(
        "/file/write",
        json={
            "path": "log.txt",
            "content": "second",
            "append": True,
            "leading_newline": True,
            "trailing_newline": True,
        },
    )

    assert response.status_code == 200
    assert (tmp_path / "log.txt").read_text(encoding="utf-8") == "first\nsecond\n"


def test_file_write_supports_base64_content(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    payload = b"\x00\x01sandbox-bytes"

    response = client.post(
        "/file/write",
        json={
            "path": "blob.bin",
            "content": base64.b64encode(payload).decode("ascii"),
            "encoding": "base64",
        },
    )

    assert response.status_code == 200
    assert (tmp_path / "blob.bin").read_bytes() == payload


def test_file_list_supports_recursive_entries(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("print('ok')\n", encoding="utf-8")
    (tmp_path / "src" / "nested").mkdir()
    (tmp_path / "src" / "nested" / "mod.py").write_text("x = 1\n", encoding="utf-8")

    response = client.post(
        "/file/list",
        json={
            "path": "src",
            "recursive": True,
            "include_size": True,
        },
    )

    assert response.status_code == 200
    paths = {entry["path"] for entry in response.json()["data"]["entries"]}
    assert "src/app.py" in paths
    assert "src/nested/mod.py" in paths


def test_file_list_can_hide_hidden_files(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    (tmp_path / "visible.txt").write_text("visible", encoding="utf-8")
    (tmp_path / ".hidden.txt").write_text("hidden", encoding="utf-8")

    response = client.post(
        "/file/list",
        json={
            "path": ".",
            "show_hidden": False,
        },
    )

    assert response.status_code == 200
    paths = {entry["path"] for entry in response.json()["data"]["entries"]}
    assert "visible.txt" in paths
    assert ".hidden.txt" not in paths
