"""프로젝트별 세션 파일(.godot_mcp/session.json): 토큰, 포트, 에디터/게임 프로세스 정보.

상태를 프로세스가 아닌 디스크+프로세스에 두므로, MCP 서버/CLI 호출이 달라도 같은 에디터/게임을 공유한다.
"""
from __future__ import annotations
import json
import os
import secrets
import socket
import tempfile
from pathlib import Path


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Session:
    def __init__(self, project: Path):
        self.project = project
        self.dir = project / ".godot_mcp"
        self.path = self.dir / "session.json"

    def load(self) -> dict:
        try:
            return json.loads(self.path.read_text())
        except (OSError, ValueError):
            return {}

    def save(self, data: dict) -> None:
        self.dir.mkdir(mode=0o700, exist_ok=True)
        os.chmod(self.dir, 0o700)
        fd, tmp = tempfile.mkstemp(dir=self.dir, prefix=".session.")
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2)
        os.chmod(tmp, 0o600)
        os.replace(tmp, self.path)

    def ensure(self) -> dict:
        """토큰/포트를 보장한다. 포트가 사용 중이면 새로 할당."""
        d = self.load()
        changed = False
        if not d.get("token"):
            d["token"] = secrets.token_urlsafe(32)
            changed = True
        for key in ("editor_port", "game_port"):
            if not d.get(key):
                d[key] = free_port()
                changed = True
        if changed:
            self.save(d)
        return d

    def update(self, **kw) -> dict:
        d = self.ensure()
        d.update(kw)
        self.save(d)
        return d

    def reset_port(self, key: str) -> int:
        d = self.ensure()
        d[key] = free_port()
        self.save(d)
        return d[key]
