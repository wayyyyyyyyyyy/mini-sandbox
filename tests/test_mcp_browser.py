import base64

from fastapi.testclient import TestClient

from app.main import app, browser_sessions


def _client(monkeypatch, tmp_path):
    monkeypatch.setattr("app.auth.SANDBOX_API_KEY", "")
    monkeypatch.setattr("app.auth.JWT_PUBLIC_KEY", "")
    monkeypatch.setattr("app.security.WORKSPACE", tmp_path)
    monkeypatch.setattr("app.main.WORKSPACE", tmp_path)
    browser_sessions.close()
    return TestClient(app)


def _data(response):
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    return body["data"]


def _page_url(html: str) -> str:
    encoded = base64.b64encode(html.encode("utf-8")).decode("ascii")
    return f"data:text/html;base64,{encoded}"


def test_mcp_browser_navigate_uses_existing_browser_session(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    url = _page_url("<html><head><title>MCP Browser</title></head><body>hello</body></html>")

    result = _data(
        client.post(
            "/mcp/sandbox/tools/browser_navigate",
            json={"url": url, "wait_until": "domcontentloaded", "timeout": 30000},
        )
    )

    assert result["isError"] is False
    data = result["content"][0]["data"]
    assert data["url"].startswith("data:text/html")
    assert data["title"] == "MCP Browser"
    assert data["status"] is None


def test_mcp_browser_navigate_validates_wait_until_and_timeout(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    url = _page_url("<html><title>Invalid options</title></html>")

    invalid_wait = client.post(
        "/mcp/sandbox/tools/browser_navigate",
        json={"url": url, "wait_until": "invalid"},
    )
    invalid_timeout = client.post(
        "/mcp/sandbox/tools/browser_navigate",
        json={"url": url, "timeout": 500},
    )

    assert invalid_wait.status_code == 422
    assert invalid_timeout.status_code == 422


def test_mcp_browser_text_reads_visible_text_from_active_page(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    url = _page_url("<html><body><h1>Visible heading</h1><p>Visible paragraph</p></body></html>")
    _data(client.post("/mcp/sandbox/tools/browser_navigate", json={"url": url}))

    result = _data(client.post("/mcp/sandbox/tools/browser_text", json={}))

    assert result["isError"] is False
    content = result["content"]
    assert len(content) == 1
    assert content[0]["type"] == "text"
    assert "Visible heading" in content[0]["text"]
    assert "Visible paragraph" in content[0]["text"]


def test_mcp_browser_screenshot_returns_standard_image_content_and_enforces_limit(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    url = _page_url("<html><body><h1>Screenshot target</h1></body></html>")
    _data(client.post("/mcp/sandbox/tools/browser_navigate", json={"url": url}))

    result = _data(client.post("/mcp/sandbox/tools/browser_screenshot", json={"format": "png"}))

    assert result["isError"] is False
    content = result["content"]
    assert len(content) == 1
    assert content[0]["type"] == "image"
    assert content[0]["mimeType"] == "image/png"
    assert base64.b64decode(content[0]["data"]).startswith(b"\x89PNG\r\n\x1a\n")

    invalid_format = client.post("/mcp/sandbox/tools/browser_screenshot", json={"format": "bmp"})
    invalid_quality = client.post("/mcp/sandbox/tools/browser_screenshot", json={"quality": 101})
    assert invalid_format.status_code == 422
    assert invalid_quality.status_code == 422

    monkeypatch.setattr("app.config.MAX_BROWSER_SCREENSHOT_BYTES", 1)
    limited = client.post("/mcp/sandbox/tools/browser_screenshot", json={})
    assert limited.status_code == 413


def test_mcp_browser_evaluate_returns_page_script_result(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    url = _page_url("<html><body><main data-value='42'>Evaluate target</main></body></html>")
    _data(client.post("/mcp/sandbox/tools/browser_navigate", json={"url": url}))

    result = _data(
        client.post(
            "/mcp/sandbox/tools/browser_evaluate",
            json={"script": "() => document.querySelector('main').dataset.value"},
        )
    )

    assert result["isError"] is False
    assert result["content"][0]["type"] == "json"
    assert result["content"][0]["data"] == {"result": "42"}


def test_mcp_browser_evaluate_rejects_empty_script(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)

    response = client.post("/mcp/sandbox/tools/browser_evaluate", json={"script": ""})

    assert response.status_code == 422


def test_mcp_browser_click_dispatches_page_interaction(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    url = _page_url(
        "<html><body><button id='save' onclick=\"document.body.dataset.clicked='yes'\">Save</button></body></html>"
    )
    _data(client.post("/mcp/sandbox/tools/browser_navigate", json={"url": url}))

    result = _data(
        client.post(
            "/mcp/sandbox/tools/browser_click",
            json={"selector": "#save", "timeout": 1000},
        )
    )
    evaluated = _data(
        client.post(
            "/mcp/sandbox/tools/browser_evaluate",
            json={"script": "() => document.body.dataset.clicked"},
        )
    )

    assert result["isError"] is False
    assert result["content"][0]["data"] == {"selector": "#save", "ok": True}
    assert evaluated["content"][0]["data"] == {"result": "yes"}


def test_mcp_browser_click_rejects_empty_selector_and_invalid_timeout(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)

    empty_selector = client.post("/mcp/sandbox/tools/browser_click", json={"selector": ""})
    invalid_timeout = client.post(
        "/mcp/sandbox/tools/browser_click",
        json={"selector": "#save", "timeout": -1},
    )

    assert empty_selector.status_code == 422
    assert invalid_timeout.status_code == 422


def test_mcp_browser_fill_updates_form_control(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    url = _page_url("<html><body><input id='name' value='old'></body></html>")
    _data(client.post("/mcp/sandbox/tools/browser_navigate", json={"url": url}))

    result = _data(
        client.post(
            "/mcp/sandbox/tools/browser_fill",
            json={"selector": "#name", "text": "new value", "timeout": 1000},
        )
    )
    evaluated = _data(
        client.post(
            "/mcp/sandbox/tools/browser_evaluate",
            json={"script": "() => document.querySelector('#name').value"},
        )
    )

    assert result["isError"] is False
    assert result["content"][0]["data"] == {"selector": "#name", "ok": True}
    assert evaluated["content"][0]["data"] == {"result": "new value"}

    cleared = _data(
        client.post(
            "/mcp/sandbox/tools/browser_fill",
            json={"selector": "#name", "text": ""},
        )
    )

    assert cleared["content"][0]["data"] == {"selector": "#name", "ok": True}


def test_mcp_browser_fill_rejects_empty_selector_and_invalid_timeout(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)

    empty_selector = client.post(
        "/mcp/sandbox/tools/browser_fill",
        json={"selector": "", "text": "value"},
    )
    invalid_timeout = client.post(
        "/mcp/sandbox/tools/browser_fill",
        json={"selector": "#name", "text": "value", "timeout": 120001},
    )

    assert empty_selector.status_code == 422
    assert invalid_timeout.status_code == 422


def test_mcp_browser_wait_for_selector_waits_for_dynamic_element(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    url = _page_url(
        "<html><body><script>setTimeout(() => { document.body.innerHTML = '<div id=\"ready\">Ready</div>'; }, 100);</script></body></html>"
    )
    _data(client.post("/mcp/sandbox/tools/browser_navigate", json={"url": url}))

    result = _data(
        client.post(
            "/mcp/sandbox/tools/browser_wait_for_selector",
            json={"selector": "#ready", "timeout": 2000},
        )
    )

    assert result["isError"] is False
    assert result["content"][0]["data"] == {"selector": "#ready", "ok": True}


def test_mcp_browser_wait_for_selector_rejects_empty_selector_and_invalid_timeout(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)

    empty_selector = client.post(
        "/mcp/sandbox/tools/browser_wait_for_selector",
        json={"selector": ""},
    )
    invalid_timeout = client.post(
        "/mcp/sandbox/tools/browser_wait_for_selector",
        json={"selector": "#ready", "timeout": 120001},
    )

    assert empty_selector.status_code == 422
    assert invalid_timeout.status_code == 422
