from collections.abc import Callable
from pathlib import Path

from fastapi import FastAPI

from ...shell_sessions import ShellSessionManager
from .rest import register_shell_rest_routes
from .sessions import register_shell_session_routes
from .websocket import register_shell_websocket_routes


def register_shell_routes(
    app: FastAPI,
    shell_sessions: ShellSessionManager,
    resolve_exec_dir: Callable[[str], Path],
) -> None:
    register_shell_websocket_routes(app, shell_sessions, resolve_exec_dir)
    register_shell_rest_routes(app, shell_sessions, resolve_exec_dir)
    register_shell_session_routes(app, shell_sessions, resolve_exec_dir)
