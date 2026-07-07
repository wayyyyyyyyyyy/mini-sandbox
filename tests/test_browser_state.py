import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from fastapi.testclient import TestClient

from app.main import app, browser_sessions


@pytest.fixture(autouse=True)
def close_browser_after_test():
    yield
    _close_browser_quietly()


@pytest.fixture
def browser_origin():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _StatePageHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _client(monkeypatch, tmp_path):
    monkeypatch.setattr("app.auth.SANDBOX_API_KEY", "")
    monkeypatch.setattr("app.auth.JWT_PUBLIC_KEY", "")
    monkeypatch.setattr("app.security.WORKSPACE", tmp_path)
    monkeypatch.setattr("app.main.WORKSPACE", tmp_path)
    _close_browser_quietly()
    return TestClient(app)


def _close_browser_quietly():
    browser_sessions.close()


def _data(response):
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    return body["data"]


class _StatePageHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Set-Cookie", "sandbox_sid=stateful; Path=/; SameSite=Lax")
        self.end_headers()
        self.wfile.write(
            b"""
            <html>
              <body>
                <script>
                  localStorage.setItem('sandbox_token', 'persisted');
                  sessionStorage.setItem('sandbox_tab', 'temporary');
                </script>
                Browser state fixture
              </body>
            </html>
            """
        )

    def log_message(self, format, *args):
        return


def test_browser_state_save_and_load_restores_cookies_and_local_storage(monkeypatch, tmp_path, browser_origin):
    client = _client(monkeypatch, tmp_path)
    _data(client.post("/browser/page/navigate", json={"url": browser_origin}))

    saved = _data(client.post("/browser/state/save", json={"path": "browser/state.json"}))
    state_file = tmp_path / "browser" / "state.json"
    assert saved["path"] == "browser/state.json"
    assert saved["cookies"] >= 1
    assert saved["origins"] == 1
    assert state_file.exists()

    _data(client.post(
        "/browser/page/evaluate",
        json={
            "script": (
                "() => { document.cookie = 'sandbox_sid=; Max-Age=0; Path=/'; "
                "localStorage.clear(); sessionStorage.clear(); return true; }"
            )
        },
    ))
    cleared = _data(client.post(
        "/browser/page/evaluate",
        json={
            "script": (
                "() => ({ cookie: document.cookie, "
                "token: localStorage.getItem('sandbox_token'), "
                "tab: sessionStorage.getItem('sandbox_tab') })"
            )
        },
    ))
    assert "sandbox_sid=stateful" not in cleared["result"]["cookie"]
    assert cleared["result"]["token"] is None
    assert cleared["result"]["tab"] is None

    loaded = _data(client.post("/browser/state/load", json={"path": "browser/state.json"}))
    restored = _data(client.post(
        "/browser/page/evaluate",
        json={
            "script": (
                "() => ({ cookie: document.cookie, "
                "token: localStorage.getItem('sandbox_token'), "
                "tab: sessionStorage.getItem('sandbox_tab') })"
            )
        },
    ))

    assert loaded == {"path": "browser/state.json", "cookies": 1, "origins": 1}
    assert "sandbox_sid=stateful" in restored["result"]["cookie"]
    assert restored["result"]["token"] == "persisted"
    assert restored["result"]["tab"] is None


def test_browser_state_load_rejects_missing_file(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)

    response = client.post("/browser/state/load", json={"path": "missing/state.json"})

    assert response.status_code == 404
    assert response.json()["success"] is False
