from fastapi import FastAPI

from .content_routes import register_file_content_routes
from .search_routes import register_file_search_routes
from .transfer_routes import register_file_transfer_routes
from .watch import FileWatchManager
from .watch_routes import register_file_watch_routes


def register_file_routes(app: FastAPI, file_watchers: FileWatchManager) -> None:
    register_file_content_routes(app)
    register_file_search_routes(app)
    register_file_transfer_routes(app)
    register_file_watch_routes(app, file_watchers)
