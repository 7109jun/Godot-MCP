from __future__ import annotations
from typing import Any


class McpError(Exception):
    """구조화된 오류. 도구 결과로 그대로 직렬화된다."""

    def __init__(self, code: str, message: str, **details: Any):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details

    def to_dict(self) -> dict:
        d = {"success": False, "error": {"code": self.code, "message": self.message}}
        if self.details:
            d["error"].update(self.details)
        return d
