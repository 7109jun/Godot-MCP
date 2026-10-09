"""요청 검증: 프로젝트 경계, 경로 탈출 차단, 허용 프로젝트."""
from __future__ import annotations
import os
import re
from pathlib import Path

from . import config
from .errors import McpError

PROTECTED_PREFIXES = (".godot/", ".godot_mcp/", "addons/godot_mcp/", ".git/")


def _inside(child: Path, parent: Path) -> bool:
    try:
        child.relative_to(parent)
        return True
    except ValueError:
        return False


def check_allowed_root(path: Path) -> Path:
    real = path.expanduser().resolve()
    for root in config.allowed_roots():
        if _inside(real, root):
            return real
    raise McpError("PATH_NOT_ALLOWED", f"허용된 작업 영역 밖의 프로젝트 경로입니다: {real}",
                   allowed=[str(r) for r in config.allowed_roots()])


def check_project(path: str | Path) -> Path:
    root = check_allowed_root(Path(path))
    if not (root / "project.godot").is_file():
        raise McpError("NOT_A_PROJECT", f"project.godot 가 없습니다: {root}")
    return root


def safe_name(name: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9_\-]+", "_", name.strip()).strip("_")
    if not slug:
        raise McpError("INVALID_NAME", f"유효하지 않은 이름: {name!r}")
    return slug


def resolve(project: Path, p: str, *, write: bool = False) -> Path:
    """res:// 또는 프로젝트 상대 경로를 절대 경로로. 경로 탈출/심볼릭 링크 탈출/보호영역 쓰기를 차단."""
    if p is None or not str(p).strip():
        raise McpError("INVALID_PATH", "빈 경로")
    raw = str(p).strip()
    if "\x00" in raw:
        raise McpError("INVALID_PATH", "경로에 NUL 문자가 있습니다")
    rel = raw[6:] if raw.startswith("res://") else raw
    if rel.startswith("/") or re.match(r"^[A-Za-z]:[\\/]", rel):
        abs_p = Path(rel)
        try:
            rel = str(abs_p.resolve().relative_to(project))
        except ValueError:
            raise McpError("PATH_ESCAPE", f"프로젝트 밖 경로는 허용되지 않습니다: {raw}")
    if "\\" in rel:
        raise McpError("INVALID_PATH", f"역슬래시 경로는 허용되지 않습니다: {raw}")
    parts = [x for x in rel.split("/") if x not in ("", ".")]
    if any(x == ".." for x in parts):
        raise McpError("PATH_ESCAPE", f"'..' 경로 탈출이 차단되었습니다: {raw}")
    target = project.joinpath(*parts)
    real = Path(os.path.realpath(target))
    if not _inside(real, project):
        raise McpError("PATH_ESCAPE", f"프로젝트 경계를 벗어나는 경로입니다: {raw}")
    norm = "/".join(parts)
    if write and any((norm + "/").startswith(pfx) or norm == pfx.rstrip("/") for pfx in PROTECTED_PREFIXES):
        raise McpError("PATH_PROTECTED", f"보호된 경로에는 쓸 수 없습니다: {raw}")
    return target


def to_res(project: Path, abs_path: Path | str) -> str:
    return "res://" + Path(abs_path).resolve().relative_to(project).as_posix()
