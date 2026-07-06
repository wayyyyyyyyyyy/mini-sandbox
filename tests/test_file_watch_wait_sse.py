import json
import sys
import threading
import time

import pytest
from fastapi.testclient import TestClient

from app.file_watch import FileWatchManager
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


def _poll(client, watcher_id, cursor=0, limit=100):
    return _data(client.post(f"/file/watch/{watcher_id}/poll", json={"cursor": cursor, "limit": limit}))


def test_file_watch_wait_returns_create_when_target_already_exists(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    (tmp_path / "ready.json").write_text("{}", encoding="utf-8")

    result = _data(
        client.post(
            "/file/watch/wait",
            json={"path": "ready.json", "timeout": 1, "event_types": ["create"]},
        )
    )

    assert result["event"]["type"] == "create"
    assert result["event"]["path"] == "ready.json"
    assert result["event"]["relative_path"] == "ready.json"
    assert result["event"]["is_dir"] is False
    assert result["event"]["size"] == 2


def test_file_watch_wait_detects_write_after_call(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    target = tmp_path / "delayed.txt"
    target.write_text("before", encoding="utf-8")

    def update_file():
        time.sleep(0.05)
        target.write_text("after", encoding="utf-8")

    thread = threading.Thread(target=update_file)
    thread.start()
    result = _data(
        client.post(
            "/file/watch/wait",
            json={"path": "delayed.txt", "timeout": 2, "event_types": ["write"]},
        )
    )
    thread.join(timeout=1)

    assert result["event"]["type"] == "write"
    assert result["event"]["path"] == "delayed.txt"
    assert result["event"]["size"] == 5


def test_file_watch_wait_detects_remove(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    target = tmp_path / "gone.txt"
    target.write_text("temporary", encoding="utf-8")

    def remove_file():
        time.sleep(0.05)
        target.unlink()

    thread = threading.Thread(target=remove_file)
    thread.start()
    result = _data(
        client.post(
            "/file/watch/wait",
            json={"path": "gone.txt", "timeout": 2, "event_types": ["remove"]},
        )
    )
    thread.join(timeout=1)

    assert result["event"]["type"] == "remove"
    assert result["event"]["path"] == "gone.txt"
    assert result["event"]["mtime"] is None
    assert result["event"]["size"] == 0


def test_file_watch_wait_times_out_without_event(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)

    response = client.post(
        "/file/watch/wait",
        json={"path": "missing.txt", "timeout": 1, "event_types": ["write"]},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["data"] == {"event": None}


def test_file_watch_wait_rejects_workspace_escape(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)

    response = client.post(
        "/file/watch/wait",
        json={"path": "../outside.txt", "timeout": 1},
    )

    assert response.status_code == 403
    assert response.json()["success"] is False


def test_file_watch_sse_streams_started_and_file_change(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    watcher = _watch(client)

    def create_file():
        time.sleep(0.05)
        (tmp_path / "from-sse.txt").write_text("event\n", encoding="utf-8")

    thread = threading.Thread(target=create_file)
    thread.start()

    with client.stream("GET", f"/file/watch/{watcher['watcher_id']}/events?timeout=2") as response:
        body = response.read().decode("utf-8")

    thread.join(timeout=1)

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")

    events = _parse_sse(body)
    assert events[0]["event"] == "watch_started"
    assert events[0]["data"]["watcher_id"] == watcher["watcher_id"]
    changed = [event for event in events if event["event"] == "file_change"]
    assert changed
    assert changed[0]["id"] == f"{watcher['watcher_id']}:1"
    assert changed[0]["data"]["path"] == "from-sse.txt"
    assert changed[0]["data"]["type"] == "created"


def test_file_watch_sse_returns_wrapped_404_for_unknown_watcher(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)

    response = client.get("/file/watch/fw_missing/events")

    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/json")
    assert response.json()["success"] is False


def test_file_watch_poll_reports_overflow_when_cursor_is_older_than_retained_history(monkeypatch, tmp_path):
    monkeypatch.setattr("app.file_watch.MAX_FILE_WATCH_EVENTS", 2)
    client = _client(monkeypatch, tmp_path)
    watcher = _watch(client)

    for index in range(3):
        (tmp_path / f"event-{index}.txt").write_text(str(index), encoding="utf-8")
        _poll(client, watcher["watcher_id"], cursor=index)

    result = _poll(client, watcher["watcher_id"], cursor=0)

    assert result["overflow"] is True
    assert result["cursor"] == 3
    assert [event["seq"] for event in result["events"]] == [2, 3]


def test_file_watch_poll_does_not_overflow_for_retained_cursor(monkeypatch, tmp_path):
    monkeypatch.setattr("app.file_watch.MAX_FILE_WATCH_EVENTS", 2)
    client = _client(monkeypatch, tmp_path)
    watcher = _watch(client)

    for index in range(3):
        (tmp_path / f"retained-{index}.txt").write_text(str(index), encoding="utf-8")
        _poll(client, watcher["watcher_id"], cursor=index)

    result = _poll(client, watcher["watcher_id"], cursor=2)

    assert result["overflow"] is False
    assert result["cursor"] == 3
    assert [event["seq"] for event in result["events"]] == [3]


def test_file_watch_sse_emits_heartbeat_without_advancing_cursor(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    watcher = _watch(client)

    with client.stream(
        "GET",
        f"/file/watch/{watcher['watcher_id']}/events?timeout=0.2&heartbeat_interval=0.05",
    ) as response:
        body = response.read().decode("utf-8")

    assert response.status_code == 200
    events = _parse_sse(body)
    heartbeats = [event for event in events if event["event"] == "heartbeat"]
    assert heartbeats
    assert heartbeats[0]["data"]["watcher_id"] == watcher["watcher_id"]
    assert heartbeats[0]["data"]["cursor"] == 0
    assert not [event for event in events if event["event"] == "file_change"]


def test_file_watch_sse_resumes_from_last_event_id_query(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    watcher = _watch(client)

    (tmp_path / "first.txt").write_text("first", encoding="utf-8")
    first = _poll(client, watcher["watcher_id"], cursor=0)
    assert first["cursor"] == 1

    def create_second_file():
        time.sleep(0.05)
        (tmp_path / "second.txt").write_text("second", encoding="utf-8")

    thread = threading.Thread(target=create_second_file)
    thread.start()
    with client.stream(
        "GET",
        f"/file/watch/{watcher['watcher_id']}/events?timeout=2&last_event_id={watcher['watcher_id']}:1",
    ) as response:
        body = response.read().decode("utf-8")
    thread.join(timeout=1)

    events = _parse_sse(body)
    changed = [event for event in events if event["event"] == "file_change"]
    assert changed
    assert changed[0]["id"] == f"{watcher['watcher_id']}:2"
    assert changed[0]["data"]["path"] == "second.txt"


def test_file_watch_uses_linux_inotify_backend_when_available(tmp_path):
    if sys.platform != "linux":
        pytest.skip("Linux inotify backend is only available on Linux")
    manager = FileWatchManager()
    watcher = manager.create(root=tmp_path, recursive=True, exclude=[], include_patterns=[])

    try:
        assert watcher.native is not None
        assert watcher.native.__class__.__name__ == "LinuxInotifyWatcher"
    finally:
        manager.delete(watcher.watcher_id)


def _parse_sse(body: str):
    events = []
    for block in body.strip().split("\n\n"):
        event = {}
        data_lines = []
        for line in block.splitlines():
            if line.startswith("id: "):
                event["id"] = line.removeprefix("id: ")
            elif line.startswith("event: "):
                event["event"] = line.removeprefix("event: ")
            elif line.startswith("data: "):
                data_lines.append(line.removeprefix("data: "))
        if data_lines:
            event["data"] = json.loads("\n".join(data_lines))
        if event:
            events.append(event)
    return events
