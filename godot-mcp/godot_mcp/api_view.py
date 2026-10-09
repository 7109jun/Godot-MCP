"""VIEW: inspect, project_state — AI 의 다음 행동을 위한 구조화된 상태."""
from __future__ import annotations
import time
from pathlib import Path

from . import api_runtime, context, security
from .errors import McpError
from .mechanics import registry as R
from .project import projectfile, tscn
from .project.manifest import Manifest
from .runtime import editor as editor_rt
from .runtime import game as game_rt
from .session import Session
from .util import pid_alive
from .util_yaml import dump
from .validation import logparse, syntax


def _scene_files(root: Path) -> list[Path]:
    return [p for p in sorted(root.rglob("*.tscn")) if not p.relative_to(root).as_posix().startswith((".godot", "addons/", "build/"))]


def _counts(root: Path) -> dict:
    checks = context.load_checks(root)
    log_errs = logparse.parse_log(game_rt.read_log(root))
    return {
        "errors": sum(1 for e in log_errs if e["severity"] == "error"),
        "warnings": sum(1 for e in log_errs if e["severity"] == "warning"),
        "last_syntax_check": _check_brief(checks.get("syntax")),
        "last_validate": _check_brief(checks.get("validate")),
    }


def _check_brief(c: dict | None) -> dict | None:
    if not c:
        return None
    return {"success": c.get("success"), "errors": len(c.get("errors", [])), "warnings": len(c.get("warnings", [])), "age_s": round(time.time() - c["time"])}


async def inspect(project: str | None = None, *, scene: str | None = None, depth: int = 6, props: bool = True, format: str = "json") -> dict | str:
    root = context.resolve_project(project)
    man = Manifest(root).load()
    pf = projectfile.parse(root / "project.godot")
    main = projectfile.get(pf, "application", "run/main_scene")
    out: dict = {"project": {"name": projectfile.get(pf, "application", "config/name"), "type": man.get("type") or "2d", "genre": man.get("genre"),
                             "main_scene": main}}
    target = api_runtime.resolve_scene(root, scene) if scene else main
    if target:
        r = await editor_rt.editor_call(root, "inspect_scene", {"path": target, "depth": depth, "props": props}, timeout=60)
        nodes: list[dict] = []

        def walk(n: dict, parent: str | None):
            item = {"name": n["name"], "type": n["type"]}
            if n.get("path"):
                item["path"] = n["path"]
            if parent is not None:
                item["parent"] = parent
            for k in ("script", "instance", "groups", "props"):
                if k in n:
                    item[k] = n[k]
            nodes.append(item)
            here = n.get("path", ".")
            for c in n.get("children", []):
                walk(c, here)

        walk(r["tree"], None)
        out["scene"] = {"name": r["tree"]["name"], "path": target, "node_count": r["node_count"]}
        out["nodes"] = nodes
    cnt = _counts(root)
    out["errors"] = cnt["errors"]
    out["warnings"] = cnt["warnings"]
    out["checks"] = {"syntax": cnt["last_syntax_check"], "validate": cnt["last_validate"]}
    if format == "yaml":
        return dump(out)
    return out


async def project_state(project: str | None = None, *, format: str = "json") -> dict | str:
    root = context.resolve_project(project)
    man = Manifest(root).load()
    pf = projectfile.parse(root / "project.godot")
    sess = Session(root).load()
    scenes = []
    for p in _scene_files(root):
        sc = tscn.parse(p)
        scenes.append({"path": security.to_res(root, p), "root": sc.nodes[0].type if sc.nodes else None, "nodes": len(sc.nodes)})
    scripts = [security.to_res(root, s) for s in syntax.list_scripts(root)]
    applied = list(man.get("mechanics", {}).keys())
    genre = man.get("genre")
    available = []
    if genre:
        for m in R.supported():
            key = m["name"].replace(" ", "_")
            if genre in m["genres"] and key not in applied:
                available.append(m["name"])
    gst = game_rt.status(root)
    changed = 0
    try:
        import json
        hashes = json.loads((root / ".godot_mcp" / "checked.json").read_text())
        from .util import sha1_file
        changed = sum(1 for s in syntax.list_scripts(root) if hashes.get(s.relative_to(root).as_posix()) != sha1_file(s))
    except (OSError, ValueError):
        changed = len(scripts)
    out = {
        "project": {"path": str(root), "name": projectfile.get(pf, "application", "config/name"), "type": man.get("type") or "2d", "genre": genre,
                    "main_scene": projectfile.get(pf, "application", "run/main_scene"),
                    "viewport": [projectfile.get(pf, "display", "window/size/viewport_width"), projectfile.get(pf, "display", "window/size/viewport_height")]},
        "mechanics": {"applied": [a.replace("_", " ") for a in applied], "available": available},
        "scenes": scenes, "scripts": scripts, "scripts_unchecked": changed,
        "autoloads": projectfile.autoloads(pf), "input_actions": sorted(projectfile.input_actions(pf)),
        "processes": {"editor": {"running": pid_alive((sess.get("editor") or {}).get("pid"))},
                      "game": {k: gst.get(k) for k in ("running", "pid", "scene", "duration", "exit_code") if gst.get(k) is not None}},
        "checks": {k: v for k, v in {"syntax": _counts(root)["last_syntax_check"], "validate": _counts(root)["last_validate"]}.items()},
        "runtime_errors": _counts(root)["errors"],
    }
    if format == "yaml":
        return dump(out)
    return out
