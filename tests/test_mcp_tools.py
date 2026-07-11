import sys
import socket

from fastapi.testclient import TestClient

from app.main import app, jupyter_sessions, shell_sessions


def _client(monkeypatch, tmp_path):
    monkeypatch.setattr("app.auth.SANDBOX_API_KEY", "")
    monkeypatch.setattr("app.auth.JWT_PUBLIC_KEY", "")
    monkeypatch.setattr("app.security.WORKSPACE", tmp_path)
    monkeypatch.setattr("app.main.WORKSPACE", tmp_path)
    monkeypatch.setattr("app.shell_sessions.WORKSPACE", tmp_path)
    _reset_sessions()
    return TestClient(app)


def _reset_sessions():
    for session_id in list(shell_sessions.list()):
        shell_sessions.close(session_id)
    with shell_sessions._lock:
        shell_sessions._sessions.clear()
    jupyter_sessions.delete_all()


def _data(response):
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    return body["data"]


def test_mcp_servers_lists_builtin_sandbox_server(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)

    servers = _data(client.get("/mcp/servers"))

    assert servers == ["sandbox"]


def test_mcp_tools_lists_json_schema_tools(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)

    result = _data(client.get("/mcp/sandbox/tools"))
    tools = {tool["name"]: tool for tool in result["tools"]}

    assert {
        "file_read",
        "file_write",
        "file_search",
        "file_grep",
        "file_replace",
        "browser_navigate",
        "browser_text",
        "browser_screenshot",
        "browser_evaluate",
        "browser_click",
        "browser_fill",
        "browser_wait_for_selector",
        "shell_exec",
        "jupyter_execute",
        "ports_list",
    } <= set(tools)
    assert tools["file_read"]["description"]
    assert tools["file_read"]["inputSchema"]["type"] == "object"
    assert "path" in tools["file_read"]["inputSchema"]["required"]
    assert {"path", "regex"} <= set(tools["file_search"]["inputSchema"]["required"])
    assert {"path", "pattern"} <= set(tools["file_grep"]["inputSchema"]["required"])
    assert {"path", "old_str", "new_str"} <= set(tools["file_replace"]["inputSchema"]["required"])
    assert tools["browser_navigate"]["inputSchema"]["required"] == ["url"]
    assert tools["browser_text"]["inputSchema"] == {"type": "object", "properties": {}, "required": []}
    assert tools["browser_screenshot"]["inputSchema"]["required"] == []
    assert tools["browser_screenshot"]["inputSchema"]["properties"]["format"]["enum"] == ["png", "jpg", "jpeg"]
    assert tools["browser_evaluate"]["inputSchema"]["required"] == ["script"]
    assert tools["browser_click"]["inputSchema"]["required"] == ["selector"]
    assert tools["browser_fill"]["inputSchema"]["required"] == ["selector", "text"]
    assert tools["browser_wait_for_selector"]["inputSchema"]["required"] == ["selector"]
    assert tools["shell_exec"]["inputSchema"]["properties"]["command"]["type"] == "string"
    assert tools["ports_list"]["inputSchema"]["required"] == []


def test_mcp_file_tools_read_and_write_workspace_files(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)

    written = _data(
        client.post(
            "/mcp/sandbox/tools/file_write",
            json={"path": "notes.txt", "content": "hello mcp"},
        )
    )
    read = _data(
        client.post(
            "/mcp/sandbox/tools/file_read",
            json={"path": "notes.txt"},
        )
    )

    assert written["isError"] is False
    assert written["content"][0]["type"] == "json"
    assert written["content"][0]["data"]["path"] == "notes.txt"
    assert read["isError"] is False
    assert read["content"][0]["type"] == "text"
    assert read["content"][0]["text"] == "hello mcp"


