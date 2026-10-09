"""환경 설정: Godot 바이너리, 작업 공간, 플러그인 경로."""
from __future__ import annotations
import os
import shutil
from pathlib import Path

from .errors import McpError

PACKAGE_DIR = Path(__file__).resolve().parent


def godot_binary() -> str:
    cand = os.environ.get("GODOT_MCP_GODOT")
    if cand and os.access(cand, os.X_OK):
        return cand
    for name in ("godot", "godot4"):
        p = shutil.which(name)
        if p:
            return p
    for p in (Path.home() / ".local/godot/godot", Path("/opt/godot/godot"), Path("/usr/local/bin/godot")):
        if p.exists() and os.access(p, os.X_OK):
            return str(p)
    raise McpError("GODOT_NOT_FOUND", "Godot 바이너리를 찾을 수 없습니다. scripts/install_godot.sh 를 실행하거나 GODOT_MCP_GODOT 를 설정하세요.")


def plugin_source() -> Path:
    env = os.environ.get("GODOT_MCP_PLUGIN_DIR")
    cands = [Path(env)] if env else []
    cands += [PACKAGE_DIR.parent / "plugin" / "godot_mcp", PACKAGE_DIR / "_plugin" / "godot_mcp"]
    for c in cands:
        if (c / "plugin.cfg").exists():
            return c
    raise McpError("PLUGIN_NOT_FOUND", "Godot 플러그인 소스(plugin/godot_mcp)를 찾을 수 없습니다.")


def workspace() -> Path:
    return Path(os.environ.get("GODOT_MCP_WORKSPACE", str(Path.home() / "godot-projects"))).expanduser().resolve()


def allowed_roots() -> list[Path]:
    raw = os.environ.get("GODOT_MCP_ALLOWED_ROOTS")
    roots = [Path(p).expanduser().resolve() for p in raw.split(":") if p] if raw else []
    roots.append(workspace())
    if not raw:
        roots.append(Path.cwd().resolve())
    out: list[Path] = []
    for r in roots:
        if r not in out:
            out.append(r)
    return out


def state_dir() -> Path:
    d = Path(os.environ.get("GODOT_MCP_STATE_DIR", str(Path.home() / ".cache" / "godot-mcp")))
    d.mkdir(parents=True, exist_ok=True)
    return d


def editor_mode() -> str:
    """'headless'(기본, 빠름: 렌더링 없음) 또는 'xvfb'(에디터 GUI 를 가상 디스플레이에서 실행: 에디터 스크린샷 가능).
    소프트웨어 렌더링(llvmpipe) 에디터는 프레임이 매우 느려 자동화에는 headless 가 훨씬 빠르다."""
    return os.environ.get("GODOT_MCP_EDITOR_MODE", "headless")
