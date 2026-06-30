import os
from pathlib import Path


def _path_from_env(name: str, default: str) -> Path:
    return Path(os.getenv(name, default)).expanduser().resolve()


WORKSPACE = _path_from_env("WORKSPACE", "/workspace")
DEFAULT_COMMAND_TIMEOUT = float(os.getenv("DEFAULT_COMMAND_TIMEOUT", "30"))
MAX_COMMAND_TIMEOUT = float(os.getenv("MAX_COMMAND_TIMEOUT", "120"))
MAX_FILE_BYTES = int(os.getenv("MAX_FILE_BYTES", str(1024 * 1024)))
MAX_COMMAND_OUTPUT_CHARS = int(os.getenv("MAX_COMMAND_OUTPUT_CHARS", "30000"))
MAX_SHELL_OUTPUT_CHARS = int(os.getenv("MAX_SHELL_OUTPUT_CHARS", "30000"))
MAX_BASH_SESSIONS = int(os.getenv("MAX_BASH_SESSIONS", "32"))
MAX_SHELL_SESSIONS = int(os.getenv("MAX_SHELL_SESSIONS", "32"))
MAX_FILE_WATCHERS = int(os.getenv("MAX_FILE_WATCHERS", "64"))
MAX_FILE_WATCH_EVENTS = int(os.getenv("MAX_FILE_WATCH_EVENTS", "1000"))
SHELL_SESSION_IDLE_TIMEOUT_SECONDS = int(os.getenv("SHELL_SESSION_IDLE_TIMEOUT_SECONDS", "1800"))
SANDBOX_API_KEY = os.getenv("SANDBOX_API_KEY", "")
JWT_PUBLIC_KEY = os.getenv("JWT_PUBLIC_KEY", "")
TICKET_TTL_SECONDS = int(os.getenv("TICKET_TTL_SECONDS", "30"))
