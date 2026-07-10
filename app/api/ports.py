import os
import platform
import re
import socket
import subprocess

from fastapi import Depends, FastAPI

from ..auth import require_api_key
from ..schemas import PortInfo, PortListResult

_LOCAL_HOSTS = {"127.0.0.1", "0.0.0.0", "::1", "::"}


def register_port_routes(app: FastAPI) -> None:
    @app.get("/ports", response_model=PortListResult)
    def list_ports(_: None = Depends(require_api_key)) -> PortListResult:
        return PortListResult(ports=discover_listening_ports())


def discover_listening_ports() -> list[PortInfo]:
    candidates = _candidate_listening_ports()
    ports = []
    seen: set[tuple[str, int]] = set()
    for host, port, pid, process_name in sorted(candidates, key=lambda item: (item[1], item[0])):
        normalized_host = _normalize_host(host)
        key = (normalized_host, port)
        if key in seen or not _is_local_host(normalized_host):
            continue
        seen.add(key)
        if not _can_connect(normalized_host, port):
            continue
        ports.append(
            PortInfo(
                port=port,
                host=normalized_host,
                pid=pid,
                process_name=process_name,
                proxy_url=f"/proxy/{port}/",
            )
        )
    return ports


def _candidate_listening_ports() -> set[tuple[str, int, int | None, str | None]]:
    if os.name == "nt":
        return _windows_netstat_ports()
    ports = _linux_proc_net_ports()
    if ports:
        return ports
    return _ss_ports()


def _windows_netstat_ports() -> set[tuple[str, int, int | None, str | None]]:
    try:
        output = subprocess.check_output(
            ["netstat", "-ano", "-p", "tcp"],
            text=True,
            stderr=subprocess.DEVNULL,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return set()

    ports: set[tuple[str, int, int | None, str | None]] = set()
    for line in output.splitlines():
        parts = line.split()
        if len(parts) < 5 or parts[0].upper() != "TCP" or parts[3].upper() != "LISTENING":
            continue
        parsed = _parse_address(parts[1])
        if parsed is None:
            continue
        host, port = parsed
        pid = _parse_int(parts[4])
        ports.add((host, port, pid, None))
    return ports


def _linux_proc_net_ports() -> set[tuple[str, int, int | None, str | None]]:
    ports: set[tuple[str, int, int | None, str | None]] = set()
    ports.update(_read_proc_net_tcp("/proc/net/tcp", ipv6=False))
    ports.update(_read_proc_net_tcp("/proc/net/tcp6", ipv6=True))
    return ports


def _read_proc_net_tcp(path: str, *, ipv6: bool) -> set[tuple[str, int, int | None, str | None]]:
    try:
        lines = open(path, encoding="utf-8").read().splitlines()
    except OSError:
        return set()

    ports: set[tuple[str, int, int | None, str | None]] = set()
    for line in lines[1:]:
        parts = line.split()
        if len(parts) < 4 or parts[3] != "0A":
            continue
        address = parts[1]
        if ":" not in address:
            continue
        host_hex, port_hex = address.split(":", 1)
        port = _parse_hex_port(port_hex)
        if port is None:
            continue
        host = _decode_proc_host(host_hex, ipv6=ipv6)
        ports.add((host, port, None, None))
    return ports


def _ss_ports() -> set[tuple[str, int, int | None, str | None]]:
    try:
        output = subprocess.check_output(
            ["ss", "-ltnp"],
            text=True,
            stderr=subprocess.DEVNULL,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return set()

    ports: set[tuple[str, int, int | None, str | None]] = set()
    for line in output.splitlines():
        if "LISTEN" not in line:
            continue
        match = re.search(r"(?P<host>\S+):(?P<port>\d+)\s", line)
        if not match:
            continue
        host = match.group("host").strip("[]")
        port = _parse_int(match.group("port"))
        if port is not None:
            ports.add((host, port, None, None))
    return ports


def _parse_address(value: str) -> tuple[str, int] | None:
    value = value.strip()
    if value.startswith("[") and "]:" in value:
        host, port_text = value.rsplit("]:", 1)
        return host.removeprefix("["), int(port_text)
    if ":" not in value:
        return None
    host, port_text = value.rsplit(":", 1)
    port = _parse_int(port_text)
    if port is None:
        return None
    return host, port


def _decode_proc_host(value: str, *, ipv6: bool) -> str:
    if ipv6:
        if value == "00000000000000000000000000000000":
            return "::"
        if value == "00000000000000000000000001000000":
            return "::1"
        return "::"
    raw = bytes.fromhex(value)
    return socket.inet_ntop(socket.AF_INET, raw[::-1])


def _parse_hex_port(value: str) -> int | None:
    try:
        return int(value, 16)
    except ValueError:
        return None


def _parse_int(value: str) -> int | None:
    try:
        return int(value)
    except ValueError:
        return None


def _normalize_host(host: str) -> str:
    host = host.strip("[]")
    if host in {"*", "0.0.0.0"}:
        return "0.0.0.0"
    if host in {":::", "::"}:
        return "::"
    return host


def _is_local_host(host: str) -> bool:
    return host in _LOCAL_HOSTS


def _can_connect(host: str, port: int) -> bool:
    targets = ["127.0.0.1"] if host in {"0.0.0.0", "::"} else [host]
    for target in targets:
        family = socket.AF_INET6 if ":" in target else socket.AF_INET
        try:
            with socket.socket(family, socket.SOCK_STREAM) as sock:
                sock.settimeout(0.2)
                sock.connect((target, port))
                return True
        except OSError:
            continue
    return False
