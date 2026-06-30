from typing import Any


class SandboxAPIError(RuntimeError):
    def __init__(self, status_code: int, message: str, data: Any = None):
        super().__init__(message)
        self.status_code = status_code
        self.message = message
        self.data = data
