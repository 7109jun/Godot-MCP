"""project.godot 읽기 전용 파서 (쓰기는 항상 Godot 플러그인을 통해 수행한다)."""
from __future__ import annotations
import re
from pathlib import Path

_KEY = re.compile(r"^([A-Za-z0-9_./\-]+)=(.*)$")


def parse(path: Path) -> dict:
    """반환: {"sections": {section: {key: raw_value}}, "duplicates": [(section,key,line)], "lines": {(section,key): lineno}}"""
    sections: dict[str, dict[str, str]] = {"": {}}
    dups: list[tuple] = []
    linenos: dict[tuple, int] = {}
    cur = ""
    last_key: tuple | None = None
    for i, line in enumerate(path.read_text(errors="replace").splitlines(), 1):
        s = line.rstrip("\n")
        if s.startswith(";") or not s.strip():
            continue
        m = re.match(r"^\[([^\]]+)\]\s*$", s)
        if m:
            cur = m.group(1)
            sections.setdefault(cur, {})
            last_key = None
            continue
        km = _KEY.match(s)
        if km:
            key, val = km.group(1), km.group(2)
            if key in sections[cur]:
                dups.append((cur, key, i))
            sections[cur][key] = val
            linenos[(cur, key)] = i
            last_key = (cur, key)
        elif last_key:
            sections[last_key[0]][last_key[1]] += "\n" + s
    return {"sections": sections, "duplicates": dups, "lines": linenos}


def unquote(v: str) -> str:
    v = v.strip()
    if len(v) >= 2 and v[0] == '"' and v[-1] == '"':
        return v[1:-1]
    return v


def get(parsed: dict, section: str, key: str, default=None):
    v = parsed["sections"].get(section, {}).get(key)
    return unquote(v) if v is not None else default


def input_actions(parsed: dict) -> set[str]:
    return set(parsed["sections"].get("input", {}).keys())


def autoloads(parsed: dict) -> dict[str, str]:
    out = {}
    for k, v in parsed["sections"].get("autoload", {}).items():
        out[k] = unquote(v).lstrip("*")
    return out
