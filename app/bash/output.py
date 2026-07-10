from __future__ import annotations

from .models import BashCommand
from .runtime_config import max_command_output_chars


def read_stream(command: BashCommand, stream_name: str) -> None:
    stream = getattr(command.process, stream_name)
    if stream is None:
        return

    for chunk in iter(stream.readline, ""):
        with command.output_changed:
            existing = getattr(command, stream_name)
            setattr(command, stream_name, existing + chunk)
            command.output_changed.notify_all()

    with command.output_changed:
        setattr(command, f"{stream_name}_closed", True)
        command.output_changed.notify_all()


def limit_output(value: str, max_chars: int | None = None) -> tuple[str, bool, int]:
    limit = max_command_output_chars() if max_chars is None else max_chars
    output_bytes = len(value.encode("utf-8"))
    if limit <= 0 or len(value) <= limit:
        return value, False, output_bytes

    marker = f"[output truncated: showing last {limit} characters of {output_bytes} bytes]\n"
    return marker + value[-limit:], True, output_bytes
