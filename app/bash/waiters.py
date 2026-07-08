from __future__ import annotations

import subprocess
import time

from .models import BashCommand
from .process import kill_process


def watch_timeout(command: BashCommand) -> None:
    if not command.hard_timeout:
        return
    deadline = command.started_at + command.hard_timeout
    remaining = deadline - time.monotonic()
    if remaining > 0:
        time.sleep(remaining)
    if command.process.poll() is None:
        kill_process(command)
        try:
            command.process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            pass
        with command.output_changed:
            command.timed_out = True
            command.output_changed.notify_all()


def wait_for_command(command: BashCommand, timeout: float) -> None:
    try:
        command.process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        return
    wait_for_streams(command, 1)
    with command.output_changed:
        command.output_changed.notify_all()


def wait_for_output(command: BashCommand, offset: int, stderr_offset: int, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    with command.output_changed:
        while command.process.poll() is None:
            if len(command.stdout) > offset or len(command.stderr) > stderr_offset:
                return
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            command.output_changed.wait(timeout=remaining)


def wait_for_streams(command: BashCommand, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    with command.output_changed:
        while not command.stdout_closed or not command.stderr_closed:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            command.output_changed.wait(timeout=remaining)
