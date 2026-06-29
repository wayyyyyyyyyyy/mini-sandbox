from fastapi.testclient import TestClient

from app.main import app


def _client(monkeypatch, tmp_path):
    monkeypatch.setattr("app.auth.SANDBOX_API_KEY", "")
    monkeypatch.setattr("app.security.WORKSPACE", tmp_path)
    monkeypatch.setattr("app.main.WORKSPACE", tmp_path)
    return TestClient(app)


def test_file_search_returns_regex_matches_with_line_numbers(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    (tmp_path / "app.py").write_text(
        "def main():\n"
        "    TODO = 'wire sandbox'\n"
        "    return TODO\n",
        encoding="utf-8",
    )

    response = client.post(
        "/file/search",
        json={
            "path": "app.py",
            "regex": "TODO",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["path"] == "app.py"
    assert body["matches"] == [
        {"line": 1, "text": "    TODO = 'wire sandbox'", "match": "TODO"},
        {"line": 2, "text": "    return TODO", "match": "TODO"},
    ]


def test_file_search_supports_case_insensitive_and_max_results(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    (tmp_path / "notes.txt").write_text("Alpha\nalpha\nALPHA\n", encoding="utf-8")

    response = client.post(
        "/file/search",
        json={
            "path": "notes.txt",
            "regex": "alpha",
            "case_insensitive": True,
            "max_results": 2,
        },
    )

    assert response.status_code == 200
    matches = response.json()["matches"]
    assert [match["line"] for match in matches] == [0, 1]
    assert [match["match"] for match in matches] == ["Alpha", "alpha"]
