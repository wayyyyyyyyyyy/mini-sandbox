from fastapi.testclient import TestClient

from app.main import app


def _client(monkeypatch, tmp_path):
    monkeypatch.setattr("app.auth.SANDBOX_API_KEY", "")
    monkeypatch.setattr("app.auth.JWT_PUBLIC_KEY", "")
    monkeypatch.setattr("app.security.WORKSPACE", tmp_path)
    monkeypatch.setattr("app.main.WORKSPACE", tmp_path)
    return TestClient(app)


def _data(response):
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    return body["data"]


def test_jupyter_info_reports_default_kernel_and_limits(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)

    info = _data(client.get("/jupyter/info"))

    assert info["default_kernel"]
    assert info["default_kernel"] in info["available_kernels"]
    assert info["active_sessions"] == 0
    assert info["session_timeout_seconds"] > 0
    assert info["max_sessions"] > 0
    assert info["description"]
    assert info["kernel_detection"]


def test_jupyter_session_create_list_and_delete(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)

    created = _data(
        client.post(
            "/jupyter/sessions/create",
            json={"session_id": "analysis", "cwd": "."},
        )
    )
    listed = _data(client.get("/jupyter/sessions"))
    deleted = _data(client.delete("/jupyter/sessions/analysis"))

    assert created["session_id"] == "analysis"
    assert created["kernel_name"]
    assert created["message"]
    assert listed["sessions"]["analysis"]["kernel_name"] == created["kernel_name"]
    assert listed["sessions"]["analysis"]["age_seconds"] >= 0
    assert deleted["success"] is True


def test_jupyter_execute_returns_stdout_and_execute_result(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)

    result = _data(
        client.post(
            "/jupyter/execute",
            json={"code": "print('hello from kernel')\n1 + 2", "cwd": "."},
        )
    )

    assert result["status"] == "ok"
    assert result["session_id"].startswith("jp_")
    assert result["kernel_name"]
    assert result["execution_count"] is not None
    assert result["code"] == "print('hello from kernel')\n1 + 2"
    assert any(output["output_type"] == "stream" and "hello from kernel" in output["text"] for output in result["outputs"])
    assert any(
        output["output_type"] == "execute_result"
        and output["data"]["text/plain"] == "3"
        for output in result["outputs"]
    )


def test_jupyter_execute_preserves_state_by_session_id(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)

    first = _data(
        client.post(
            "/jupyter/execute",
            json={"session_id": "stateful", "code": "value = 41", "cwd": "."},
        )
    )
    second = _data(
        client.post(
            "/jupyter/execute",
            json={"session_id": "stateful", "code": "value + 1"},
        )
    )

    assert first["session_id"] == "stateful"
    assert second["session_id"] == "stateful"
    assert any(
        output["output_type"] == "execute_result"
        and output["data"]["text/plain"] == "42"
        for output in second["outputs"]
    )


def test_jupyter_execute_returns_error_output(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)

    result = _data(
        client.post(
            "/jupyter/execute",
            json={"code": "raise ValueError('bad input')", "cwd": "."},
        )
    )

    assert result["status"] == "error"
    error = next(output for output in result["outputs"] if output["output_type"] == "error")
    assert error["ename"] == "ValueError"
    assert "bad input" in error["evalue"]
    assert error["traceback"]


def test_jupyter_rejects_workspace_escape(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)

    response = client.post(
        "/jupyter/execute",
        json={"code": "1 + 1", "cwd": "../outside"},
    )

    assert response.status_code == 403
    assert response.json()["success"] is False
