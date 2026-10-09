"""ACTION/VIEW(런타임): run, stop, game_state, screenshot, play."""
from __future__ import annotations
import base64
import re
import time
from pathlib import Path

from . import context, security
from .errors import McpError
from .project import projectfile
from .runtime import editor as editor_rt
from .runtime import game as game_rt
from .validation import logparse

BRIDGE_AUTOLOAD = "res://addons/godot_mcp/game_bridge.gd"


async def ensure_bridge(root: Path) -> None:
    """게임 브릿지(autoload)와 플러그인이 설치되어 있는지 보장."""
    changed = editor_rt.install_plugin(root)
    pf = projectfile.parse(root / "project.godot")
    if projectfile.autoloads(pf).get("McpBridge") != BRIDGE_AUTOLOAD:
        await editor_rt.editor_call(root, "set_autoloads", {"autoloads": {"McpBridge": BRIDGE_AUTOLOAD}})
        await editor_rt.editor_call(root, "scan", timeout=60)
    elif changed and await editor_rt.editor_running(root):
        await editor_rt.editor_call(root, "scan", timeout=60)


def resolve_scene(root: Path, scene: str | None) -> str | None:
    if not scene:
        return None
    s = scene.strip()
    if s.startswith("res://") or s.endswith(".tscn"):
        p = security.resolve(root, s)
    else:
        snake = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", s)
        snake = re.sub(r"[^A-Za-z0-9]+", "_", snake).strip("_").lower()
        p = security.resolve(root, f"scenes/{snake}.tscn")
        if not p.exists():
            p = security.resolve(root, f"{snake}.tscn")
    if not p.is_file():
        cands = sorted(x.relative_to(root).as_posix() for x in root.rglob("*.tscn") if not x.relative_to(root).as_posix().startswith((".godot", "addons/")))
        raise McpError("SCENE_NOT_FOUND", f"씬을 찾을 수 없습니다: {scene}", available=["res://" + c for c in cands])
    return security.to_res(root, p)


async def run(project: str | None = None, *, scene: str | None = None, timeout: float | None = None, observe: float = 1.5,
              headless: bool = False) -> dict:
    root = context.resolve_project(project)
    scene_res = resolve_scene(root, scene)
    await ensure_bridge(root)
    if timeout is not None and timeout <= 0:
        raise McpError("INVALID_ARGUMENT", "timeout 은 0보다 커야 합니다")
    r = await game_rt.start(root, scene=scene_res, timeout=timeout, observe=max(0.0, observe), headless=headless)
    r["success"] = r["running"] and not r["runtime_errors"] if timeout is None else not r["runtime_errors"]
    if r.get("exit_code") == 124:
        r["ended_by"] = "timeout"
        r["success"] = not r["runtime_errors"]
    r["summary"] = (f"running pid={r['process']['pid']}" if r["running"] else f"exited (code {r.get('exit_code')})") + f", {len(r['runtime_errors'])} runtime error(s)"
    return r


async def stop(project: str | None = None, *, editor: bool = False) -> dict:
    root = context.resolve_project(project)
    r = await game_rt.stop(root)
    errs = [e for e in game_rt.runtime_errors(root) if e["severity"] == "error"]
    r["runtime_errors"] = len(errs)
    r["success"] = True
    if editor:
        r["editor"] = await editor_rt.stop_editor(root)
    return r


async def game_state(project: str | None = None, *, depth: int = 3, max_nodes: int = 60) -> dict:
    root = context.resolve_project(project)
    st = game_rt.status(root)
    if not st["running"]:
        errs = [e for e in game_rt.runtime_errors(root) if e["severity"] == "error"]
        raise McpError("GAME_NOT_RUNNING", "실행 중인 게임이 없습니다. run() 을 먼저 호출하세요.", last_run=st if st.get("has_run") else None, runtime_errors=len(errs))
    s = await game_rt.game_call(root, "game_state", {"depth": depth, "max_nodes": max_nodes})
    errs = [e for e in game_rt.runtime_errors(root) if e["severity"] == "error"]
    s["errors"] = {"count": len(errs), "latest": [{k: e.get(k) for k in ("type", "file", "line", "message")} for e in errs[-3:]]}
    s["process"] = {"pid": st["pid"], "uptime": st["duration"]}
    s["success"] = True
    return s


