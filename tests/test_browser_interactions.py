import base64

import pytest
from fastapi.testclient import TestClient

from app.main import app, browser_sessions


@pytest.fixture(autouse=True)
def close_browser_after_test():
    yield
    _close_browser_quietly()


def _client(monkeypatch, tmp_path):
    monkeypatch.setattr("app.auth.SANDBOX_API_KEY", "")
    monkeypatch.setattr("app.auth.JWT_PUBLIC_KEY", "")
    monkeypatch.setattr("app.security.WORKSPACE", tmp_path)
    monkeypatch.setattr("app.main.WORKSPACE", tmp_path)
    _close_browser_quietly()
    return TestClient(app)


def _close_browser_quietly():
    try:
        browser_sessions.close()
    except PermissionError:
        pass


def _data(response):
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    return body["data"]


def _page_url(html: str) -> str:
    encoded = base64.b64encode(html.encode("utf-8")).decode("ascii")
    return f"data:text/html;base64,{encoded}"


def test_browser_wait_for_selector_reports_match(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    _data(client.post(
        "/browser/page/navigate",
        json={"url": _page_url("<html><body><button id='save'>Save</button></body></html>")},
    ))

    result = _data(client.post(
        "/browser/page/wait_for_selector",
        json={"selector": "#save", "timeout": 1000},
    ))

    assert result == {"selector": "#save", "ok": True}


def test_browser_click_type_and_fill_dispatch_dom_events(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    _data(client.post(
        "/browser/page/navigate",
        json={
            "url": _page_url(
                """
                <html>
                  <body>
                    <input id="name" value="old">
                    <input id="notes" value="">
                    <button id="submit" onclick="document.body.dataset.clicked='yes'">Save</button>
                    <script>
                      window.events = [];
                      for (const id of ['name', 'notes']) {
                        const el = document.getElementById(id);
                        el.addEventListener('input', () => window.events.push(id + ':input:' + el.value));
                        el.addEventListener('change', () => window.events.push(id + ':change:' + el.value));
                      }
                    </script>
                  </body>
                </html>
                """
            )
        },
    ))

    clicked = _data(client.post("/browser/page/click", json={"selector": "#submit"}))
    typed = _data(client.post("/browser/page/type", json={"selector": "#notes", "text": "abc"}))
    filled = _data(client.post("/browser/page/fill", json={"selector": "#name", "text": "way"}))
    state = _data(client.post(
        "/browser/page/evaluate",
        json={
            "script": (
                "() => ({ clicked: document.body.dataset.clicked, "
                "name: document.querySelector('#name').value, "
                "notes: document.querySelector('#notes').value, events: window.events })"
            )
        },
    ))

    assert clicked == {"selector": "#submit", "ok": True}
    assert typed == {"selector": "#notes", "ok": True}
    assert filled == {"selector": "#name", "ok": True}
    assert state["result"]["clicked"] == "yes"
    assert state["result"]["notes"] == "abc"
    assert state["result"]["name"] == "way"
    assert "notes:input:abc" in state["result"]["events"]
    assert "name:input:way" in state["result"]["events"]
    assert "name:change:way" in state["result"]["events"]


def test_browser_wait_for_selector_times_out(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    _data(client.post(
        "/browser/page/navigate",
        json={"url": _page_url("<html><body><main>empty</main></body></html>")},
    ))

    response = client.post(
        "/browser/page/wait_for_selector",
        json={"selector": "#missing", "timeout": 1000},
    )

    assert response.status_code == 408
    assert response.json()["success"] is False


def test_browser_interaction_uses_active_tab(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    first_url = _page_url("<html><body><input id='shared' value='first'></body></html>")
    second_url = _page_url("<html><body><input id='shared' value='second'></body></html>")
    _data(client.post("/browser/page/navigate", json={"url": first_url}))
    second = _data(client.post("/browser/tabs", json={"url": second_url}))

    _data(client.post("/browser/page/fill", json={"selector": "#shared", "text": "active"}))
    active_value = _data(client.post(
        "/browser/page/evaluate",
        json={"script": "() => document.querySelector('#shared').value"},
    ))
    _data(client.put("/browser/tabs/0/activate"))
    first_value = _data(client.post(
        "/browser/page/evaluate",
        json={"script": "() => document.querySelector('#shared').value"},
    ))

    assert second["active"] is True
    assert active_value["result"] == "active"
    assert first_value["result"] == "first"
