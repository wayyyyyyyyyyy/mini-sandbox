import pytest
from fastapi.testclient import TestClient

from app.main import app


def _client(monkeypatch, tmp_path):
    monkeypatch.setattr("app.auth.SANDBOX_API_KEY", "")
    monkeypatch.setattr("app.security.WORKSPACE", tmp_path)
    monkeypatch.setattr("app.main.WORKSPACE", tmp_path)
    return TestClient(app)


@pytest.mark.xfail(strict=True, reason="file glob endpoint is not implemented yet")
def test_file_glob_supports_recursive_patterns_and_excludes(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text("print('ok')\n", encoding="utf-8")
    (tmp_path / "src" / "main.test.py").write_text("print('test')\n", encoding="utf-8")
    (tmp_path / "src" / "pkg").mkdir()
    (tmp_path / "src" / "pkg" / "mod.py").write_text("x = 1\n", encoding="utf-8")

    response = client.post(
        "/file/glob",
        json={
            "path": ".",
            "pattern": "src/**/*.py",
            "exclude": ["**/*.test.py"],
            "files_only": True,
            "sort_by": "path",
        },
    )

    assert response.status_code == 200
    assert response.json()["matches"] == ["src/main.py", "src/pkg/mod.py"]


@pytest.mark.xfail(strict=True, reason="file glob endpoint is not implemented yet")
def test_file_glob_can_include_metadata_and_limit_results(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    (tmp_path / "a.txt").write_text("aaa", encoding="utf-8")
    (tmp_path / "b.txt").write_text("bb", encoding="utf-8")

    response = client.post(
        "/file/glob",
        json={
            "path": ".",
            "pattern": "*.txt",
            "include_metadata": True,
            "max_results": 1,
            "sort_by": "path",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["matches"] == ["a.txt"]
    assert body["entries"][0]["path"] == "a.txt"
    assert body["entries"][0]["bytes"] == 3
