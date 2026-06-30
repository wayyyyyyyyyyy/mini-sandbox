from fastapi.testclient import TestClient

from app.main import app


def _client(monkeypatch, tmp_path):
    monkeypatch.setattr("app.auth.SANDBOX_API_KEY", "")
    monkeypatch.setattr("app.security.WORKSPACE", tmp_path)
    monkeypatch.setattr("app.main.WORKSPACE", tmp_path)
    return TestClient(app)


def test_file_upload_writes_file_and_wraps_result(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)

    response = client.post(
        "/file/upload",
        data={"path": "notes/uploaded.txt"},
        files={"file": ("uploaded.txt", b"hello upload\n", "text/plain")},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["data"] == {
        "path": "notes/uploaded.txt",
        "bytes": len(b"hello upload\n"),
    }
    assert (tmp_path / "notes" / "uploaded.txt").read_bytes() == b"hello upload\n"


def test_file_upload_can_overwrite_existing_file(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    target = tmp_path / "notes.txt"
    target.write_text("old\n", encoding="utf-8")

    response = client.post(
        "/file/upload",
        data={"path": "notes.txt"},
        files={"file": ("notes.txt", b"new\n", "text/plain")},
    )

    assert response.status_code == 200
    assert response.json()["data"]["bytes"] == len(b"new\n")
    assert target.read_bytes() == b"new\n"


def test_file_upload_rejects_workspace_escape(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)

    response = client.post(
        "/file/upload",
        data={"path": "../outside.txt"},
        files={"file": ("outside.txt", b"escape", "text/plain")},
    )

    assert response.status_code == 403
    assert response.json()["success"] is False
    assert "path escapes workspace" in response.json()["message"]


def test_file_download_streams_file_without_wrapper(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    (tmp_path / "artifact.txt").write_bytes(b"download me\n")

    response = client.get("/file/download", params={"path": "artifact.txt"})

    assert response.status_code == 200
    assert response.content == b"download me\n"
    assert response.headers["content-type"].startswith("text/plain")
    assert "attachment" in response.headers["content-disposition"]


def test_file_download_returns_wrapped_404_for_missing_file(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)

    response = client.get("/file/download", params={"path": "missing.txt"})

    assert response.status_code == 404
    body = response.json()
    assert body["success"] is False
    assert body["data"] is None
    assert "file not found" in body["message"]


def test_file_download_rejects_workspace_escape(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)

    response = client.get("/file/download", params={"path": "../outside.txt"})

    assert response.status_code == 403
    assert response.json()["success"] is False
    assert "path escapes workspace" in response.json()["message"]
