"""GDScript 소스 텍스트 유틸 (문자열/주석을 무시한 스캔, 선언 추출)."""
from __future__ import annotations
import re

DECL_RE = re.compile(r"^\s*(?:@\w+(?:\([^)]*\))?\s+)*(?:static\s+)?(var|const|signal|func|enum|class)\s+(\w+)", re.M)
PARAM_RE = re.compile(r"^\s*(?:static\s+)?func\s+\w+\s*\(([^)]*)\)", re.M)
FOR_RE = re.compile(r"\bfor\s+(\w+)\s+in\b")
EXTENDS_RE = re.compile(r"^\s*extends\s+(\"[^\"]+\"|'[^']+'|[A-Za-z_]\w*)", re.M)
CLASS_NAME_RE = re.compile(r"^\s*class_name\s+(\w+)", re.M)


def code_part(line: str) -> tuple[str, str]:
    """(코드, 주석) 분리 — 문자열 안의 # 는 무시."""
    q = None
    i = 0
    while i < len(line):
        ch = line[i]
        if q:
            if ch == "\\":
                i += 2
                continue
            if ch == q:
                q = None
        elif ch in "\"'":
            q = ch
        elif ch == "#":
            return line[:i], line[i:]
        i += 1
    return line, ""


def mask_strings(code: str) -> str:
    """문자열 내용을 같은 길이의 공백으로 치환 (따옴표는 유지) — 괄호 카운트/치환 위치 계산용."""
    out, q, i = [], None, 0
    while i < len(code):
        ch = code[i]
        if q:
            if ch == "\\" and i + 1 < len(code):
                out.append("  ")
                i += 2
                continue
            if ch == q:
                q = None
                out.append(ch)
            else:
                out.append(" ")
        else:
            if ch in "\"'":
                q = ch
            out.append(ch)
        i += 1
    return "".join(out)


def unterminated_quote(code: str) -> str | None:
    q, i = None, 0
    while i < len(code):
        ch = code[i]
        if q:
            if ch == "\\":
                i += 2
                continue
            if ch == q:
                q = None
        elif ch in "\"'":
            q = ch
        i += 1
    return q


def declared_names(src: str) -> dict[str, set[str]]:
    kinds: dict[str, set[str]] = {"var": set(), "const": set(), "signal": set(), "func": set(), "enum": set(), "class": set(), "local": set()}
    for m in DECL_RE.finditer(src):
        kinds[m.group(1)].add(m.group(2))
    for m in PARAM_RE.finditer(src):
        for p in m.group(1).split(","):
            nm = re.match(r"\s*(\w+)", p)
            if nm:
                kinds["local"].add(nm.group(1))
    for m in FOR_RE.finditer(src):
        kinds["local"].add(m.group(1))
    for m in re.finditer(r"^\s*(?:var|const)\s+(\w+)", src, re.M):
        kinds["local"].add(m.group(1))
    return kinds


def extends_of(src: str) -> str | None:
    m = EXTENDS_RE.search(src)
    return m.group(1).strip("\"'") if m else None


def indent_of(line: str) -> str:
    return line[: len(line) - len(line.lstrip(" \t"))]


def replace_word(line: str, old: str, new: str, *, first_only: bool = False) -> str:
    """문자열/주석 밖에서만 단어 단위 치환."""
    code, comment = code_part(line)
    masked = mask_strings(code)
    out, last = [], 0
    for m in re.finditer(rf"(?<![\w.]){re.escape(old)}(?!\w)" if old[0].isalpha() or old[0] == "_" else re.escape(old), masked):
        out.append(code[last:m.start()])
        out.append(new)
        last = m.end()
        if first_only:
            break
    out.append(code[last:])
    return "".join(out) + comment


def replace_member(line: str, old: str, new: str) -> str:
    """'.old' 멤버 접근 또는 'old(' 호출 형태 치환."""
    code, comment = code_part(line)
    masked = mask_strings(code)
    res, last = [], 0
    for m in re.finditer(rf"(?<=\.){re.escape(old)}(?!\w)|(?<![\w.]){re.escape(old)}(?=\s*\()", masked):
        res.append(code[last:m.start()])
        res.append(new)
        last = m.end()
    res.append(code[last:])
    return "".join(res) + comment
