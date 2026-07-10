import socket

from fastapi.testclient import TestClient

from app.main import app


def _client(monkeypatch, tmp_path):
    monkeypatch.setattr("app.auth.SANDBOX_API_KEY", "")
    monkeypatch.setattr("app.auth.JWT_PUBLIC_KEY", "")
    monkeypatch.setattr("app.security.WORKSPACE", tmp_path)
    monkeypatch.setattr("app.main.WORKSPACE", tmp_path)
    return TestClient(app)


def test_ports_lists_local_listening_tcp_ports(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    try:
        port = listener.getsockname()[1]

        response = client.get("/ports")
    finally:
        listener.close()

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    ports = body["data"]["ports"]
    discovered = [entry for entry in ports if entry["port"] == port]
    assert discovered
    assert discovered[0]["host"] in {"127.0.0.1", "0.0.0.0", "::1", "::"}
    assert discovered[0]["protocol"] == "tcp"
    assert discovered[0]["proxy_url"] == f"/proxy/{port}/"


def test_ports_requires_credentials(monkeypatch, tmp_path):
    monkeypatch.setattr("app.auth.SANDBOX_API_KEY", "secret")
    monkeypatch.setattr("app.auth.JWT_PUBLIC_KEY", "")
    monkeypatch.setattr("app.security.WORKSPACE", tmp_path)
    monkeypatch.setattr("app.main.WORKSPACE", tmp_path)
    client = TestClient(app)

    response = client.get("/ports")

    assert response.status_code == 401
    assert response.json()["success"] is False
