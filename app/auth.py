from fastapi import Header, HTTPException

from .config import SANDBOX_API_KEY


def require_api_key(
    x_sandbox_api_key: str | None = Header(default=None),
    authorization: str | None = Header(default=None),
) -> None:
    if not SANDBOX_API_KEY:
        return

    if x_sandbox_api_key == SANDBOX_API_KEY:
        return

    if authorization == f"Bearer {SANDBOX_API_KEY}":
        return

    raise HTTPException(status_code=401, detail="missing or invalid API key")
