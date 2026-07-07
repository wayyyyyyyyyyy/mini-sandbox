from __future__ import annotations

import os

from .. import security


def prepare_jupyter_environment() -> None:
    root = security.WORKSPACE
    paths = {
        "IPYTHONDIR": root / ".ipython",
        "JUPYTER_CONFIG_DIR": root / ".jupyter" / "config",
        "JUPYTER_DATA_DIR": root / ".jupyter" / "data",
        "JUPYTER_RUNTIME_DIR": root / ".jupyter" / "runtime",
    }
    for name, path in paths.items():
        path.mkdir(parents=True, exist_ok=True)
        os.environ[name] = str(path)