def test_mcp_file_search_tool_finds_regex_matches(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    (tmp_path / "search.txt").write_text("alpha\nBeta\nalphabet\n", encoding="utf-8")

    result = _data(
        client.post(
            "/mcp/sandbox/tools/file_search",
            json={"path": "search.txt", "regex": "alpha", "max_results": 2},
        )
    )

    assert result["isError"] is False
    assert result["content"][0]["type"] == "json"
    data = result["content"][0]["data"]
    assert data["path"] == "search.txt"
    assert data["regex"] == "alpha"
    assert data["matches"] == [
        {"line": 0, "text": "alpha", "match": "alpha"},
        {"line": 2, "text": "alphabet", "match": "alpha"},
    ]


def test_mcp_file_grep_tool_searches_directory_tree(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("# TODO app\nprint('ok')\n", encoding="utf-8")
    (tmp_path / "src" / "notes.txt").write_text("TODO notes\n", encoding="utf-8")
    (tmp_path / "src" / "pkg").mkdir()
    (tmp_path / "src" / "pkg" / "mod.py").write_text("value = 'TODO mod'\n", encoding="utf-8")

    result = _data(
        client.post(
            "/mcp/sandbox/tools/file_grep",
            json={"path": "src", "pattern": "TODO", "include": ["*.py"], "max_results": 2},
        )
    )

    assert result["isError"] is False
    assert result["content"][0]["type"] == "json"
    data = result["content"][0]["data"]
    assert data["path"] == "src"
    assert data["pattern"] == "TODO"
    assert [(match["path"], match["line"], match["match"]) for match in data["matches"]] == [
        ("src/app.py", 0, "TODO"),
        ("src/pkg/mod.py", 0, "TODO"),
    ]


def test_mcp_file_grep_tool_supports_options(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.txt").write_text("Alpha\n", encoding="utf-8")
    (tmp_path / "src" / "b.txt").write_text("alpha\n", encoding="utf-8")
    (tmp_path / "src" / "skip").mkdir()
    (tmp_path / "src" / "skip" / "c.txt").write_text("ALPHA\n", encoding="utf-8")

    result = _data(
        client.post(
            "/mcp/sandbox/tools/file_grep",
            json={
                "path": "src",
                "pattern": "alpha",
                "exclude": ["skip/**"],
                "case_insensitive": True,
                "max_results": 1,
            },
        )
    )

    assert result["isError"] is False
    data = result["content"][0]["data"]
    assert len(data["matches"]) == 1
    assert data["matches"][0]["path"] == "src/a.txt"
    assert data["matches"][0]["match"] == "Alpha"


def test_mcp_file_replace_replaces_first_match_by_default(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    target = tmp_path / "notes.txt"
    target.write_text("alpha beta alpha\n", encoding="utf-8")

    result = _data(
        client.post(
            "/mcp/sandbox/tools/file_replace",
            json={"path": "notes.txt", "old_str": "alpha", "new_str": "omega"},
        )
    )

    assert result["isError"] is False
    data = result["content"][0]["data"]
    assert data == {"path": "notes.txt", "replaced": 1, "changed": True}
    assert target.read_text(encoding="utf-8") == "omega beta alpha\n"


def test_mcp_file_replace_supports_all_and_count(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    target = tmp_path / "notes.txt"
    target.write_text("alpha alpha alpha\n", encoding="utf-8")

    count_result = _data(
        client.post(
            "/mcp/sandbox/tools/file_replace",
            json={"path": "notes.txt", "old_str": "alpha", "new_str": "beta", "count": 2},
        )
    )

    assert count_result["content"][0]["data"]["replaced"] == 2
    assert target.read_text(encoding="utf-8") == "beta beta alpha\n"

    all_result = _data(
        client.post(
            "/mcp/sandbox/tools/file_replace",
            json={"path": "notes.txt", "old_str": "alpha", "new_str": "gamma", "all": True},
        )
    )

    assert all_result["content"][0]["data"] == {"path": "notes.txt", "replaced": 1, "changed": True}
    assert target.read_text(encoding="utf-8") == "beta beta gamma\n"


def test_mcp_file_replace_returns_errors_for_missing_match_and_empty_old_string(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    (tmp_path / "notes.txt").write_text("alpha beta\n", encoding="utf-8")

    missing = client.post(
        "/mcp/sandbox/tools/file_replace",
        json={"path": "notes.txt", "old_str": "missing", "new_str": "omega"},
    )
    empty = client.post(
        "/mcp/sandbox/tools/file_replace",
        json={"path": "notes.txt", "old_str": "", "new_str": "omega"},
    )

    assert missing.status_code == 404
    assert "old_str not found" in missing.json()["message"]
    assert empty.status_code == 422


def test_mcp_shell_exec_tool_runs_command(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)

    result = _data(
        client.post(
            "/mcp/sandbox/tools/shell_exec",
            json={"command": f'"{sys.executable}" -c "print(\'mcp-shell\')"', "timeout": 5},
        )
    )

    assert result["isError"] is False
    assert result["content"][0]["type"] == "json"
    assert result["content"][0]["data"]["status"] == "completed"
    assert "mcp-shell" in result["content"][0]["data"]["output"]


def test_mcp_jupyter_execute_tool_runs_stateful_python(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)

    first = _data(
        client.post(
            "/mcp/sandbox/tools/jupyter_execute",
            json={"session_id": "mcp-jupyter", "code": "value = 10", "timeout": 30},
        )
    )
    second = _data(
        client.post(
            "/mcp/sandbox/tools/jupyter_execute",
            json={"session_id": "mcp-jupyter", "code": "value + 5", "timeout": 30},
        )
    )

    assert first["isError"] is False
    assert second["isError"] is False
    assert any(
        output["output_type"] == "execute_result"
        and output["data"]["text/plain"] == "15"
        for output in second["content"][0]["data"]["outputs"]
    )


def test_mcp_ports_list_tool_returns_local_listening_ports(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    try:
        port = listener.getsockname()[1]
        result = _data(client.post("/mcp/sandbox/tools/ports_list", json={}))
    finally:
        listener.close()

    assert result["isError"] is False
    assert result["content"][0]["type"] == "json"
    ports = result["content"][0]["data"]["ports"]
    discovered = [entry for entry in ports if entry["port"] == port]
    assert discovered
    assert discovered[0]["proxy_url"] == f"/proxy/{port}/"


def test_mcp_rejects_unknown_server_and_tool(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)

    missing_server = client.get("/mcp/missing/tools")
    missing_tool = client.post("/mcp/sandbox/tools/missing_tool", json={})

    assert missing_server.status_code == 404
    assert missing_server.json()["success"] is False
    assert missing_tool.status_code == 404
    assert missing_tool.json()["success"] is False
