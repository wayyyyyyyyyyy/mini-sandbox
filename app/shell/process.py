from __future__ import annotations

import os
import subprocess
from pathlib import Path


def start_process(command: str, cwd: Path, env: dict[str, str]) -> tuple[subprocess.Popen[str], int | None]:
    if os.name == "nt":
        process = subprocess.Popen(
            command,
            cwd=cwd,
            env=env,
            shell=True,
            text=True,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            bufsize=1,
            encoding="utf-8",
            errors="replace",
        )
        return process, None

    import pty

    master_fd, slave_fd = pty.openpty()
    try:
        process = subprocess.Popen(
            command,
            cwd=cwd,
            env=env,
            shell=True,
            stdin=slave_fd,
            stdout=slave_fd,
            stderr=slave_fd,
            close_fds=True,
            text=False,
        )
    finally:
        os.close(slave_fd)
    return process, master_fd


def start_interactive_process(
    cwd: Path,
    env: dict[str, str],
    *,
    cols: int | None,
    rows: int | None,
) -> tuple[subprocess.Popen[str], int | None]:
    if os.name == "nt":
        process = subprocess.Popen(
            interactive_shell_command(),
            cwd=cwd,
            env=env,
            shell=False,
            text=True,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            bufsize=1,
            encoding="utf-8",
            errors="replace",
        )
        return process, None

    import pty

    master_fd, slave_fd = pty.openpty()
    if cols is not None and rows is not None:
        resize_pty(master_fd, cols=cols, rows=rows)
    try:
        process = subprocess.Popen(
            [interactive_shell_command()],
            cwd=cwd,
            env=env,
            stdin=slave_fd,
            stdout=slave_fd,
            stderr=slave_fd,
            close_fds=True,
            text=False,
        )
    finally:
        os.close(slave_fd)
    return process, master_fd


def interactive_shell_command() -> str:
    if os.name == "nt":
        return os.environ.get("COMSPEC", "cmd.exe")
    return os.environ.get("SHELL", "/bin/bash")


def resize_pty(master_fd: int, *, cols: int, rows: int) -> None:
    if os.name == "nt":
        return
    try:
        import fcntl
        import struct
        import termios

        size = struct.pack("HHHH", rows, cols, 0, 0)
        fcntl.ioctl(master_fd, termios.TIOCSWINSZ, size)
    except OSError:
        return
