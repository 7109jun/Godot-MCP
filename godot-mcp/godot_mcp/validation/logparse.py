"""Godot 로그(stdout/stderr) -> 구조화된 오류 목록."""
from __future__ import annotations
import re
from pathlib import Path

_HEAD = re.compile(r"^(SCRIPT ERROR|USER SCRIPT ERROR|ERROR|USER ERROR|WARNING|USER WARNING|SCRIPT WARNING|SHADER ERROR): ?(.*)$")
_AT = re.compile(r"^\s+at: (.*)$")
_LOC = re.compile(r"\((res://[^:()]+):(\d+)\)")
_CPP = re.compile(r"\(([^():]+\.(?:cpp|h|mm)):(\d+)\)")
_BT = re.compile(r"^\s+\[(\d+)\] (.*)$")

NOISE = (
    "ALSA lib", "Godot Engine v", "OpenGL API", "Vulkan API", "Mesa", "libGL", "Using ", "WARNING: ObjectDB instances leaked",
    "leaked at exit", "resources still in use at exit", "ObjectDB", "Orphan StringName", "StringName: ", "Pages in use exist at exit",
    "RID allocations", "RIDs of type", "WARNING: 1 RID", "XDG_RUNTIME_DIR", "pcm.c", "audio_driver", "All audio drivers failed",
    "initialize (servers/audio_server", "init_output_device", "Condition \"status < 0\"", "ERROR: BUG: Unreferenced static string",
    "XInput", "libdecor", "Wayland", "dbus", "Unknown PCM",
)


def _is_noise(text: str) -> bool:
    return any(n in text for n in NOISE)


def parse_log(text: str) -> list[dict]:
    lines = text.splitlines()
    out: list[dict] = []
    i = 0
    while i < len(lines):
        m = _HEAD.match(lines[i])
        if not m:
            i += 1
            continue
        kind, msg = m.group(1), m.group(2).strip()
        raw = [lines[i]]
        j = i + 1
        at_lines: list[str] = []
        stack: list[str] = []
        while j < len(lines):
            am = _AT.match(lines[j])
            if am:
                at_lines.append(am.group(1))
                raw.append(lines[j])
                j += 1
                continue
            if lines[j].strip().startswith("GDScript backtrace"):
                raw.append(lines[j])
                j += 1
                while j < len(lines) and _BT.match(lines[j]):
                    stack.append(_BT.match(lines[j]).group(2))
                    raw.append(lines[j])
                    j += 1
                continue
            break
        i = j
        if _is_noise("\n".join(raw)):
            continue
        entry = _build(kind, msg, at_lines, stack)
        if entry:
            out.append(entry)
    return _dedupe(out)


def _build(kind: str, msg: str, at_lines: list[str], stack: list[str]) -> dict | None:
    severity = "warning" if "WARNING" in kind else "error"
    file = line = None
    func = None
    for a in at_lines + stack:
        lm = _LOC.search(a)
        if lm:
            file, line = lm.group(1), int(lm.group(2))
            func = a.split(" (")[0].strip()
            break
    source = None
    if file is None and at_lines:
        cm = _CPP.search(at_lines[0])
        source = f"{cm.group(1)}:{cm.group(2)}" if cm else at_lines[0]
    if msg.startswith("Parse Error:"):
        etype, msg = "syntax", msg[len("Parse Error:"):].strip()
    elif msg.startswith("Compile Error:"):
        etype, msg = "syntax", msg[len("Compile Error:"):].strip()
    elif "SCRIPT" in kind:
        etype = "script"
    elif "SHADER" in kind:
        etype = "shader"
    elif file and file.endswith(".gd"):
        etype = "script"
    else:
        etype = "runtime"
    if msg.startswith("Failed to load script") or "Cannot open file" in msg or "Failed loading resource" in msg:
        etype = "resource" if etype == "runtime" else etype
    entry: dict = {"type": etype, "severity": severity, "message": msg}
    if file:
        entry["file"] = file
        entry["line"] = line
    if func:
        entry["function"] = func
    if source:
        entry["source"] = source
    nm = re.search(r'Node not found: "([^"]+)" \(relative to "([^"]+)"\)', msg)
    if nm:
        entry["node"] = nm.group(1)
        entry["scene"] = nm.group(2).rsplit("/", 1)[-1]
    if stack:
        entry["stack"] = stack[:8]
    return entry


