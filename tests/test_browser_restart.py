import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from fastapi.testclient import TestClient

from app.main import app, browser_sessions


@pytest.fixture(autouse=True)
def close_browser_after_test():
    yield
    browser_sessions.close()


@pytest.fixture
def restart_origin():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _RestartPageHandler)
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
    browser_sessions.close()
    return TestClient(app)


def _data(response):
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    return body["data"]


class _RestartPageHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/empty":
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"<html><body>empty restart fixture</body></html>")
            return

        if self.path == "/api/value":
            content = b'{"source":"server"}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Set-Cookie", "restart_sid=before; Path=/; SameSite=Lax")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)
            return

        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Set-Cookie", "restart_sid=before; Path=/; SameSite=Lax")
        self.end_headers()
        self.wfile.write(
            b"""
            <html>
              <body>
                <script>localStorage.setItem('restart_token', 'before');</script>
                restart fixture
              </body>
            </html>
            """
        )

    def log_message(self, format, *args):
        return


def test_browser_restart_resets_process_state_tabs_network_and_routes(monkeypatch, tmp_path, restart_origin):
    client = _client(monkeypatch, tmp_path)
    _data(client.post("/browser/page/navigate", json={"url": restart_origin + "/empty"}))
    _data(client.post("/browser/tabs", json={"url": restart_origin + "/api/value"}))
    _data(client.post(
        "/browser/page/evaluate",
        json={"script": "() => fetch('/api/value').then(response => response.json())"},
    ))
    _data(client.post(
        "/browser/network/route",
        json={
            "url_pattern": "*/api/value",
            "response": {"body": '{"source":"mocked"}', "content_type": "application/json"},
        },
    ))

    before = _data(client.get("/browser/info"))
    network_before = _data(client.get("/browser/network/requests"))
    restarted = _data(client.post("/browser/restart", json={"mode": "hard"}))
    after = _data(client.get("/browser/info"))
    network_after = _data(client.get("/browser/network/requests"))

    _data(client.post("/browser/page/navigate", json={"url": restart_origin}))
    restored_state = _data(client.post(
        "/browser/page/evaluate",
        json={
            "script": (
                "() => ({ cookie: document.cookie, "
                "token: localStorage.getItem('restart_token') })"
            )
        },
    ))
    route_after_restart = _data(client.post(
        "/browser/page/evaluate",
        json={"script": "() => fetch('/api/value').then(response => response.json())"},
    ))

    assert before["page_count"] == 2
    assert len(network_before["requests"]) >= 1
    assert restarted["mode"] == "hard"
    assert restarted["restarted"] is True
    assert restarted["page_count"] == 1
    assert after["page_count"] == 1
    assert after["current_url"] == "about:blank"
    assert network_after["requests"] == []
    assert "restart_sid=before" in restored_state["result"]["cookie"]
    assert restored_state["result"]["token"] == "before"
    assert route_after_restart["result"] == {"source": "server"}
