from fastapi.testclient import TestClient

from app.main import app


def _client(monkeypatch, tmp_path):
    monkeypatch.setattr("app.auth.SANDBOX_API_KEY", "")
    monkeypatch.setattr("app.security.WORKSPACE", tmp_path)
    monkeypatch.setattr("app.main.WORKSPACE", tmp_path)
    return TestClient(app)


def test_file_replace_replaces_first_match_by_default(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    target = tmp_path / "notes.txt"
    target.write_text("alpha beta alpha\n", encoding="utf-8")

    response = client.post(
        "/file/replace",
        json={
            "path": "notes.txt",
            "old_str": "alpha",
            "new_str": "omega",
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "path": "notes.txt",
        "replaced": 1,
        "changed": True,
    }
    assert target.read_text(encoding="utf-8") == "omega beta alpha\n"


def test_file_replace_can_replace_all_matches(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    target = tmp_path / "notes.txt"
    target.write_text("alpha beta alpha\n", encoding="utf-8")

    response = client.post(
        "/file/replace",
        json={
            "path": "notes.txt",
            "old_str": "alpha",
            "new_str": "omega",
            "all": True,
        },
    )

    assert response.status_code == 200
    assert response.json()["replaced"] == 2
    assert target.read_text(encoding="utf-8") == "omega beta omega\n"


def test_file_replace_can_limit_replacement_count(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    target = tmp_path / "notes.txt"
    target.write_text("alpha alpha alpha\n", encoding="utf-8")

    response = client.post(
        "/file/replace",
        json={
            "path": "notes.txt",
            "old_str": "alpha",
            "new_str": "omega",
            "count": 2,
        },
    )

    assert response.status_code == 200
    assert response.json()["replaced"] == 2
    assert target.read_text(encoding="utf-8") == "omega omega alpha\n"


def test_file_replace_returns_404_when_old_string_is_missing(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    target = tmp_path / "notes.txt"
    target.write_text("alpha beta\n", encoding="utf-8")

    response = client.post(
        "/file/replace",
        json={
            "path": "notes.txt",
            "old_str": "gamma",
            "new_str": "omega",
        },
    )

    assert response.status_code == 404
    assert "old_str not found" in response.json()["detail"]
    assert target.read_text(encoding="utf-8") == "alpha beta\n"


def test_file_replace_rejects_empty_old_string(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    (tmp_path / "notes.txt").write_text("alpha beta\n", encoding="utf-8")

    response = client.post(
        "/file/replace",
        json={
            "path": "notes.txt",
            "old_str": "",
            "new_str": "omega",
        },
    )

    assert response.status_code == 422


def test_file_replace_rejects_workspace_escape(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)

    response = client.post(
        "/file/replace",
        json={
            "path": "../outside.txt",
            "old_str": "alpha",
            "new_str": "omega",
        },
    )

    assert response.status_code == 400


def test_file_replace_result_can_be_read_back(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    (tmp_path / "notes.txt").write_text("alpha beta\n", encoding="utf-8")

    replace_response = client.post(
        "/file/replace",
        json={
            "path": "notes.txt",
            "old_str": "beta",
            "new_str": "omega",
        },
    )
    read_response = client.post("/file/read", json={"path": "notes.txt"})

    assert replace_response.status_code == 200
    assert read_response.status_code == 200
    assert read_response.json()["content"] == "alpha omega\n"
