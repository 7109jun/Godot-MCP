"""test(): syntax -> validate -> launch -> runtime observation -> (gameplay) -> 결과."""
from __future__ import annotations
import time
from pathlib import Path

from . import api_check, api_runtime, context
from .errors import McpError
from .project.manifest import Manifest
from .runtime import game as game_rt
from .validation import logparse


def _step(name: str, ok: bool, t0: float, **details) -> dict:
    return {"name": name, "success": ok, "duration": round(time.monotonic() - t0, 2), **details}


def _player(state: dict) -> dict | None:
    for e in state.get("entities", []):
        if e.get("group") == "player" or e.get("name") == "Player":
            return e
    return None


async def test(project: str | None = None, *, scene: str | None = None, all: bool = False, gameplay: bool = False, observe: float = 2.5,
               keep_running: bool = False) -> dict:
    root = context.resolve_project(project)
    man = Manifest(root).load()
    steps: list[dict] = []
    t_all = time.monotonic()
    blocked = False

    t0 = time.monotonic()
    syn = await api_check.syntax_check(str(root))
    steps.append(_step("syntax_check", syn["success"], t0, errors=syn["errors"][:8], checked_files=syn["checked_files"]))
    t0 = time.monotonic()
    val = await api_check.validate(str(root))
    steps.append(_step("validate", val["success"], t0, errors=val["errors"][:8], warnings=len(val["warnings"]), checked=val.get("checked")))
    if not syn["success"] or not val["success"]:
        blocked = True
        steps.append({"name": "launch", "success": False, "skipped": True, "reason": "정적 검사 실패로 실행 테스트를 건너뜀 (fix() 후 재시도)"})
    else:
        target_scene = scene
        if gameplay and not scene and "player_movement" in man.get("mechanics", {}):
            target_scene = "res://scenes/main.tscn"
        t0 = time.monotonic()
        r = await api_runtime.run(str(root), scene=target_scene, observe=observe, timeout=None)
        launch_ok = r["running"] and r["bridge"] and not r["runtime_errors"]
        steps.append(_step("launch", launch_ok, t0, running=r["running"], bridge=r["bridge"], exit_code=r.get("exit_code"), runtime_errors=r["runtime_errors"][:8],
                           **({"log_tail": r.get("log_tail")} if not r["running"] else {})))
        if r["running"]:
            t0 = time.monotonic()
            try:
                st = await api_runtime.game_state(str(root), depth=1)
                alive = st.get("frames", 0) > 10 and st.get("fps", 0) > 0
                steps.append(_step("runtime_observation", alive and st["errors"]["count"] == 0, t0, fps=st.get("fps"), frames=st.get("frames"), scene=st.get("scene", {}).get("name"),
                                   entities=len(st.get("entities", [])), errors=st["errors"]))
            except McpError as e:
                steps.append(_step("runtime_observation", False, t0, error=e.message))
            t0 = time.monotonic()
            try:
                shot = await api_runtime.screenshot(str(root), auto_run=False)
                from .runtime import game as g
                raw = await g.game_call(root, "screenshot", {"max_width": 320})
                colors = raw.get("distinct_colors", 0)
                steps.append(_step("visual_check", colors > 2, t0, distinct_colors=colors, screenshot=shot["path"],
                                   **({"warning": "화면이 거의 단색입니다 (렌더링/카메라/씬 구성 확인)"} if colors <= 2 else {})))
            except McpError as e:
                steps.append(_step("visual_check", False, t0, error=e.message, skipped_reason="headless 또는 렌더링 불가"))
            if gameplay or all:
                steps.append(await _gameplay(root, man))
            t0 = time.monotonic()
            await api_runtime.stop(str(root)) if not keep_running else None
            errs = [e for e in logparse.parse_log(game_rt.read_log(root)) if e["severity"] == "error"]
            steps.append(_step("runtime_errors", not errs, t0, count=len(errs), errors=[{k: e.get(k) for k in ("type", "file", "line", "message")} for e in errs[:8]]))
    failed = [s["name"] for s in steps if not s["success"] and not s.get("skipped")]
    return {"success": not failed and not blocked, "steps": steps, "failed": failed, "duration": round(time.monotonic() - t_all, 2),
            "summary": ("모든 검사 통과" if not failed and not blocked else f"실패: {', '.join(failed) or 'static checks'}"),
            **({"next": "fix() 로 자동 수정 후 test() 를 다시 실행하세요"} if failed else {})}


async def _gameplay(root: Path, man: dict) -> dict:
    t0 = time.monotonic()
    genre = man.get("genre")
    mechs = man.get("mechanics", {})
    if "player_movement" not in mechs:
        return {"name": "gameplay", "success": True, "skipped": True, "reason": "플레이어 메카닉이 없어 건너뜀", "duration": 0}
    checks: list[dict] = []
    k = 0.05 if genre == "3d" else 1.0  # 3D 는 미터 단위: 임계값을 축소
    try:
        base = _player(await game_rt.game_call(root, "game_state", {"depth": 0, "max_nodes": 1}))
        if base is None:
            return _step("gameplay", False, t0, error="player 그룹 엔티티를 찾지 못했습니다")
        await game_rt.game_call(root, "input", {"steps": [{"action": "move_right", "hold_ms": 500}]}, timeout=15)
        after = _player(await game_rt.game_call(root, "game_state", {"depth": 0, "max_nodes": 1}))
        dx = after["position"][0] - base["position"][0]
        checks.append({"check": "move_right", "success": dx > 10 * k, "dx": round(dx, 1)})
        if genre == "topdown":
            before = after
            await game_rt.game_call(root, "input", {"steps": [{"action": "move_down", "hold_ms": 400}]}, timeout=15)
            a2 = _player(await game_rt.game_call(root, "game_state", {"depth": 0, "max_nodes": 1}))
            checks.append({"check": "move_down", "success": a2["position"][1] - before["position"][1] > 10 * k, "dy": round(a2["position"][1] - before["position"][1], 1)})
        if genre in ("platformer", "3d"):
            import asyncio
            ground = _player(await game_rt.game_call(root, "game_state", {"depth": 0, "max_nodes": 1}))
            input_task = asyncio.create_task(game_rt.game_call(root, "input", {"steps": [{"action": "jump", "hold_ms": 120}]}, timeout=15))
            await asyncio.sleep(0.25)
            air = _player(await game_rt.game_call(root, "game_state", {"depth": 0, "max_nodes": 1}))
            await input_task
            rise = ground["position"][1] - air["position"][1] if genre == "platformer" else air["position"][1] - ground["position"][1]
            checks.append({"check": "jump", "success": rise > 8 * k, "rise": round(rise, 1)})
        errs = [e for e in logparse.parse_log(game_rt.read_log(root)) if e["severity"] == "error"]
        checks.append({"check": "no_runtime_errors_during_play", "success": not errs, "count": len(errs)})
    except McpError as e:
        return _step("gameplay", False, t0, error=e.message, checks=checks)
    return _step("gameplay", all(c["success"] for c in checks), t0, checks=checks)
