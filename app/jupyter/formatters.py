from __future__ import annotations

from typing import Any

from .models import JupyterSession, utcnow


def session_info(session: JupyterSession) -> dict[str, Any]:
    now = utcnow()
    return {
        "kernel_name": session.kernel_name,
        "last_used": session.last_used_at.isoformat(),
        "age_seconds": int((now - session.created_at).total_seconds()),
    }
