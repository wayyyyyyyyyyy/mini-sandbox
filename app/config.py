import os
from pathlib import Path


def _path_from_env(name: str, default: str) -> Path:
    return Path(os.getenv(name, default)).expanduser().resolve()


WORKSPACE = _path_from_env("WORKSPACE", "/workspace")
DEFAULT_COMMAND_TIMEOUT = float(os.getenv("DEFAULT_COMMAND_TIMEOUT", "30"))
MAX_COMMAND_TIMEOUT = float(os.getenv("MAX_COMMAND_TIMEOUT", "120"))
MAX_FILE_BYTES = int(os.getenv("MAX_FILE_BYTES", str(1024 * 1024)))
MAX_COMMAND_OUTPUT_CHARS = int(os.getenv("MAX_COMMAND_OUTPUT_CHARS", "30000"))
SANDBOX_API_KEY = os.getenv("SANDBOX_API_KEY", "")
