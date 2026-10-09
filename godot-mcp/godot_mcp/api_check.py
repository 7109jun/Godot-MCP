"""VIEW/검사 계열: syntax_check, validate, errors."""
from __future__ import annotations
import time
from pathlib import Path

from . import context, security
from .errors import McpError
from .runtime import editor as editor_rt
from .runtime import game as game_rt
from .validation import logparse, static, syntax


async def refresh_class_cache(project: Path) -> None:
    """class_name 스크립트가 새로 생겼으면 전역 클래스 캐시를 갱신 (헤드리스 파서가 새 class_name 을 알도록)."""
    cache = project / ".godot" / "global_script_class_cache.cfg"
    newest = 0.0
    for s in syntax.list_scripts(project):
        try:
            if "class_name" in s.read_text(errors="replace"):
                newest = max(newest, s.stat().st_mtime)
        except OSError:
            pass
    if newest == 0.0 or (cache.exists() and cache.stat().st_mtime >= newest):
        return
    if await editor_rt.editor_running(project):
        await editor_rt.editor_call(project, "scan", timeout=90)
    else:
        from . import config
        from .util import run_cmd
        await run_cmd([config.godot_binary(), "--headless", "--path", str(project), "--import"], timeout=120)


async def syntax_check(project: str | None = None, *, files: list[str] | None = None, changed: bool = False) -> dict:
    root = context.resolve_project(project)
    await refresh_class_cache(root)
    r = await syntax.syntax_check(root, files=files, changed=changed)
    context.save_check(root, "syntax", r) if not files and not changed else None
    r["summary"] = f"{len(r['errors'])} error(s), {len(r.get('warnings', []))} warning(s) in {r['checked_files']} file(s)"
    return r


async def validate(project: str | None = None, *, engine: bool = True, syntax_too: bool = False) -> dict:
    root = context.resolve_project(project)
    r = static.validate_static(root)
    if engine:
        paths = [static.res(root, p) for p in static.list_files(root, (".tscn", ".tres"))]
        if paths:
            try:
                eng = await editor_rt.editor_call(root, "check_resources", {"paths": paths}, timeout=120)
                for e in eng.get("results", []):
                    if not e.get("loadable") or not e.get("instantiable") or e.get("missing_deps"):
                        msg = e.get("error") or ("missing dependencies: " + ", ".join(e.get("missing_deps", [])))
                        r["errors"].append({"type": "broken_resource", "severity": "error", "file": e["path"], "message": f"엔진이 리소스를 로드/인스턴스화하지 못했습니다: {msg}", "source": "engine"})
                r["engine_checked"] = len(paths)
            except McpError as ex:
                r["warnings"].append({"type": "tool", "severity": "warning", "message": f"엔진 검사를 건너뜀: {ex.message}"})
    if syntax_too:
        s = await syntax_check(str(root))
        r["errors"] += s["errors"]
        r["warnings"] += s["warnings"]
        r["syntax_checked"] = s["checked_files"]
    r["success"] = not any(e["severity"] == "error" for e in r["errors"])
    cats: dict[str, int] = {}
    for e in r["errors"]:
        cats[e["type"]] = cats.get(e["type"], 0) + 1
    r["error_categories"] = cats
    r["summary"] = f"{len(r['errors'])} error(s), {len(r['warnings'])} warning(s) — syntax는 syntax_check() 로 별도 검사" if not syntax_too else f"{len(r['errors'])} error(s), {len(r['warnings'])} warning(s)"
    context.save_check(root, "validate", r)
    return r


async def errors(project: str | None = None, *, scope: str = "all", limit: int = 20, context_lines: int = 2) -> dict:
    """VIEW: 수집된 오류를 계층화해서 반환 (원본 로그 전체는 반환하지 않는다)."""
    root = context.resolve_project(project)
    if scope not in ("all", "runtime", "script", "editor", "static"):
        raise McpError("INVALID_ARGUMENT", "scope 는 all|runtime|script|editor|static 중 하나여야 합니다")
    entries: list[dict] = []
    gst = game_rt.status(root)
    if scope in ("all", "runtime", "script"):
        for e in game_rt.runtime_errors(root):
            e["source_log"] = "game"
            entries.append(e)
    if scope == "editor":  # 에디터 로그는 시작/임포트 과정의 과거 소음이 많아 명시적으로 요청할 때만 포함
        elog = root / ".godot_mcp" / "editor.log"
        if elog.exists():
            for e in logparse.parse_log(elog.read_text(errors="replace")):
                if "VK_KHR" in e["message"] or "vulkan" in str(e.get("source", "")).lower() or "rendering_context" in str(e.get("source", "")):
                    continue
                e["source_log"] = "editor"
                entries.append(e)
    if scope == "script":
        entries = [e for e in entries if e["type"] in ("script", "syntax")]
    elif scope == "runtime":
        entries = [e for e in entries if e["type"] != "syntax"]
    checks = context.load_checks(root)
    if scope in ("all", "static", "script"):
        for key in (("syntax",) if scope == "script" else ("syntax", "validate")):
            c = checks.get(key)
            if c and not c.get("success"):
                for e in c.get("errors", []):
                    e = dict(e)
                    e["source_log"] = f"last_{key}"
                    entries.append(e)
    # 중복 제거 (동일 type/file/line/message)
    seen, uniq = set(), []
    for e in entries:
        k = (e["type"], e.get("file"), e.get("line"), e["message"])
        if k in seen:
            continue
        seen.add(k)
        uniq.append(e)
    errs = [e for e in uniq if e["severity"] == "error"]
    warns = [e for e in uniq if e["severity"] == "warning"]
    for e in errs[:limit]:
        if "context" not in e:
            ctx = logparse.source_context(root, e.get("file"), e.get("line"), context_lines)
            if ctx:
                e["context"] = ctx
        if "column" not in e:
            col = logparse.guess_column(root, e)
            if col:
                e["column"] = col
    by_type: dict[str, int] = {}
    for e in errs:
        by_type[e["type"]] = by_type.get(e["type"], 0) + 1
    return {"count": len(errs), "summary": {"errors": len(errs), "warnings": len(warns), "by_type": by_type,
                                              "game": {"running": gst["running"], "exit_code": gst.get("exit_code")}},
            "errors": errs[:limit], "warnings": warns[:max(0, min(limit, 10))], "truncated": max(0, len(errs) - limit)}
