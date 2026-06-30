from fastapi.testclient import TestClient

from app.main import app


def test_healthz_does_not_require_api_key(monkeypatch):
    monkeypatch.setattr("app.auth.SANDBOX_API_KEY", "secret")
    client = TestClient(app)

    response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_context_rejects_missing_api_key(monkeypatch):
    monkeypatch.setattr("app.auth.SANDBOX_API_KEY", "secret")
    client = TestClient(app)

    response = client.get("/context")

    assert response.status_code == 401


def test_context_rejects_invalid_api_key(monkeypatch):
    monkeypatch.setattr("app.auth.SANDBOX_API_KEY", "secret")
    client = TestClient(app)

    response = client.get("/context", headers={"X-Sandbox-Api-Key": "wrong"})

    assert response.status_code == 401


def test_context_accepts_x_sandbox_api_key(monkeypatch):
    monkeypatch.setattr("app.auth.SANDBOX_API_KEY", "secret")
    client = TestClient(app)

    response = client.get("/context", headers={"X-Sandbox-Api-Key": "secret"})

    assert response.status_code == 200
    assert response.json()["data"]["workspace"]


def test_context_accepts_bearer_api_key(monkeypatch):
    monkeypatch.setattr("app.auth.SANDBOX_API_KEY", "secret")
    client = TestClient(app)

    response = client.get("/context", headers={"Authorization": "Bearer secret"})

    assert response.status_code == 200
    assert response.json()["data"]["workspace"]


def test_context_allows_learning_mode_without_configured_key(monkeypatch):
    monkeypatch.setattr("app.auth.SANDBOX_API_KEY", "")
    client = TestClient(app)

    response = client.get("/context")

    assert response.status_code == 200
