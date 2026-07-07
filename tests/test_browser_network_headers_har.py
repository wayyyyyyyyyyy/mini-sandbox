import json
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
def header_origins():
    servers = [
        ThreadingHTTPServer(("127.0.0.1", 0), _HeaderPageHandler),
        ThreadingHTTPServer(("127.0.0.1", 0), _HeaderPageHandler),
    ]
    threads = [threading.Thread(target=server.serve_forever, daemon=True) for server in servers]
    for thread in threads:
        thread.start()
    try:
        yield [f"http://127.0.0.1:{server.server_port}" for server in servers]
    finally:
        for server in servers:
            server.shutdown()
            server.server_close()
        for thread in threads:
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


class _HeaderPageHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/api/headers":
            headers = {key.lower(): value for key, value in self.headers.items()}
            content = json.dumps(headers).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)
            return

        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"<html><body>headers fixture</body></html>")

    def log_message(self, format, *args):
        return


def test_browser_network_headers_and_scoped_headers(monkeypatch, tmp_path, header_origins):
    client = _client(monkeypatch, tmp_path)
    origin_a, origin_b = header_origins

    global_headers = _data(client.post(
        "/browser/network/headers",
        json={"headers": {"x-mini-global": "global-value"}},
    ))
    scoped_headers = _data(client.post(
        "/browser/network/scoped_headers",
        json={"origin": origin_a, "headers": {"x-mini-scoped": "scoped-value"}},
    ))

    _data(client.post("/browser/page/navigate", json={"url": origin_a}))
    origin_a_headers = _data(client.post(
        "/browser/page/evaluate",
        json={"script": "() => fetch('/api/headers').then(response => response.json())"},
    ))["result"]

    _data(client.post("/browser/page/navigate", json={"url": origin_b}))
    origin_b_headers = _data(client.post(
        "/browser/page/evaluate",
        json={"script": "() => fetch('/api/headers').then(response => response.json())"},
    ))["result"]

    assert global_headers == {"headers": {"x-mini-global": "global-value"}}
    assert scoped_headers == {
        "origin": origin_a,
        "headers": {"x-mini-scoped": "scoped-value"},
    }
    assert origin_a_headers["x-mini-global"] == "global-value"
    assert origin_a_headers["x-mini-scoped"] == "scoped-value"
    assert origin_b_headers["x-mini-global"] == "global-value"
    assert "x-mini-scoped" not in origin_b_headers


def test_browser_network_export_har(monkeypatch, tmp_path, header_origins):
    client = _client(monkeypatch, tmp_path)
    origin = header_origins[0]

    _data(client.post("/browser/page/navigate", json={"url": origin}))
    _data(client.post(
        "/browser/page/evaluate",
        json={"script": "() => fetch('/api/headers').then(response => response.json())"},
    ))
    result = _data(client.post(
        "/browser/network/export_har",
        json={"save_path": "network/session.har"},
    ))

    har_path = tmp_path / "network" / "session.har"
    har = json.loads(har_path.read_text(encoding="utf-8"))
    urls = [entry["request"]["url"] for entry in har["log"]["entries"]]

    assert result["path"] == "network/session.har"
    assert result["entries"] == len(har["log"]["entries"])
    assert any(url == origin + "/api/headers" for url in urls)
