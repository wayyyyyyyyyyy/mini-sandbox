from fastapi.testclient import TestClient

from app.main import app


def _client(monkeypatch, tmp_path):
    monkeypatch.setattr("app.auth.SANDBOX_API_KEY", "")
    monkeypatch.setattr("app.security.WORKSPACE", tmp_path)
    monkeypatch.setattr("app.main.WORKSPACE", tmp_path)
    return TestClient(app)


def test_file_grep_searches_directory_tree_with_include_patterns(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("# TODO app\nprint('ok')\n", encoding="utf-8")
    (tmp_path / "src" / "notes.txt").write_text("TODO notes\n", encoding="utf-8")
    (tmp_path / "src" / "pkg").mkdir()
    (tmp_path / "src" / "pkg" / "mod.py").write_text("value = 'TODO mod'\n", encoding="utf-8")

    response = client.post(
        "/file/grep",
        json={
            "path": "src",
            "pattern": "TODO",
            "include": ["*.py"],
        },
    )

    assert response.status_code == 200
    matches = response.json()["matches"]
    assert [(match["path"], match["line"]) for match in matches] == [
        ("src/app.py", 0),
        ("src/pkg/mod.py", 0),
    ]


def test_file_grep_supports_exclude_case_insensitive_and_max_results(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.txt").write_text("Alpha\n", encoding="utf-8")
    (tmp_path / "src" / "b.txt").write_text("alpha\n", encoding="utf-8")
    (tmp_path / "src" / "skip").mkdir()
    (tmp_path / "src" / "skip" / "c.txt").write_text("ALPHA\n", encoding="utf-8")

    response = client.post(
        "/file/grep",
        json={
            "path": "src",
            "pattern": "alpha",
            "exclude": ["skip/**"],
            "case_insensitive": True,
            "max_results": 1,
        },
    )

    assert response.status_code == 200
    matches = response.json()["matches"]
    assert len(matches) == 1
    assert matches[0]["path"] == "src/a.txt"
    assert matches[0]["match"] == "Alpha"
