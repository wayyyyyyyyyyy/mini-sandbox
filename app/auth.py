import base64
import secrets
import time
from dataclasses import dataclass

import jwt
from fastapi import Header, HTTPException, Query

from .config import JWT_PUBLIC_KEY, SANDBOX_API_KEY, TICKET_TTL_SECONDS


@dataclass
class Ticket:
    token: str
    expires_at: float


_tickets: dict[str, Ticket] = {}


def require_api_key(
    x_sandbox_api_key: str | None = Header(default=None),
    authorization: str | None = Header(default=None),
    ticket: str | None = Query(default=None),
) -> None:
    if not SANDBOX_API_KEY and not JWT_PUBLIC_KEY:
        return

    if _valid_api_key(x_sandbox_api_key, authorization):
        return

    if _valid_jwt(authorization):
        return

    if ticket and consume_ticket(ticket):
        return

    raise HTTPException(status_code=401, detail="missing or invalid credentials")


def require_http_credentials(
    x_sandbox_api_key: str | None = None,
    authorization: str | None = None,
    ticket: str | None = None,
) -> None:
    if not SANDBOX_API_KEY and not JWT_PUBLIC_KEY:
        return
    if _valid_api_key(x_sandbox_api_key, authorization):
        return
    if _valid_jwt(authorization):
        return
    if ticket and consume_ticket(ticket):
        return
    raise HTTPException(status_code=401, detail="missing or invalid credentials")


def create_ticket() -> dict[str, int | str]:
    _cleanup_tickets()
    token = f"tk_{secrets.token_urlsafe(32)}"
    expires_in = TICKET_TTL_SECONDS
    _tickets[token] = Ticket(token=token, expires_at=time.time() + expires_in)
    return {"ticket": token, "expires_in": expires_in}


def consume_ticket(token: str) -> bool:
    _cleanup_tickets()
    ticket = _tickets.pop(token, None)
    if ticket is None:
        return False
    return ticket.expires_at >= time.time()


def _valid_api_key(x_sandbox_api_key: str | None, authorization: str | None) -> bool:
    if not SANDBOX_API_KEY:
        return False
    return x_sandbox_api_key == SANDBOX_API_KEY or authorization == f"Bearer {SANDBOX_API_KEY}"


def _valid_jwt(authorization: str | None) -> bool:
    if not JWT_PUBLIC_KEY or not authorization or not authorization.startswith("Bearer "):
        return False
    token = authorization.removeprefix("Bearer ").strip()
    public_key = _public_key_material(JWT_PUBLIC_KEY)
    try:
        jwt.decode(token, public_key, algorithms=["RS256"])
    except jwt.PyJWTError:
        return False
    return True


def _public_key_material(value: str) -> str:
    if "BEGIN PUBLIC KEY" in value:
        return value
    try:
        decoded = base64.b64decode(value, validate=True).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return value
    return decoded if "BEGIN PUBLIC KEY" in decoded else value


def _cleanup_tickets() -> None:
    now = time.time()
    expired = [token for token, ticket in _tickets.items() if ticket.expires_at < now]
    for token in expired:
        _tickets.pop(token, None)
