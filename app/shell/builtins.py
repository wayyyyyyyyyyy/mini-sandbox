from __future__ import annotations

import re
from pathlib import Path

from fastapi import HTTPException

from .models import ShellSession, utcnow


def apply_stateful_shell_builtin(session: ShellSession, command: str) -> bool:
    parts = [part.strip() for part in command.split("&&")]
    if not parts:
        return False
    handled_any = False
    for part in parts:
        if part.startswith("cd "):
            apply_cd(session, part[3:].strip())
            handled_any = True
            continue
        if part.startswith("export "):
            apply_export(session, part[7:].strip())
            handled_any = True
            continue
        return False
    if handled_any:
        with session.output_changed:
            session.exit_code = 0
            session.current_process = None
            session.output_changed.notify_all()
    return handled_any


def apply_cd(session: ShellSession, target: str) -> None:
    next_dir = (session.working_dir / target).resolve() if not Path(target).is_absolute() else Path(target).resolve()
    try:
        next_dir.relative_to(session.workspace)
    except ValueError as exc:
        raise HTTPException(status_code=403, detail=f"path escapes workspace: {target}") from exc
    if not next_dir.exists() or not next_dir.is_dir():
        raise HTTPException(status_code=400, detail=f"directory not found: {target}")
    with session.output_changed:
        session.working_dir = next_dir
        session.last_used_at = utcnow()
        session.output_changed.notify_all()


def apply_export(session: ShellSession, assignment: str) -> None:
    match = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$", assignment)
    if not match:
        raise HTTPException(status_code=400, detail=f"invalid export: {assignment}")
    key, value = match.groups()
    with session.output_changed:
        session.env[key] = value.strip("'\"")
        session.last_used_at = utcnow()
        session.output_changed.notify_all()
