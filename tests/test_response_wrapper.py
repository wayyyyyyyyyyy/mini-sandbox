from fastapi.testclient import TestClient

from app.main import app


def _client(monkeypatch, tmp_path):
    monkeypatch.setattr("app.auth.SANDBOX_API_KEY", "")
    monkeypatch.setattr("app.security.WORKSPACE", tmp_path)
    monkeypatch.setattr("app.main.WORKSPACE", tmp_path)
    return TestClient(app)


def test_json_api_success_responses_are_wrapped(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)

    response = client.get("/context")

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["message"] == "Operation successful"
    assert body["hint"] is None
    assert body["data"]["workspace"]


def test_file_api_success_data_is_wrapped(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    (tmp_path / "notes.txt").write_text("alpha\n", encoding="utf-8")

    response = client.post("/file/read", json={"path": "notes.txt"})

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["data"]["path"] == "notes.txt"
    assert body["data"]["content"].replace("\r\n", "\n") == "alpha\n"


def test_json_api_http_errors_are_wrapped(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)

    response = client.post("/file/read", json={"path": "missing.txt"})

    assert response.status_code == 404
    body = response.json()
    assert body["success"] is False
    assert body["message"] == "file not found: missing.txt"
    assert body["data"] is None
    assert body["hint"] is None


def test_json_api_validation_errors_are_wrapped(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)

    response = client.post(
        "/file/replace",
        json={
            "path": "notes.txt",
            "old_str": "",
            "new_str": "omega",
        },
    )

    assert response.status_code == 422
    body = response.json()
    assert body["success"] is False
    assert body["message"] == "Validation error"
    assert isinstance(body["data"], list)
    assert body["hint"] is None


def test_healthz_keeps_plain_response(monkeypatch):
    monkeypatch.setattr("app.auth.SANDBOX_API_KEY", "secret")
    client = TestClient(app)

    response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
