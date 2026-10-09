"""게임 매니페스트(.godot_mcp/game.json): 장르, 메카닉, 레이아웃, 검사 이력."""
from __future__ import annotations
import json
from pathlib import Path


class Manifest:
    def __init__(self, project: Path):
        self.path = project / ".godot_mcp" / "game.json"

    def load(self) -> dict:
        try:
            return json.loads(self.path.read_text())
        except (OSError, ValueError):
            return {"version": 1, "mechanics": {}}

    def save(self, data: dict) -> None:
        self.path.parent.mkdir(mode=0o700, exist_ok=True)
        self.path.write_text(json.dumps(data, indent=2, ensure_ascii=False))

    def update(self, **kw) -> dict:
        d = self.load()
        d.update(kw)
        self.save(d)
        return d
