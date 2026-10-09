"""현재 프로젝트 컨텍스트(요청 간 유지) + 프로젝트 해석/검증."""
from __future__ import annotations
import json
from pathlib import Path

from . import config, security
from .errors import McpError


def _cur_file() -> Path:
    return config.state_dir() / "current_project.json"


def set_current(project: Path) -> None:
    _cur_file().write_text(json.dumps({"project": str(project)}))


def get_current() -> Path | None:
    try:
        p = Path(json.loads(_cur_file().read_text())["project"])
        return p if (p / "project.godot").is_file() else None
    except (OSError, ValueError, KeyError):
        return None


def resolve_project(project: str | None = None) -> Path:
    """project 인자 → 절대 경로(허용 영역 검사). 생략 시 현재 프로젝트."""
    if project:
        p = Path(project).expanduser()
        if not p.is_absolute() and not (p / "project.godot").exists():
            cand = config.workspace() / project  # 이름만 준 경우
            if (cand / "project.godot").exists():
                p = cand
        root = security.check_project(p)
    else:
        cur = get_current()
        if cur is None:
            raise McpError("NO_PROJECT", "현재 프로젝트가 없습니다. create_project/create_game 을 호출하거나 project 인자를 지정하세요.")
        root = security.check_project(cur)
    set_current(root)
    return root


def save_check(project: Path, key: str, data: dict) -> None:
    p = project / ".godot_mcp" / "last_check.json"
    try:
        cur = json.loads(p.read_text())
    except (OSError, ValueError):
        cur = {}
    import time
    cur[key] = {"time": time.time(), "success": data.get("success"), "errors": data.get("errors", []), "warnings": data.get("warnings", []),
                "checked": data.get("checked") or data.get("checked_files")}
    p.parent.mkdir(mode=0o700, exist_ok=True)
    p.write_text(json.dumps(cur))


def load_checks(project: Path) -> dict:
    try:
        return json.loads((project / ".godot_mcp" / "last_check.json").read_text())
    except (OSError, ValueError):
        return {}
