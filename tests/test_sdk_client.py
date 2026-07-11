import base64
import sys
from pathlib import Path

import pytest
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


def test_sdk_file_watch_poll(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    watcher = client.file.watch(".")

    (tmp_path / "created.txt").write_text("created\n", encoding="utf-8")
    polled = client.file.watch_poll(watcher["watcher_id"], cursor=watcher["cursor"])

    assert polled["watcher_id"] == watcher["watcher_id"]
    assert polled["cursor"] == 1
    assert polled["events"][0]["type"] == "created"
    assert polled["events"][0]["path"] == "created.txt"


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
