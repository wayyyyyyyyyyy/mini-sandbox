import base64
import sys
import threading
import time
from pathlib import Path

import pytest
import httpx
from fastapi.testclient import TestClient

from app.main import app, browser_sessions

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "sdk" / "python"))

from mini_agent_sandbox import SandboxAPIError, SandboxClient  # noqa: E402


def _client(monkeypatch, tmp_path, api_key="secret"):
    monkeypatch.setattr("app.auth.SANDBOX_API_KEY", api_key)
    monkeypatch.setattr("app.security.WORKSPACE", tmp_path)
    monkeypatch.setattr("app.main.WORKSPACE", tmp_path)
    browser_sessions.close()
    http_client = TestClient(app)
    return SandboxClient(
        base_url="http://testserver",
        api_key=api_key,
        http_client=http_client,
    )


def _page_url(html: str) -> str:
    encoded = base64.b64encode(html.encode("utf-8")).decode("ascii")
    return f"data:text/html;base64,{encoded}"


def test_sdk_context_unwraps_response_wrapper(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)

    context = client.context()

    assert context["workspace"] == str(tmp_path)
    assert "success" not in context


def test_sdk_file_write_read_and_replace(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)

    written = client.file.write("notes.txt", "hello sandbox\n")
    read_before = client.file.read("notes.txt")
    replaced = client.file.replace("notes.txt", "hello", "hi")
    read_after = client.file.read("notes.txt")

    assert written["path"] == "notes.txt"
    assert read_before["content"] == "hello sandbox\n"
    assert replaced == {"path": "notes.txt", "replaced": 1, "changed": True}
    assert read_after["content"] == "hi sandbox\n"


