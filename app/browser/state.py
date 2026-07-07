from __future__ import annotations

from typing import Any

from fastapi import HTTPException

from .scripts import (
    local_storage_state_script as _local_storage_state_script,
    restore_local_storage_script as _restore_local_storage_script,
)


class BrowserStateMixin:
    def save_state(self) -> dict[str, Any]:
        with self._lock:
            tab = self._current_tab()
            cookies = tab.client.call("Network.getAllCookies").get("cookies", [])
            origin = self._evaluate(tab, _local_storage_state_script())
            origins = [origin] if origin is not None else []
            return {
                "cookies": cookies,
                "origins": origins,
            }

    def load_state(self, state: dict[str, Any]) -> dict[str, int]:
        with self._lock:
            tab = self._current_tab()
            cookies = state.get("cookies", [])
            if not isinstance(cookies, list):
                raise HTTPException(status_code=400, detail="browser state cookies must be a list")
            cookie_params = [cookie_param(cookie) for cookie in cookies if isinstance(cookie, dict)]
            if cookie_params:
                tab.client.call("Network.setCookies", {"cookies": cookie_params})

            current_origin = self._evaluate(tab, "location.origin")
            origins = state.get("origins", [])
            if not isinstance(origins, list):
                raise HTTPException(status_code=400, detail="browser state origins must be a list")
            restored_origins = 0
            for origin_state in origins:
                if not isinstance(origin_state, dict) or origin_state.get("origin") != current_origin:
                    continue
                local_storage = origin_state.get("localStorage", {})
                if not isinstance(local_storage, dict):
                    raise HTTPException(status_code=400, detail="browser state localStorage must be an object")
                self._evaluate(tab, _restore_local_storage_script(local_storage))
                restored_origins += 1
            return {
                "cookies": len(cookie_params),
                "origins": restored_origins,
            }


def cookie_param(cookie: dict[str, Any]) -> dict[str, Any]:
    required = {"name", "value"}
    if not required.issubset(cookie):
        raise HTTPException(status_code=400, detail="browser cookie missing name or value")

    param: dict[str, Any] = {
        "name": str(cookie["name"]),
        "value": str(cookie["value"]),
    }
    for key in (
        "domain",
        "path",
        "secure",
        "httpOnly",
        "sameSite",
        "priority",
        "sameParty",
        "sourceScheme",
        "sourcePort",
    ):
        if key in cookie and cookie[key] is not None:
            param[key] = cookie[key]
    if not cookie.get("session") and cookie.get("expires") is not None:
        param["expires"] = cookie["expires"]
    return param
