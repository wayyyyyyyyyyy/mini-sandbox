from __future__ import annotations

import os
import subprocess
from typing import TextIO

from .models import ShellSession
from .runtime_config import max_shell_output_chars


def append_output(session: ShellSession, chunk: str) -> None:
    session.output += chunk
    max_chars = max_shell_output_chars()
    if max_chars > 0 and len(session.output) > max_chars:
        session.output = session.output[-max_chars:]


def read_process_output(
    session: ShellSession,
    process: subprocess.Popen[str],
    master_fd: int | None,
) -> None:
    if master_fd is not None:
        read_pty_output(session, master_fd)
    elif process.stdout is not None:
        read_text_output(session, process.stdout)
    exit_code = process.wait()
    with session.output_changed:
        if session.current_process is process:
            session.exit_code = exit_code
            session.current_master_fd = None
            if session.status == "closed":
                pass
            elif session.killed:
                session.status = "killed"
            else:
                session.status = "ready"
            session.output_changed.notify_all()


def read_text_output(session: ShellSession, stream: TextIO) -> None:
    for chunk in iter(stream.readline, ""):
        with session.output_changed:
            append_output(session, chunk)
            session.output_changed.notify_all()


def read_pty_output(session: ShellSession, master_fd: int) -> None:
    try:
        while True:
            try:
                chunk = os.read(master_fd, 4096)
            except OSError:
                break
            if not chunk:
                break
            with session.output_changed:
                append_output(session, chunk.decode("utf-8", errors="replace"))
                session.output_changed.notify_all()
    finally:
        try:
            os.close(master_fd)
        except OSError:
            pass
