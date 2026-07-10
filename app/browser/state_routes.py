import json

from fastapi import Depends, FastAPI, HTTPException

from ..auth import require_api_key
from ..core.paths import relative_path as _relative
from ..schemas import BrowserStatePathRequest, BrowserStateResult
from ..security import ensure_file_size_allowed, resolve_workspace_path
from .manager import BrowserSessionManager


def register_browser_state_routes(app: FastAPI, browser_sessions: BrowserSessionManager) -> None:
    @app.post("/browser/state/save", response_model=BrowserStateResult)
    def browser_state_save(
        request: BrowserStatePathRequest,
        _: None = Depends(require_api_key),
    ) -> BrowserStateResult:
        path = resolve_workspace_path(request.path)
        state = browser_sessions.save_state()
        content = json.dumps(state, indent=2, sort_keys=True).encode("utf-8")
        ensure_file_size_allowed(content)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return BrowserStateResult(
            path=_relative(path),
            cookies=len(state["cookies"]),
            origins=len(state["origins"]),
        )

    @app.post("/browser/state/load", response_model=BrowserStateResult)
    def browser_state_load(
        request: BrowserStatePathRequest,
        _: None = Depends(require_api_key),
    ) -> BrowserStateResult:
        path = resolve_workspace_path(request.path)
        if not path.exists() or not path.is_file():
            raise HTTPException(status_code=404, detail=f"browser state file not found: {request.path}")
        try:
            state = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=400, detail=f"invalid browser state JSON: {request.path}") from exc
        if not isinstance(state, dict):
            raise HTTPException(status_code=400, detail="browser state must be a JSON object")
        restored = browser_sessions.load_state(state)
        return BrowserStateResult(path=_relative(path), **restored)