def test_sdk_file_download_returns_binary_content(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    (tmp_path / "artifact.bin").write_bytes(b"\x00sandbox artifact\xff")

    content = client.file.download("artifact.bin")

    assert content == b"\x00sandbox artifact\xff"


def test_sdk_file_download_raises_wrapper_error_for_missing_file(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)

    with pytest.raises(SandboxAPIError) as exc_info:
        client.file.download("missing.bin")

    assert exc_info.value.status_code == 404
    assert exc_info.value.message == "file not found: missing.bin"


def test_sdk_file_upload_writes_binary_content(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)

    result = client.file.upload("artifacts/result.bin", b"\x00sandbox artifact\xff")

    assert result == {"path": "artifacts/result.bin", "bytes": 18}
    assert (tmp_path / "artifacts" / "result.bin").read_bytes() == b"\x00sandbox artifact\xff"


def test_sdk_file_upload_sends_multipart_metadata():
    requests = []

    class RecordingClient:
        def request(self, *args, **kwargs):
            requests.append((args, kwargs))
            return httpx.Response(
                200,
                json={"success": True, "data": {"path": "reports/data.csv", "bytes": 3}},
            )

    client = SandboxClient(
        base_url="http://testserver",
        api_key="secret",
        http_client=RecordingClient(),
    )

    result = client.file.upload(
        "reports/data.csv",
        b"a,b",
        filename="source.csv",
        content_type="text/csv",
    )

    assert result == {"path": "reports/data.csv", "bytes": 3}
    assert requests == [
        (
            ("POST", "http://testserver/file/upload"),
            {
                "data": {"path": "reports/data.csv"},
                "files": {"file": ("source.csv", b"a,b", "text/csv")},
                "headers": {"X-Sandbox-Api-Key": "secret"},
            },
        )
    ]


def test_sdk_file_upload_raises_wrapper_error_for_workspace_escape(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)

    with pytest.raises(SandboxAPIError) as exc_info:
        client.file.upload("../outside.bin", b"escape")

    assert exc_info.value.status_code == 403
    assert "path escapes workspace" in exc_info.value.message


def test_sdk_file_watch_poll(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    watcher = client.file.watch(".")

    (tmp_path / "created.txt").write_text("created\n", encoding="utf-8")
    polled = client.file.watch_poll(watcher["watcher_id"], cursor=watcher["cursor"])

    assert polled["watcher_id"] == watcher["watcher_id"]
    assert polled["cursor"] == 1
    assert polled["events"][0]["type"] == "created"
    assert polled["events"][0]["path"] == "created.txt"


def test_sdk_file_watch_wait_detects_write(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    target = tmp_path / "delayed.txt"
    target.write_text("before", encoding="utf-8")

    def update_file():
        time.sleep(0.05)
        target.write_text("after", encoding="utf-8")

    thread = threading.Thread(target=update_file)
    thread.start()
    result = client.file.wait("delayed.txt", timeout=2, event_types=["write"])
    thread.join(timeout=1)

    assert result["event"]["type"] == "write"
    assert result["event"]["path"] == "delayed.txt"


def test_sdk_file_watch_events_parses_sse_changes(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    watcher = client.file.watch(".")

    def create_file():
        time.sleep(0.05)
        (tmp_path / "from-sdk-sse.txt").write_text("event\n", encoding="utf-8")

    thread = threading.Thread(target=create_file)
    thread.start()
    events = client.file.watch_events(watcher["watcher_id"], timeout=2)
    thread.join(timeout=1)

    assert events[0]["event"] == "watch_started"
    changed = [event for event in events if event["event"] == "file_change"]
    assert changed
    assert changed[0]["id"] == f"{watcher['watcher_id']}:1"
    assert changed[0]["data"]["path"] == "from-sdk-sse.txt"


def test_sdk_file_watch_events_stream_yields_changes_incrementally(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    watcher = client.file.watch(".")

    def create_file():
        time.sleep(0.05)
        (tmp_path / "from-sdk-stream.txt").write_text("event\n", encoding="utf-8")

    thread = threading.Thread(target=create_file)
    thread.start()
    events = client.file.watch_events_stream(watcher["watcher_id"], timeout=2)

    started = next(events)
    changed = next(events)
    thread.join(timeout=1)

    assert started == {
        "event": "watch_started",
        "data": {"watcher_id": watcher["watcher_id"], "cursor": 0},
    }
    assert changed["event"] == "file_change"
    assert changed["id"] == f"{watcher['watcher_id']}:1"
    assert changed["data"]["path"] == "from-sdk-stream.txt"


def test_sdk_file_watch_events_raises_wrapper_error_for_unknown_watcher(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)

    with pytest.raises(SandboxAPIError) as exc_info:
        client.file.watch_events("fw_missing", timeout=0)

    assert exc_info.value.status_code == 404
    assert exc_info.value.message == "file watcher not found: fw_missing"


def test_sdk_file_watch_events_stream_raises_wrapper_error_for_unknown_watcher(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)

    with pytest.raises(SandboxAPIError) as exc_info:
        next(client.file.watch_events_stream("fw_missing", timeout=0))

    assert exc_info.value.status_code == 404
    assert exc_info.value.message == "file watcher not found: fw_missing"


def test_sdk_file_watch_events_stream_uses_incremental_response_and_request_contract():
    requests = []

    class StreamResponse:
        status_code = 200

        @property
        def text(self):
            raise AssertionError("streaming API must not read the complete response body")

        def json(self):
            raise ValueError("SSE responses do not have a JSON body")

        def iter_lines(self):
            yield "event: watch_started"
            yield 'data: {"watcher_id":"fw_test","cursor":0}'
            yield ""
            yield "id: fw_test:1"
            yield "event: file_change"
            yield 'data: {"seq":1,"path":"notes.txt"}'
            yield ""

    class StreamContext:
        def __enter__(self):
            return StreamResponse()

        def __exit__(self, *_exc):
            return False

    class RecordingClient:
        def stream(self, *args, **kwargs):
            requests.append((args, kwargs))
            return StreamContext()

    client = SandboxClient(
        base_url="http://testserver",
        api_key="secret",
        http_client=RecordingClient(),
    )

    events = list(client.file.watch_events_stream("fw_test", timeout=60, last_event_id="fw_test:0"))

    assert events[0]["event"] == "watch_started"
    assert events[1]["data"]["path"] == "notes.txt"
    assert requests == [
        (
            ("GET", "http://testserver/file/watch/fw_test/events"),
            {
                "json": None,
                "params": {"timeout": 60, "heartbeat_interval": 15, "last_event_id": "fw_test:0"},
                "headers": {"X-Sandbox-Api-Key": "secret", "Accept": "text/event-stream"},
                "timeout": 65,
            },
        )
    ]


def test_sdk_file_watch_uses_transport_timeouts_that_cover_long_waits():
    requests = []

    class RecordingClient:
        def request(self, *args, **kwargs):
            requests.append(kwargs)
            if "/events" in args[1]:
                return httpx.Response(200, text="event: watch_started\ndata: {}\n\n")
            return httpx.Response(200, json={"success": True, "data": {"event": None}})

    client = SandboxClient(
        base_url="http://testserver",
        api_key="secret",
        http_client=RecordingClient(),
    )

    client.file.wait("notes.txt", timeout=30)
    client.file.watch_events("fw_test", timeout=60)

    assert requests[0]["timeout"] == 35
    assert requests[1]["timeout"] == 65


def test_sdk_non_watch_requests_keep_the_http_client_default_timeout():
    requests = []

    class RecordingClient:
        def request(self, *args, **kwargs):
            requests.append(kwargs)
            return httpx.Response(200, json={"success": True, "data": {}})

    client = SandboxClient(base_url="http://testserver", http_client=RecordingClient())

    client.context()

    assert "timeout" not in requests[0]


def test_sdk_bash_and_shell_exec(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)

    bash_result = client.bash.exec(f'"{sys.executable}" -c "print(\'sdk-bash\')"', timeout=5, hard_timeout=5)
    shell_result = client.shell.exec(f'"{sys.executable}" -c "print(\'sdk-shell\')"', timeout=5, hard_timeout=5)

    assert bash_result["status"] == "completed"
    assert "sdk-bash" in bash_result["stdout"]
    assert shell_result["status"] == "completed"
    assert "sdk-shell" in shell_result["output"]


def test_sdk_uses_api_key_header(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path, api_key="correct")
    client.api_key = "wrong"

    with pytest.raises(SandboxAPIError) as exc_info:
        client.context()

    assert exc_info.value.status_code == 401
    assert exc_info.value.message == "missing or invalid credentials"
    assert exc_info.value.data is None


def test_sdk_raises_api_error_for_wrapper_errors(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)

    with pytest.raises(SandboxAPIError) as exc_info:
        client.file.read("missing.txt")

    assert exc_info.value.status_code == 404
    assert exc_info.value.message == "file not found: missing.txt"
    assert exc_info.value.data is None


def test_sdk_browser_client_aligns_page_and_screenshot_contracts(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    url = _page_url(
        "<html><body><button id='save' onclick=\"document.body.dataset.clicked='yes'\">Save</button>"
        "<input id='name'></body></html>"
    )

    navigated = client.browser.navigate(url)
    text = client.browser.text()
    evaluated = client.browser.evaluate("() => document.title")
    filled = client.browser.fill("#name", "Way")
    clicked = client.browser.click("#save")
    waited = client.browser.wait_for_selector("#name", timeout=1000)
    screenshot = client.browser.screenshot()

    assert navigated["url"].startswith("data:text/html")
    assert "Save" in text
    assert evaluated == {"result": ""}
    assert filled == {"selector": "#name", "ok": True}
    assert clicked == {"selector": "#save", "ok": True}
    assert waited == {"selector": "#name", "ok": True}
    assert screenshot.startswith(b"\x89PNG\r\n\x1a\n")
    browser_sessions.close()
