from fastapi import FastAPI

from .lifecycle_routes import register_browser_lifecycle_routes
from .manager import BrowserSessionManager
from .media_routes import register_browser_media_routes
from .network_routes import register_browser_network_routes
from .page_routes import register_browser_page_routes
from .state_routes import register_browser_state_routes
from .tabs_routes import register_browser_tabs_routes


def register_browser_routes(app: FastAPI, browser_sessions: BrowserSessionManager) -> None:
    register_browser_lifecycle_routes(app, browser_sessions)
    register_browser_page_routes(app, browser_sessions)
    register_browser_network_routes(app, browser_sessions)
    register_browser_state_routes(app, browser_sessions)
    register_browser_media_routes(app, browser_sessions)
    register_browser_tabs_routes(app, browser_sessions)
