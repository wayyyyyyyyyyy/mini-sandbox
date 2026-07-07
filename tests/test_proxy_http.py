import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

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


class _ProxyTargetHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.startswith("/api/users"):
            content = json.dumps({
                "method": "GET",
                "path": self.path,
                "host": self.headers.get("host"),
                "custom": self.headers.get("x-mini-proxy"),
            }).encode("utf-8")
            self.send_response(203)
            self.send_header("Content-Type", "application/json")
            self.send_header("X-Upstream-Service", "target")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)
            return
        self.send_response(404)
        self.end_headers()

    def do_POST(self):
        length = int(self.headers.get("content-length") or "0")
        body = self.rfile.read(length).decode("utf-8")
        content = json.dumps({
            "method": "POST",
            "path": self.path,
            "body": body,
            "content_type": self.headers.get("content-type"),
        }).encode("utf-8")
        self.send_response(201)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def log_message(self, format, *args):
        return


def _start_target_server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _ProxyTargetHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def test_proxy_forwards_get_path_query_headers_and_status(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    server, thread = _start_target_server()
    try:
        response = client.get(
            f"/proxy/{server.server_port}/api/users",
            params={"page": "1"},
            headers={"x-mini-proxy": "yes"},
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert response.status_code == 203
    assert response.headers["x-upstream-service"] == "target"
    assert response.json() == {
        "method": "GET",
        "path": "/api/users?page=1",
        "host": f"127.0.0.1:{server.server_port}",
        "custom": "yes",
    }


def test_proxy_forwards_post_body(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    server, thread = _start_target_server()
    try:
        response = client.post(
            f"/proxy/{server.server_port}/api/users",
            content=b'{"name":"way"}',
            headers={"content-type": "application/json"},
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert response.status_code == 201
    assert response.json() == {
        "method": "POST",
        "path": "/api/users",
        "body": '{"name":"way"}',
        "content_type": "application/json",
    }


def test_proxy_rejects_invalid_port(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    response = client.get("/proxy/70000/api/users")

    assert response.status_code == 422
    assert response.json()["success"] is False


def test_proxy_reports_unavailable_upstream(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    response = client.get("/proxy/9/api/users")

    assert response.status_code == 502
    assert response.json()["success"] is False
