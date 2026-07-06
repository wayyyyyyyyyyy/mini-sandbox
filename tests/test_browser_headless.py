import base64

from fastapi.testclient import TestClient

from app.main import app


def _client(monkeypatch, tmp_path):
    monkeypatch.setattr("app.auth.SANDBOX_API_KEY", "")
    monkeypatch.setattr("app.auth.JWT_PUBLIC_KEY", "")
    monkeypatch.setattr("app.security.WORKSPACE", tmp_path)
    monkeypatch.setattr("app.main.WORKSPACE", tmp_path)
    return TestClient(app)


def _data(response):
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    return body["data"]


def _page_url(html: str) -> str:
    encoded = base64.b64encode(html.encode("utf-8")).decode("ascii")
    return f"data:text/html;base64,{encoded}"


def test_browser_info_reports_headless_browser(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)

    info = _data(client.get("/browser/info"))

    assert info["browser"] == "chromium"
    assert info["headless"] is True
    assert info["viewport"]["width"] > 0
    assert info["viewport"]["height"] > 0
    assert info["page_count"] >= 1
    assert info["current_url"]


def test_browser_navigate_html_text_and_evaluate(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    url = _page_url(
        """
        <!doctype html>
        <html>
          <head><title>Mini Browser</title></head>
          <body>
            <main id="app">
              <h1>Browser works</h1>
              <p data-value="7">Visible text here</p>
            </main>
          </body>
        </html>
        """
    )

    navigated = _data(client.post("/browser/page/navigate", json={"url": url}))
    html = _data(client.get("/browser/page/html"))
    text = _data(client.get("/browser/page/text"))
    evaluated = _data(
        client.post(
            "/browser/page/evaluate",
            json={"script": "() => document.querySelector('[data-value]').dataset.value"},
        )
    )

    assert navigated["url"].startswith("data:text/html")
    assert navigated["title"] == "Mini Browser"
    assert "<h1>Browser works</h1>" in html
    assert "Visible text here" in text
    assert evaluated["result"] == "7"


def test_browser_screenshot_returns_png_bytes(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    url = _page_url("<html><body><h1>Screenshot target</h1></body></html>")
    _data(client.post("/browser/page/navigate", json={"url": url}))

    response = client.get("/browser/screenshot")

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert response.content.startswith(b"\x89PNG\r\n\x1a\n")
    assert int(response.headers["x-image-width"]) > 0
    assert int(response.headers["x-image-height"]) > 0


def test_browser_tabs_create_list_activate_and_close(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)

    created = _data(client.post("/browser/tabs", json={"url": "about:blank"}))
    listed = _data(client.get("/browser/tabs"))
    activated = _data(client.put(f"/browser/tabs/{created['index']}/activate"))
    closed = _data(client.delete(f"/browser/tabs/{created['index']}"))

    assert created["index"] >= 1
    assert any(tab["index"] == created["index"] for tab in listed)
    assert activated["active_index"] == created["index"]
    assert closed["closed"] is True


def test_browser_rejects_unsupported_url_scheme(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)

    response = client.post("/browser/page/navigate", json={"url": "file:///etc/passwd"})

    assert response.status_code == 400
    assert response.json()["success"] is False
