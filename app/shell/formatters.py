from __future__ import annotations

from .models import ShellSession, utcnow


def shell_result(session: ShellSession) -> dict:
    with session.output_changed:
        return {
            "session_id": session.session_id,
            "command": session.current_command,
            "status": session.command_status,
            "output": session.output,
            "exit_code": session.exit_code,
        }


def shell_session_info(session: ShellSession) -> dict:
    now = utcnow()
    with session.output_changed:
        return {
            "working_dir": str(session.working_dir),
            "created_at": session.created_at.isoformat(),
            "last_used_at": session.last_used_at.isoformat(),
            "age_seconds": int((now - session.created_at).total_seconds()),
            "status": session.command_status,
            "current_command": session.current_command,
            "no_change_timeout": session.no_change_timeout,
        }
