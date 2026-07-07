from fastapi import Depends, FastAPI
from fastapi.responses import Response

from ..auth import require_api_key
from .manager import BrowserSessionManager


def register_browser_media_routes(app: FastAPI, browser_sessions: BrowserSessionManager) -> None:
    @app.get("/browser/screenshot")
    def browser_screenshot(
        format: str = "png",
        quality: int | None = None,
        _: None = Depends(require_api_key),
    ) -> Response:
        content, headers = browser_sessions.screenshot(image_format=format, quality=quality)
        media_type = "image/jpeg" if format in {"jpg", "jpeg"} else "image/png"
        return Response(content=content, media_type=media_type, headers=headers)