async def screenshot(project: str | None = None, *, target: str = "game", max_width: int = 960, auto_run: bool = True) -> dict:
    root = context.resolve_project(project)
    if target not in ("game", "editor"):
        raise McpError("INVALID_ARGUMENT", "target 은 game|editor 중 하나여야 합니다")
    started = False
    if target == "game":
        if not game_rt.status(root)["running"]:
            if not auto_run:
                raise McpError("GAME_NOT_RUNNING", "실행 중인 게임이 없습니다 (auto_run=true 로 자동 실행 가능)")
            await run(str(root), observe=1.0)
            started = True
            if not game_rt.status(root)["running"]:
                raise McpError("GAME_NOT_RUNNING", "게임이 시작 직후 종료되어 스크린샷을 찍을 수 없습니다. errors() 를 확인하세요.")
        await _wait_frames(root)
        r = await game_rt.game_call(root, "screenshot", {"max_width": max_width}, timeout=20)
    else:
        await editor_rt.get_editor(root, mode="xvfb")  # 에디터 GUI 가 있어야 캡처 가능 (기본 에디터는 headless)
        r = await editor_rt.editor_call(root, "editor_screenshot", {"max_width": max_width}, timeout=60)
    png = base64.b64decode(r["png_base64"])
    d = root / ".godot_mcp" / "screenshots"
    d.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = d / f"{target}_{int(time.time() * 1000)}.png"
    path.write_bytes(png)
    for old in sorted(d.glob("*.png"))[:-20]:
        old.unlink(missing_ok=True)
    return {"success": True, "target": target, "path": str(path), "width": r["width"], "height": r["height"], "bytes": len(png),
            "auto_started_game": started, "png": png}


async def _wait_frames(root: Path) -> None:
    for _ in range(20):
        try:
            s = await game_rt.game_call(root, "game_state", {"depth": 0, "max_nodes": 1})
            if s.get("frames", 0) > 5:
                return
        except McpError:
            pass
        import asyncio
        await asyncio.sleep(0.15)


async def play(actions: list, project: str | None = None, *, observe: bool = True) -> dict:
    """게임에 입력 시퀀스를 주입하고 전/후 상태를 비교해서 반환.

    actions: ["jump", {"action": "move_right", "hold_ms": 600}, {"action": "jump", "hold_ms": 80, "wait_ms": 200}]
    """
    root = context.resolve_project(project)
    if not game_rt.status(root)["running"]:
        raise McpError("GAME_NOT_RUNNING", "실행 중인 게임이 없습니다. run() 을 먼저 호출하세요.")
    steps = []
    for a in actions:
        if isinstance(a, str):
            steps.append({"action": a, "hold_ms": 120})
        elif isinstance(a, dict) and a.get("action"):
            steps.append({"action": str(a["action"]), "hold_ms": int(a.get("hold_ms", 120)), "wait_ms": int(a.get("wait_ms", 0)), "strength": float(a.get("strength", 1.0))})
        else:
            raise McpError("INVALID_ARGUMENT", f"잘못된 액션: {a!r}")
    total = sum(s.get("hold_ms", 0) + s.get("wait_ms", 0) for s in steps) / 1000.0
    before = await game_rt.game_call(root, "game_state", {"depth": 0, "max_nodes": 1}) if observe else None
    n_err0 = len(game_rt.runtime_errors(root))
    await game_rt.game_call(root, "input", {"steps": steps}, timeout=total + 10)
    after = await game_rt.game_call(root, "game_state", {"depth": 0, "max_nodes": 1}) if observe else None
    errs = [e for e in game_rt.runtime_errors(root) if e["severity"] == "error"]
    out = {"success": True, "played": len(steps), "duration": round(total, 2)}
    if observe:
        out["entities_before"] = _brief(before)
        out["entities_after"] = _brief(after)
        out["events"] = [e for e in after.get("recent_events", [])][-10:]
    out["new_runtime_errors"] = max(0, len(game_rt.runtime_errors(root)) - n_err0)
    out["runtime_errors"] = [{k: e.get(k) for k in ("type", "file", "line", "message")} for e in errs[-3:]]
    return out


def _brief(state: dict | None) -> list[dict]:
    if not state:
        return []
    out = []
    for e in state.get("entities", [])[:8]:
        out.append({k: e.get(k) for k in ("name", "group", "position", "velocity", "on_floor", "vars") if k in e})
    return out
