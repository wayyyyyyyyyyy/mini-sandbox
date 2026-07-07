from __future__ import annotations

import sys
from pathlib import Path

from ..config import MAX_COMMAND_OUTPUT_CHARS, WORKSPACE


def workspace() -> Path:
    legacy_module = sys.modules.get("app.bash_sessions")
    if legacy_module is not None:
        return getattr(legacy_module, "WORKSPACE", WORKSPACE)
    return WORKSPACE


def max_command_output_chars() -> int:
    legacy_module = sys.modules.get("app.bash_sessions")
    if legacy_module is not None:
        return getattr(legacy_module, "MAX_COMMAND_OUTPUT_CHARS", MAX_COMMAND_OUTPUT_CHARS)
    return MAX_COMMAND_OUTPUT_CHARS