def _dedupe(entries: list[dict]) -> list[dict]:
    seen: dict[tuple, dict] = {}
    out: list[dict] = []
    for e in entries:
        key = (e["type"], e.get("file"), e.get("line"), e["message"])
        if key in seen:
            seen[key]["count"] = seen[key].get("count", 1) + 1
        else:
            seen[key] = e
            e["count"] = 1
            out.append(e)
    return out


def source_context(project: Path, file: str | None, line: int | None, radius: int = 2) -> list[dict]:
    if not file or not line or not file.startswith("res://"):
        return []
    p = project / file[6:]
    try:
        src = p.read_text(errors="replace").splitlines()
    except OSError:
        return []
    lo, hi = max(1, line - radius), min(len(src), line + radius)
    return [{"line": n, "text": src[n - 1], "error": n == line} for n in range(lo, hi + 1)]


def guess_column(project: Path, e: dict) -> int | None:
    """Godot 은 열 번호를 주지 않으므로 메시지/소스 줄에서 최선의 추정."""
    file, line = e.get("file"), e.get("line")
    if not file or not line or not file.startswith("res://"):
        return None
    try:
        src = (project / file[6:]).read_text(errors="replace").splitlines()[line - 1]
    except (OSError, IndexError):
        return None
    msg = e.get("message", "")
    m = re.search(r'Identifier "([^"]+)"', msg) or re.search(r"property or key '([^']+)'", msg) or re.search(r'member "([^"]+)"', msg)
    if m:
        idx = src.find(m.group(1))
        if idx >= 0:
            return idx + 1
    if msg.startswith("Expected \":\"") or "Expected closing" in msg or "Expected indented block" in msg:
        return len(src.rstrip()) + 1
    stripped = len(src) - len(src.lstrip())
    return stripped + 1


def summarize(entries: list[dict]) -> dict:
    return {
        "errors": sum(1 for e in entries if e["severity"] == "error"),
        "warnings": sum(1 for e in entries if e["severity"] == "warning"),
    }


def link_script_context(entries: list[dict], project: Path) -> list[dict]:
    """파일/줄 정보가 없는 엔진(C++) 오류에, 바로 뒤따르는 스크립트 오류의 파일과 실제 참조 위치(추정)를 연결한다.

    예: 'Node not found: "EnemySpawner"' 는 C++ 위치만 출력되지만, 뒤따르는 SCRIPT ERROR(res://x.gd:5) 의
    파일에서 "EnemySpawner" 를 참조하는 줄(<=5)을 찾아 file/line 으로 채운다 (inferred=true).
    """
    for i, e in enumerate(entries):
        if e.get("file") or e["severity"] != "error" or i + 1 >= len(entries):
            continue
        nxt = entries[i + 1]
        if not nxt.get("file") or nxt["severity"] != "error":
            continue
        e["file"] = nxt["file"]
        e["related"] = {"type": nxt["type"], "line": nxt.get("line"), "message": nxt["message"]}
        needle = e.get("node") or ""
        try:
            src = (project / nxt["file"][6:]).read_text(errors="replace").splitlines()
        except OSError:
            continue
        upto = nxt.get("line") or len(src)
        if needle:
            hit = next((k for k in range(min(upto, len(src)) - 1, -1, -1) if needle.split("/")[-1] in src[k]), None)
            if hit is not None:
                e["line"] = hit + 1
                e["inferred"] = True
                continue
        e["line"] = nxt.get("line")
        e["inferred"] = True
    return entries
