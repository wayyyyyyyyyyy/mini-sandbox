import time

from fastapi.testclient import TestClient

from app.main import app


def _client(monkeypatch, tmp_path):
    monkeypatch.setattr("app.auth.SANDBOX_API_KEY", "")
    monkeypatch.setattr("app.security.WORKSPACE", tmp_path)
    monkeypatch.setattr("app.main.WORKSPACE", tmp_path)
    return TestClient(app)


def _data(response):
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    return body["data"]


def _watch(client, path=".", recursive=True):
    return _data(client.post("/file/watch", json={"path": path, "recursive": recursive}))


def _poll(client, watcher_id, cursor=0):
    return _data(client.post(f"/file/watch/{watcher_id}/poll", json={"cursor": cursor}))


def test_file_watch_detects_created_file(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    watcher = _watch(client)

    (tmp_path / "created.txt").write_text("hello\n", encoding="utf-8")
    result = _poll(client, watcher["watcher_id"])

    assert result["watcher_id"] == watcher["watcher_id"]
    assert result["cursor"] == 1
    assert result["overflow"] is False
    assert result["events"] == [
        {
            "seq": 1,
            "type": "created",
            "path": "created.txt",
            "relative_path": "created.txt",
            "is_dir": False,
            "mtime": result["events"][0]["mtime"],
            "size": 6,
            "timestamp": result["events"][0]["timestamp"],
        }
    ]


def test_file_watch_detects_modified_file(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    (tmp_path / "notes.txt").write_text("first\n", encoding="utf-8")
    watcher = _watch(client)

    time.sleep(0.02)
    (tmp_path / "notes.txt").write_text("second\n", encoding="utf-8")
    result = _poll(client, watcher["watcher_id"])

    assert [event["type"] for event in result["events"]] == ["modified"]
    assert result["events"][0]["path"] == "notes.txt"
    assert result["events"][0]["size"] == 7


def test_file_watch_detects_deleted_file(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    target = tmp_path / "gone.txt"
    target.write_text("temporary\n", encoding="utf-8")
    watcher = _watch(client)

    target.unlink()
    result = _poll(client, watcher["watcher_id"])

    assert [event["type"] for event in result["events"]] == ["deleted"]
    assert result["events"][0]["path"] == "gone.txt"
    assert result["events"][0]["size"] == 0
    assert result["events"][0]["mtime"] is None


def test_file_watch_cursor_does_not_repeat_consumed_events(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    watcher = _watch(client)

    (tmp_path / "one.txt").write_text("one", encoding="utf-8")
    first = _poll(client, watcher["watcher_id"])
    second = _poll(client, watcher["watcher_id"], cursor=first["cursor"])

    assert first["cursor"] == 1
    assert len(first["events"]) == 1
    assert second["cursor"] == 1
    assert second["events"] == []


def test_file_watch_honors_recursive_flag(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    (tmp_path / "nested").mkdir()
    watcher = _watch(client, recursive=False)

    (tmp_path / "nested" / "inner.txt").write_text("inner", encoding="utf-8")
    (tmp_path / "root.txt").write_text("root", encoding="utf-8")
    result = _poll(client, watcher["watcher_id"])

    paths = {event["path"] for event in result["events"]}
    assert "root.txt" in paths
    assert "nested/inner.txt" not in paths


def test_file_watch_can_be_deleted(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    watcher = _watch(client)

    deleted = _data(client.delete(f"/file/watch/{watcher['watcher_id']}"))
    response = client.post(f"/file/watch/{watcher['watcher_id']}/poll", json={"cursor": 0})

    assert deleted["watcher_id"] == watcher["watcher_id"]
    assert deleted["closed"] is True
    assert response.status_code == 404
    assert response.json()["success"] is False


def test_file_watch_rejects_workspace_escape(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)

    response = client.post("/file/watch", json={"path": "../outside", "recursive": True})

    assert response.status_code == 403
    assert response.json()["success"] is False
