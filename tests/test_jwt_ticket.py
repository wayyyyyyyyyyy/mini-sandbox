from datetime import UTC, datetime, timedelta

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from app.main import app


def _rsa_key_pair():
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_key = private_key.public_key()
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_pem = public_key.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return private_pem, public_pem


def _jwt(private_pem: bytes, expires_delta: timedelta = timedelta(minutes=5)) -> str:
    payload = {"sub": "way", "exp": datetime.now(UTC) + expires_delta}
    return jwt.encode(payload, private_pem, algorithm="RS256")


def _client_with_jwt(monkeypatch, public_pem: bytes):
    monkeypatch.setattr("app.auth.SANDBOX_API_KEY", "")
    monkeypatch.setattr("app.auth.JWT_PUBLIC_KEY", public_pem.decode("utf-8"))
    monkeypatch.setattr("app.auth.TICKET_TTL_SECONDS", 30)
    monkeypatch.setattr("app.auth._tickets", {})
    return TestClient(app)


def test_context_accepts_valid_rs256_jwt(monkeypatch):
    private_pem, public_pem = _rsa_key_pair()
    token = _jwt(private_pem)
    client = _client_with_jwt(monkeypatch, public_pem)

    response = client.get("/context", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200
    assert response.json()["data"]["workspace"]


def test_context_rejects_invalid_jwt(monkeypatch):
    _, public_pem = _rsa_key_pair()
    other_private_pem, _ = _rsa_key_pair()
    token = _jwt(other_private_pem)
    client = _client_with_jwt(monkeypatch, public_pem)

    response = client.get("/context", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 401
    assert response.json()["success"] is False


def test_context_rejects_expired_jwt(monkeypatch):
    private_pem, public_pem = _rsa_key_pair()
    token = _jwt(private_pem, expires_delta=timedelta(seconds=-1))
    client = _client_with_jwt(monkeypatch, public_pem)

    response = client.get("/context", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 401
    assert response.json()["message"] == "missing or invalid credentials"


def test_ticket_can_be_created_from_jwt_and_used_once(monkeypatch):
    private_pem, public_pem = _rsa_key_pair()
    token = _jwt(private_pem)
    client = _client_with_jwt(monkeypatch, public_pem)

    created = client.post("/tickets", headers={"Authorization": f"Bearer {token}"})

    assert created.status_code == 200
    data = created.json()["data"]
    assert data["ticket"]
    assert data["expires_in"] == 30

    first = client.get("/context", params={"ticket": data["ticket"]})
    second = client.get("/context", params={"ticket": data["ticket"]})

    assert first.status_code == 200
    assert second.status_code == 401
