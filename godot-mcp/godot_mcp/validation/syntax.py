"""syntax_check: Godot 의 실제 GDScript 파서(ResourceLoader.load -> GDScript::reload)를 헤드리스로 호출."""
from __future__ import annotations
import json
import time
from pathlib import Path

from .. import config, security
from ..util import run_cmd, sha1_file
from . import logparse

CHECKER = """extends Node
## 오토로드가 등록된 뒤(씬 모드)에 스크립트를 로드해야 'Identifier not found: <Autoload>' 오탐이 없다.
func _ready() -> void:
	for f in OS.get_cmdline_user_args():
		var s = ResourceLoader.load(f, "GDScript", ResourceLoader.CACHE_MODE_IGNORE)
		print("MCP_CHECK ", "OK " if s != null else "FAIL ", f)
	get_tree().quit()
"""

CHECKER_SCENE = """[gd_scene load_steps=2 format=3]

[ext_resource type="Script" path="res://.godot_mcp/_checker.gd" id="1"]

[node name="Checker" type="Node"]
script = ExtResource("1")
"""


def list_scripts(project: Path) -> list[Path]:
    out = []
    for p in sorted(project.rglob("*.gd")):
        rel = p.relative_to(project).as_posix()
        if rel.startswith((".godot/", "addons/godot_mcp/", ".godot_mcp/", "build/")):
            continue
        out.append(p)
    return out


def _hash_state(project: Path) -> Path:
    return project / ".godot_mcp" / "checked.json"


def _load_hashes(project: Path) -> dict:
    try:
        return json.loads(_hash_state(project).read_text())
    except (OSError, ValueError):
        return {}


def _save_hashes(project: Path, data: dict) -> None:
    _hash_state(project).parent.mkdir(mode=0o700, exist_ok=True)
    _hash_state(project).write_text(json.dumps(data))


async def check_scripts(project: Path, files: list[Path], timeout: float = 90) -> dict:
    t0 = time.monotonic()
    if not files:
        return {"success": True, "errors": [], "warnings": [], "checked_files": 0, "duration": 0.0}
    checker = project / ".godot_mcp" / "_checker.gd"
    checker.parent.mkdir(mode=0o700, exist_ok=True)
    checker.write_text(CHECKER)
    (checker.parent / "_checker.tscn").write_text(CHECKER_SCENE)
    res_files = [security.to_res(project, f) for f in files]
    code, out, _ = await run_cmd(
        [config.godot_binary(), "--headless", "--path", str(project), "res://.godot_mcp/_checker.tscn", "--", *res_files],
        timeout=timeout)
    failed = {ln.split("FAIL ", 1)[1].strip() for ln in out.splitlines() if ln.startswith("MCP_CHECK FAIL ")}
    ok = {ln.split("OK ", 1)[1].strip() for ln in out.splitlines() if ln.startswith("MCP_CHECK OK ")}
    wanted = set(res_files)
    entries = [e for e in logparse.parse_log(out) if e.get("file") in wanted or (e["type"] == "syntax" and e.get("file", "").endswith(".gd"))]
    errors, warnings = [], []
    for e in entries:
        if e["severity"] == "warning":
            warnings.append(e)
            continue
        e["type"] = "syntax" if e["type"] in ("script", "syntax") else e["type"]
        col = logparse.guess_column(project, e)
        if col:
            e["column"] = col
        e["context"] = logparse.source_context(project, e.get("file"), e.get("line"))
        errors.append(e)
    # 로드 실패했는데 파싱된 오류가 없는 파일(드문 경우)은 일반 오류로 보고
    reported = {e.get("file") for e in errors}
    for f in sorted(failed):
        if f not in reported:
            errors.append({"type": "syntax", "severity": "error", "file": f, "line": None, "message": "script failed to load (see Godot log)", "count": 1})
    processed = failed | ok
    unprocessed = [f for f in res_files if f not in processed]
    if code == -9:
        errors.append({"type": "tool", "severity": "error", "message": f"syntax check timed out after {timeout}s"})
    elif unprocessed:  # 검사기가 끝까지 실행되지 못함: 조용히 '성공' 으로 보고하면 안 된다
        tail = "\n".join(out.strip().splitlines()[-12:])
        errors.append({"type": "tool", "severity": "error", "message": f"Godot 검사기가 {len(unprocessed)}개 파일을 처리하지 못했습니다 (프로젝트가 시작되지 않았을 수 있음)",
                       "unprocessed": unprocessed[:10], "log_tail": tail})
    return {"success": not errors, "errors": errors, "warnings": warnings,
            "checked_files": len(files), "ok_files": sorted(set(res_files) - {e.get("file") for e in errors} - failed), "duration": round(time.monotonic() - t0, 2)}


async def syntax_check(project: Path, *, files: list[str] | None = None, changed: bool = False) -> dict:
    all_scripts = list_scripts(project)
    if files:
        targets = []
        for f in files:
            p = security.resolve(project, f)
            if not p.is_file() or p.suffix != ".gd":
                matches = [s for s in all_scripts if s.name == f or s.relative_to(project).as_posix() == f]
                if not matches:
                    return {"success": False, "errors": [{"type": "tool", "severity": "error", "message": f"GDScript file not found: {f}"}], "checked_files": 0}
                targets.extend(matches)
            else:
                targets.append(p)
        mode = "files"
    elif changed:
        hashes = _load_hashes(project)
        targets = [s for s in all_scripts if hashes.get(s.relative_to(project).as_posix()) != sha1_file(s)]
        mode = "changed"
    else:
        targets, mode = all_scripts, "all"
    result = await check_scripts(project, targets)
    result["mode"] = mode
    # 성공한 파일 해시 기록 (--changed 용)
    hashes = _load_hashes(project)
    bad = {e.get("file") for e in result["errors"]}
    for s in targets:
        rel = s.relative_to(project).as_posix()
        if f"res://{rel}" in bad:
            hashes.pop(rel, None)
        else:
            hashes[rel] = sha1_file(s)
    _save_hashes(project, hashes)
    return result
