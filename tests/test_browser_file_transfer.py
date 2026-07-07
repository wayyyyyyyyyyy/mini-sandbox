import base64
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from fastapi.testclient import TestClient

from app.main import app, browser_sessions


@pytest.fixture(autouse=True)
def close_browser_after_test():
    yield
    _close_browser_quietly()


@pytest.fixture
def download_origin():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _DownloadPageHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _client(monkeypatch, tmp_path):
    monkeypatch.setattr("app.auth.SANDBOX_API_KEY", "")
    monkeypatch.setattr("app.auth.JWT_PUBLIC_KEY", "")
    monkeypatch.setattr("app.security.WORKSPACE", tmp_path)
    monkeypatch.setattr("app.main.WORKSPACE", tmp_path)
    _close_browser_quietly()
    return TestClient(app)


def _close_browser_quietly():
    browser_sessions.close()


def _data(response):
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    return body["data"]


def _page_url(html: str) -> str:
    encoded = base64.b64encode(html.encode("utf-8")).decode("ascii")
    return f"data:text/html;base64,{encoded}"


class _DownloadPageHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/artifact.txt":
            content = b"browser download artifact\n"
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Disposition", 'attachment; filename="artifact.txt"')
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)
            return

        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(b'<html><body><a id="download" href="/artifact.txt">download</a></body></html>')

    def log_message(self, format, *args):
        return


def test_browser_upload_file_sets_input_files_from_workspace(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    (tmp_path / "fixtures").mkdir()
    (tmp_path / "fixtures" / "upload.txt").write_bytes(b"hello from workspace\n")
    _data(client.post(
        "/browser/page/navigate",
        json={
            "url": _page_url(
                """
                <html>
                  <body>
                    <input id="file" type="file">
                    <script>
                      window.uploaded = null;
                      document.getElementById('file').addEventListener('change', async (event) => {
                        const file = event.target.files[0];
                        window.uploaded = { name: file.name, text: await file.text() };
                      });
                    </script>
                  </body>
                </html>
                """
            )
        },
    ))

    uploaded = _data(client.post(
        "/browser/page/upload_file",
        json={"selector": "#file", "files": ["fixtures/upload.txt"]},
    ))
    observed = _data(client.post(
        "/browser/page/evaluate",
        json={"script": "() => window.uploaded"},
    ))

    assert uploaded == {"selector": "#file", "files": ["fixtures/upload.txt"], "ok": True}
    assert observed["result"] == {"name": "upload.txt", "text": "hello from workspace\n"}


def test_browser_downloads_are_saved_under_workspace_downloads(monkeypatch, tmp_path, download_origin):
    client = _client(monkeypatch, tmp_path)
    _data(client.post("/browser/page/navigate", json={"url": download_origin}))

    _data(client.post("/browser/page/click", json={"selector": "#download"}))
    download_path = _wait_for_download(tmp_path / "Downloads" / "artifact.txt")
    listed = _data(client.post("/file/list", json={"path": "Downloads"}))
    downloaded = client.get("/file/download", params={"path": "Downloads/artifact.txt"})

    assert download_path.read_bytes() == b"browser download artifact\n"
    assert any(entry["path"] == "Downloads/artifact.txt" for entry in listed["entries"])
    assert downloaded.status_code == 200
    assert downloaded.content == b"browser download artifact\n"


def test_browser_upload_rejects_missing_workspace_file(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    _data(client.post(
        "/browser/page/navigate",
        json={"url": _page_url("<html><body><input id='file' type='file'></body></html>")},
    ))

    response = client.post(
        "/browser/page/upload_file",
        json={"selector": "#file", "files": ["missing.txt"]},
    )

    assert response.status_code == 404
    assert response.json()["success"] is False


def _wait_for_download(path):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if path.exists():
            return path
        time.sleep(0.05)
    raise AssertionError(f"download did not appear: {path}")
