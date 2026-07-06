from fastapi.testclient import TestClient

from app.main import app


def _client(monkeypatch, tmp_path):
    monkeypatch.setattr("app.auth.SANDBOX_API_KEY", "")
    monkeypatch.setattr("app.security.WORKSPACE", tmp_path)
    monkeypatch.setattr("app.main.WORKSPACE", tmp_path)
    return TestClient(app)


def test_file_find_matches_simple_glob_recursively(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("print('ok')\n", encoding="utf-8")
    (tmp_path / "src" / "app.txt").write_text("skip\n", encoding="utf-8")
    (tmp_path / "src" / "nested").mkdir()
    (tmp_path / "src" / "nested" / "worker.py").write_text("x = 1\n", encoding="utf-8")

    response = client.post(
        "/file/find",
        json={
            "path": "src",
            "glob": "*.py",
        },
    )

    assert response.status_code == 200
    assert response.json()["data"]["files"] == ["src/app.py", "src/nested/worker.py"]


def test_file_find_respects_hidden_filter_and_max_results(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    (tmp_path / "a.py").write_text("a\n", encoding="utf-8")
    (tmp_path / "b.py").write_text("b\n", encoding="utf-8")
    (tmp_path / ".hidden.py").write_text("hidden\n", encoding="utf-8")

    response = client.post(
        "/file/find",
        json={
            "path": ".",
            "glob": "*.py",
            "include_hidden": False,
            "max_results": 1,
        },
    )

    assert response.status_code == 200
    assert response.json()["data"]["files"] == ["a.py"]
