"""build(): export_presets.cfg 보장 -> Godot 헤드리스 export -> 산출물 검증(+ 실행 스모크 테스트)."""
from __future__ import annotations
import os
import time
from pathlib import Path

from .. import api_check, config, context, security
from ..errors import McpError
from ..project import projectfile
from ..runtime import editor as editor_rt
from ..util import run_cmd
from ..validation import logparse, syntax

PRESET = """[preset.0]

name="Linux"
platform="Linux"
runnable=true
advanced_options=false
dedicated_server=false
custom_features=""
export_filter="all_resources"
include_filter=""
exclude_filter=".godot_mcp/*, build/*"
export_path="build/linux/{name}.x86_64"
encryption_include_filters=""
encryption_exclude_filters=""
encrypt_pck=false
encrypt_directory=false

[preset.0.options]

custom_template/debug=""
custom_template/release=""
debug/export_console_wrapper=1
binary_format/embed_pck=true
texture_format/s3tc_bptc=true
texture_format/etc2_astc=false
binary_format/architecture="x86_64"
"""

TARGETS = ("linux", "pck")


def ensure_presets(root: Path, name: str) -> bool:
    p = root / "export_presets.cfg"
    if p.exists() and 'platform="Linux"' in p.read_text():
        return False
    p.write_text(PRESET.format(name=name))
    return True


def _templates_ok(mode: str) -> tuple[bool, str]:
    import subprocess
    ver = subprocess.run([config.godot_binary(), "--version", "--headless"], capture_output=True, text=True).stdout.strip()
    short = ".".join(ver.split(".")[:3]) if ver.count(".") >= 3 else ver
    parts = ver.split(".")
    tver = f"{parts[0]}.{parts[1]}.{parts[2]}.{parts[3]}" if len(parts) >= 4 else ver
    d = Path.home() / ".local/share/godot/export_templates" / tver
    f = d / f"linux_{'release' if mode == 'release' else 'debug'}.x86_64"
    return f.exists(), str(f)


async def build(project: str | None = None, *, target: str = "linux", mode: str = "release", verify: bool = True, force: bool = False) -> dict:
    root = context.resolve_project(project)
    if target not in TARGETS:
        raise McpError("UNSUPPORTED_TARGET", f"지원하지 않는 타깃입니다: {target!r} (Ubuntu/POSIX 우선 정책: 지원 타깃 {list(TARGETS)})")
    if mode not in ("release", "debug"):
        raise McpError("INVALID_ARGUMENT", "mode 는 release|debug 여야 합니다")
    t0 = time.monotonic()
    pf = projectfile.parse(root / "project.godot")
    name = security.safe_name(projectfile.get(pf, "application", "config/name", root.name))
    if not force:
        s = await api_check.syntax_check(str(root))
        if not s["success"]:
            return {"success": False, "target": target, "mode": mode, "output": None, "exit_code": None, "errors": s["errors"][:10], "warnings": [],
                    "duration": round(time.monotonic() - t0, 2), "blocked_by": "syntax_check", "next": "fix() 로 문법 오류를 수정한 뒤 다시 build() 하세요 (force=true 로 무시 가능)"}
    created_presets = ensure_presets(root, name)
    if target == "linux":
        ok, tpl = _templates_ok(mode)
        if not ok:
            raise McpError("EXPORT_TEMPLATE_MISSING", f"Linux export 템플릿이 없습니다: {tpl}", hint="scripts/install_godot.sh <버전> --templates 를 실행하세요")
    if await editor_rt.editor_running(root):
        await editor_rt.editor_call(root, "scan", timeout=90)
    elif not (root / ".godot" / "imported").exists():
        await run_cmd([config.godot_binary(), "--headless", "--path", str(root), "--import"], timeout=180)
    out_dir = root / "build" / ("linux" if target == "linux" else "pck")
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / (f"{name}.x86_64" if target == "linux" else f"{name}.pck")
    out.unlink(missing_ok=True)
    flag = "--export-pack" if target == "pck" else ("--export-release" if mode == "release" else "--export-debug")
    code, log, dur = await run_cmd([config.godot_binary(), "--headless", "--path", str(root), flag, "Linux", str(out)], timeout=300)
    entries = logparse.parse_log(log)
    errors = [e for e in entries if e["severity"] == "error"]
    warnings = [e for e in entries if e["severity"] == "warning"]
    for e in errors:
        ctx = logparse.source_context(root, e.get("file"), e.get("line"))
        if ctx:
            e["context"] = ctx
    produced = out.exists() and out.stat().st_size > 0
    res: dict = {"target": target, "mode": mode if target == "linux" else "pack", "output": str(out) if produced else None,
                 "size_bytes": out.stat().st_size if produced else 0, "exit_code": code, "errors": errors, "warnings": warnings[:10],
                 "preset_created": created_presets, "duration": round(time.monotonic() - t0, 2)}
    success = produced and code == 0 and not errors
    if produced and not success and code == 0:
        success = True  # 산출물 정상이고 비치명 에러 로그만 있는 경우
        res["note"] = "산출물은 생성되었지만 export 로그에 오류가 있습니다"
    if success and target == "linux" and verify:
        os.chmod(out, 0o755)
        c2, o2, d2 = await run_cmd([str(out), "--headless", "--quit-after", "30", "--audio-driver", "Dummy"], timeout=60)
        v_errs = [e for e in logparse.parse_log(o2) if e["severity"] == "error"]
        res["verify"] = {"launched": c2 == 0, "exit_code": c2, "errors": v_errs[:5], "duration": round(d2, 2)}
        success = success and c2 == 0 and not v_errs
    if not produced:
        res["log_tail"] = "\n".join(log.splitlines()[-15:])
    res["success"] = bool(success)
    return res
