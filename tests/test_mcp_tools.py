import sys

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

    assert {"file_read", "file_write", "shell_exec", "jupyter_execute"} <= set(tools)
    assert tools["file_read"]["description"]
    assert tools["file_read"]["inputSchema"]["type"] == "object"
    assert "path" in tools["file_read"]["inputSchema"]["required"]
    assert tools["shell_exec"]["inputSchema"]["properties"]["command"]["type"] == "string"


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


def test_mcp_rejects_unknown_server_and_tool(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)

    missing_server = client.get("/mcp/missing/tools")
    missing_tool = client.post("/mcp/sandbox/tools/missing_tool", json={})

    assert missing_server.status_code == 404
    assert missing_server.json()["success"] is False
    assert missing_tool.status_code == 404
    assert missing_tool.json()["success"] is False
