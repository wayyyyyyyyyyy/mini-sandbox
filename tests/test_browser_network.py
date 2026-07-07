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
def network_origin():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _NetworkPageHandler)
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


class _NetworkPageHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/api/data":
            content = b'{"source":"server"}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)
            return

        if self.path == "/api/plain":
            content = b"plain response"
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)
            return

        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"<html><body>network fixture</body></html>")

    def log_message(self, format, *args):
        return


def test_browser_network_requests_records_navigation_and_fetch(monkeypatch, tmp_path, network_origin):
    client = _client(monkeypatch, tmp_path)
    _data(client.post("/browser/page/navigate", json={"url": network_origin}))
    result = _data(client.post(
        "/browser/page/evaluate",
        json={"script": "() => fetch('/api/data').then(response => response.json())"},
    ))

    requests = _data(client.get("/browser/network/requests"))
    filtered = _data(client.get("/browser/network/requests", params={"filter": "/api/data", "limit": 1}))

    assert result["result"] == {"source": "server"}
    assert any(item["url"] == network_origin + "/" for item in requests["requests"])
    assert filtered["requests"][0]["url"] == network_origin + "/api/data"
    assert filtered["requests"][0]["method"] == "GET"
    assert filtered["requests"][0]["resource_type"] in {"Fetch", "XHR"}


def test_browser_network_route_mocks_and_removes_response(monkeypatch, tmp_path, network_origin):
    client = _client(monkeypatch, tmp_path)
    _data(client.post("/browser/page/navigate", json={"url": network_origin}))

    route = _data(client.post(
        "/browser/network/route",
        json={
            "url_pattern": "*/api/data",
            "response": {
                "status": 201,
                "headers": {"x-mini-route": "yes"},
                "body": '{"source":"mocked"}',
                "content_type": "application/json",
            },
        },
    ))
    mocked = _data(client.post(
        "/browser/page/evaluate",
        json={
            "script": (
                "() => fetch('/api/data').then(async response => ({ "
                "status: response.status, header: response.headers.get('x-mini-route'), "
                "body: await response.json() }))"
            )
        },
    ))
    removed = _data(client.request(
        "DELETE",
        "/browser/network/route",
        json={"url_pattern": "*/api/data"},
    ))
    restored = _data(client.post(
        "/browser/page/evaluate",
        json={"script": "() => fetch('/api/data').then(response => response.json())"},
    ))

    assert route == {"url_pattern": "*/api/data", "active": True, "abort": False}
    assert mocked["result"] == {
        "status": 201,
        "header": "yes",
        "body": {"source": "mocked"},
    }
    assert removed == {"url_pattern": "*/api/data", "removed": True}
    assert restored["result"] == {"source": "server"}
