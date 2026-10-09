"""fix(): 오류 분석 -> 원인 추론 -> 실제 수정 -> 재검사 루프. (규칙 기반 + 진단 컨텍스트)

고칠 수 없는 오류는 원인/제안/소스 컨텍스트가 담긴 구조화된 진단으로 반환하여 AI Agent 가 이어서 수정할 수 있게 한다.
"""
from __future__ import annotations
import difflib
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

from .. import api_check, context, security
from ..errors import McpError
from ..project import projectfile, tscn
from ..project.manifest import Manifest
from ..runtime import editor as editor_rt
from ..runtime import game as game_rt
from ..validation import logparse, static, syntax
from . import gdsrc as G

INPUT_DEFAULTS = {
    "jump": {"type": "key", "key": "Space"}, "dash": {"type": "key", "key": "Shift"}, "pause": {"type": "key", "key": "Escape"},
    "interact": {"type": "key", "key": "E"}, "attack": {"type": "key", "key": "J"}, "shoot": {"type": "key", "key": "J"}, "fire": {"type": "key", "key": "J"},
    "move_left": {"type": "key", "key": "A"}, "move_right": {"type": "key", "key": "D"}, "move_up": {"type": "key", "key": "W"}, "move_down": {"type": "key", "key": "S"},
    "left": {"type": "key", "key": "A"}, "right": {"type": "key", "key": "D"}, "up": {"type": "key", "key": "W"}, "down": {"type": "key", "key": "S"},
    "reload": {"type": "key", "key": "R"}, "sprint": {"type": "key", "key": "Shift"}, "crouch": {"type": "key", "key": "C"}, "use": {"type": "key", "key": "E"},
    "inventory": {"type": "key", "key": "I"}, "restart": {"type": "key", "key": "R"}, "menu": {"type": "key", "key": "Tab"},
}
VIRTUAL_PARAMS = {"_process": ("delta", "float"), "_physics_process": ("delta", "float"), "_input": ("event", "InputEvent"),
                  "_unhandled_input": ("event", "InputEvent"), "_unhandled_key_input": ("event", "InputEvent"), "_gui_input": ("event", "InputEvent"),
                  "_ready": None, "_enter_tree": None, "_exit_tree": None, "_draw": None}


@dataclass
class Change:
    file: str
    rule: str
    reason: str
    line: int | None = None
    before: str | None = None
    after: str | None = None

    def to_dict(self) -> dict:
        d = {"file": self.file, "rule": self.rule, "reason": self.reason}
        for k in ("line", "before", "after"):
            v = getattr(self, k)
            if v is not None:
                d[k] = v
        return d


@dataclass
class Fixer:
    root: Path
    apply: bool = True
    changes: list[Change] = field(default_factory=list)
    _cls_cache: dict = field(default_factory=dict)
    _settings_changed: bool = False

    # ---- 파일 편집 ---------------------------------------------------
    def _path(self, res: str) -> Path:
        return security.resolve(self.root, res, write=True)

    def _read(self, res: str) -> list[str]:
        return self._path(res).read_text(errors="replace").split("\n")

    def _write(self, res: str, lines: list[str]) -> None:
        if self.apply:
            self._path(res).write_text("\n".join(lines))

    def _record(self, file: str, rule: str, reason: str, line: int | None, before: str | None, after: str | None) -> Change:
        c = Change(file, rule, reason, line, before.strip() if before is not None else None, after.strip() if after is not None else None)
        self.changes.append(c)
        return c

    # ---- 클래스 정보 ---------------------------------------------------
    async def native_members(self, cls: str) -> set[str]:
        if cls in self._cls_cache:
            return self._cls_cache[cls]
        names: set[str] = set()
        try:
            c = cls
            while c:
                r = await editor_rt.editor_call(self.root, "class_info", {"class": c})
                if not r.get("exists"):
                    break
                names |= set(r["properties"]) | set(r["methods"]) | set(r["signals"])
                c = r.get("parent") or ""
        except McpError:
            pass
        self._cls_cache[cls] = names
        return names

    def script_for_class(self, name: str) -> str | None:
        cache = self.root / ".godot" / "global_script_class_cache.cfg"
        if cache.exists():
            m = re.search(rf'"class": &"{re.escape(name)}".*?"path": "(res://[^"]+)"', cache.read_text(), re.S)
            if m:
                return m.group(1)
        for s in syntax.list_scripts(self.root):
            if re.search(rf"^\s*class_name\s+{re.escape(name)}\b", s.read_text(errors="replace"), re.M):
                return security.to_res(self.root, s)
        return None

    async def members_for_script(self, res: str, depth: int = 0) -> set[str]:
        p = self.root / res[6:]
        if not p.exists() or depth > 6:
            return set()
        src = p.read_text(errors="replace")
        d = G.declared_names(src)
        names = set().union(*d.values())
        base = G.extends_of(src)
        if base:
            if base.startswith("res://"):
                names |= await self.members_for_script(base, depth + 1)
            else:
                sup = self.script_for_class(base)
                names |= await self.members_for_script(sup, depth + 1) if sup else await self.native_members(base)
        return names

    async def members_for_type(self, type_str: str) -> set[str]:
        """'CharacterBody2D (player.gd)' 또는 'Node2D' 형태."""
        m = re.match(r"(\w+)(?: \(([^)]+)\))?", type_str.strip())
        names: set[str] = set()
        if not m:
            return names
        if m.group(2):
            for s in syntax.list_scripts(self.root):
                if s.name == m.group(2):
                    names |= await self.members_for_script(security.to_res(self.root, s))
        names |= await self.native_members(m.group(1))
        return names

    # ---- 문법 오류 규칙 ----------------------------------------------------
    async def fix_syntax(self, err: dict) -> bool:
        file, line_no, msg = err.get("file"), err.get("line"), err.get("message", "")
        if not file or not line_no or not file.startswith("res://"):
            return False
        lines = self._read(file)
        i = line_no - 1
        if i < 0 or i >= len(lines):
            return False
        line = lines[i]
        code, comment = G.code_part(line)
        masked = G.mask_strings(code)

        m = re.match(r'Expected ":" after (?:"(\w+)"(?: condition)?|function declaration|class declaration|match)', msg) or msg.startswith('Expected ":"')
        if m:
            if re.match(r"^\s*else\s+if\b", code):
                new = re.sub(r"^(\s*)else\s+if\b", r"\1elif", code).rstrip()
                if not new.endswith(":"):
                    new += ":"
                lines[i] = new + (" " + comment if comment else "")
                return self._commit(file, lines, "else_if_to_elif", "GDScript 는 'else if' 대신 'elif' 를 사용합니다", i, line)
            if code.rstrip().endswith(":"):
                return False
            lines[i] = code.rstrip() + ":" + (" " + comment if comment else "")
            return self._commit(file, lines, "missing_colon", "블록 문 끝에 ':' 가 없어 추가했습니다", i, line)

        cm = re.match(r'Expected closing "([)\]}])"', msg)
        if cm:
            stack = []
            for ch in masked:
                if ch in "([{":
                    stack.append(ch)
                elif ch in ")]}" and stack:
                    stack.pop()
            if not stack:
                return False
            closers = "".join({"(": ")", "[": "]", "{": "}"}[c] for c in reversed(stack))
            lines[i] = code.rstrip() + closers + (" " + comment if comment else "")
            return self._commit(file, lines, "unclosed_bracket", f"닫히지 않은 괄호 {closers!r} 를 추가했습니다", i, line)

        if msg.startswith("Unterminated string"):
            q = G.unterminated_quote(code)
            if not q:
                return False
            lines[i] = code.rstrip() + q + (" " + comment if comment else "")
            return self._commit(file, lines, "unterminated_string", "닫히지 않은 문자열 따옴표를 추가했습니다 (뒤따르는 괄호는 재검사에서 처리)", i, line)

        if msg.startswith('Expected indented block after'):
            lines.insert(i + 1, G.indent_of(line) + "\tpass")
            return self._commit(file, lines, "empty_block", "비어 있는 블록에 'pass' 를 추가했습니다", i, line, after=lines[i + 1])

        if "Unindent doesn't match" in msg or msg.startswith("Used space character for indentation") or msg.startswith("Used tab character for indentation") \
                or "Mixed use of tabs and spaces" in msg or msg.startswith("Expected statement") and re.match(r"^\s+", line):
            new_lines = normalize_indentation(lines)
            if new_lines != lines:
                changed = [k for k in range(len(lines)) if lines[k] != new_lines[k]]
                self._write(file, new_lines)
                self._record(file, "indentation", f"들여쓰기를 탭 기준으로 정규화했습니다 ({len(changed)}줄)", (changed[0] + 1) if changed else line_no, lines[changed[0]] if changed else None,
                             new_lines[changed[0]] if changed else None)
                return True
            return False

        if msg.startswith("Assignment is not allowed inside an expression"):
            mm = re.match(r"^(\s*(?:if|elif|while)\s+)(.*?)(\s*:\s*)$", code)
            if mm and re.search(r"(?<![=!<>:+\-*/%&|^])=(?!=)", G.mask_strings(mm.group(2))):
                cond = re.sub(r"(?<![=!<>:+\-*/%&|^])=(?!=)", "==", mm.group(2), count=1)
                lines[i] = mm.group(1) + cond + mm.group(3) + comment
                return self._commit(file, lines, "assign_in_condition", "조건식의 '=' 를 비교 연산자 '==' 로 바꿨습니다", i, line)
            return False

        if msg.startswith("Expected expression for variable initial value"):
            if code.rstrip().endswith("="):
                lines[i] = code.rstrip() + " null" + (" " + comment if comment else "")
                return self._commit(file, lines, "empty_initializer", "비어 있는 초기값을 'null' 로 채웠습니다 (값을 직접 지정하세요)", i, line)
            return False

        if msg.startswith('Expected variable name after "var"'):
            mm = re.search(r"\bvar\s+(\d\w*)", code)
            if mm:
                old = mm.group(1)
                new = "_" + old
                for k, l in enumerate(lines):
                    lines[k] = G.replace_word(l, old, new) if re.search(rf"(?<!\w){re.escape(old)}(?!\w)", l) else l
                return self._commit(file, lines, "invalid_identifier", f"식별자 '{old}' 는 숫자로 시작할 수 없어 '{new}' 로 바꿨습니다", i, line)
            return False

        mm = re.match(r'Variable "(\w+)" has the same name as a previously declared variable', msg)
        if mm and re.match(r"^\s*var\s+\w+", code):
            lines[i] = G.indent_of(line) + "# [godot-mcp] 중복 선언 제거: " + line.strip()
            return self._commit(file, lines, "duplicate_variable", f"중복된 변수 선언 '{mm.group(1)}' 을 주석 처리했습니다", i, line)

        mm = re.match(r'Identifier "(\w+)" not declared in the current scope', msg)
        if mm:
            return await self._fix_identifier(file, lines, i, mm.group(1))

        mm = re.match(r'Function "(\w+)\(\)" not found in base', msg)
        if mm:
            return await self._fix_function(file, lines, i, mm.group(1))

        mm = re.match(r'The function signature doesn\'t match the parent\. Parent signature is "(\w+)\(([^)]*)\)', msg)
        if mm:
            return self._fix_virtual_signature(file, lines, mm.group(1), mm.group(2), line_no)
        return False

    def _commit(self, file: str, lines: list[str], rule: str, reason: str, i: int, before: str, after: str | None = None) -> bool:
        self._write(file, lines)
        self._record(file, rule, reason, i + 1, before, after if after is not None else lines[i])
        return True

    async def _fix_identifier(self, file: str, lines: list[str], i: int, name: str) -> bool:
        src = "\n".join(lines)
        decl = G.declared_names(src)
        local_all = set().union(*decl.values())
        cands = set(local_all)
        cands |= await self.members_for_script(file)
        cands |= set(projectfile.autoloads(projectfile.parse(self.root / "project.godot")).keys())
        cache = self.root / ".godot" / "global_script_class_cache.cfg"
        if cache.exists():
            cands |= set(re.findall(r'"class": &"(\w+)"', cache.read_text()))
        cands.discard(name)
        close = difflib.get_close_matches(name, sorted(cands), n=1, cutoff=0.8)
        line = lines[i]
        if close and re.search(rf"(?<![\w.]){re.escape(name)}(?!\w)", G.mask_strings(G.code_part(line)[0])):
            lines[i] = G.replace_word(line, name, close[0])
            return self._commit(file, lines, "identifier_typo", f"선언되지 않은 '{name}' 를 가장 가까운 선언 '{close[0]}' 로 수정했습니다", i, line)
        code, comment = G.code_part(line)
        cm = re.match(rf"^(\s*){re.escape(name)}\s*([+\-*/]?)=\s*(.+?)\s*$", code)
        if cm:
            rhs = cm.group(3)
            literal = re.fullmatch(r"-?\d+(\.\d+)?|true|false|\"[^\"]*\"|'[^']*'", rhs)
            if cm.group(2):
                init = "0.0" if "." in rhs else "0"
                return self._insert_member(file, lines, f"var {name} = {init}", name, i, line)
            if literal:
                lines[i] = f"{cm.group(1)}var {name} = {rhs}" + (" " + comment if comment else "")
                return self._commit(file, lines, "implicit_declaration", f"선언 없이 대입된 '{name}' 에 'var' 를 추가했습니다", i, line)
        return False

    def _insert_member(self, file: str, lines: list[str], decl: str, name: str, i: int, before: str) -> bool:
        pos = 0
        for k, l in enumerate(lines):
            if re.match(r"^(@tool|@icon|@static_unnamed|class_name|extends|signal|##|#)\b|^\s*$", l) or re.match(r"^(@tool|extends|class_name|signal)", l):
                pos = k + 1
            else:
                break
        lines.insert(pos, decl)
        self._write(file, lines)
        self._record(file, "implicit_declaration", f"선언되지 않은 멤버 '{name}' 를 클래스 최상단에 추가했습니다", i + 1, before, decl)
        return True

    async def _fix_function(self, file: str, lines: list[str], i: int, name: str) -> bool:
        cands = await self.members_for_script(file)
        cands = {c for c in cands if not c.startswith("_")} | {c for c in cands if c.startswith("_")}
        cands.discard(name)
        close = difflib.get_close_matches(name, sorted(cands), n=1, cutoff=0.8)
        if not close:
            return False
        line = lines[i]
        new = G.replace_member(line, name, close[0])
        if new == line:
            return False
        lines[i] = new
        return self._commit(file, lines, "function_typo", f"존재하지 않는 함수 '{name}()' 를 '{close[0]}()' 로 수정했습니다", i, line)

    def _fix_virtual_signature(self, file: str, lines: list[str], fn: str, parent_args: str, line_no: int) -> bool:
        spec = VIRTUAL_PARAMS.get(fn, "missing")
        if spec == "missing":
            return False
        for k, l in enumerate(lines):
            m = re.match(rf"^(\s*func\s+{fn}\s*)\(([^)]*)\)(\s*->\s*\w+)?\s*:(.*)$", l)
            if m:
                params = "" if spec is None else f"{spec[0]}: {spec[1]}"
                existing = [p.strip() for p in m.group(2).split(",") if p.strip()]
                if spec is not None and len(existing) == 1:
                    pname = re.match(r"(\w+)", existing[0]).group(1)
                    params = f"{pname}: {spec[1]}"
                lines[k] = f"{m.group(1)}({params}) -> void:{m.group(4)}"
                return self._commit(file, lines, "virtual_signature", f"가상 함수 '{fn}' 의 시그니처를 부모 시그니처에 맞췄습니다", k, l)
        return False

    # ---- 런타임 오류 규칙 ----------------------------------------------------
    async def fix_runtime(self, err: dict) -> bool:
        msg, file, line_no = err.get("message", ""), err.get("file"), err.get("line")
        if file and line_no and file.startswith("res://") and file.endswith(".gd"):
            mm = (re.match(r"Invalid access to property or key '(\w+)' on a base object of type '([^']+)'", msg)
                  or re.match(r"Invalid (?:get|set) index '(\w+)' \(on base: '([^']+)'\)", msg)
                  or re.match(r"Invalid assignment of property or key '(\w+)' with value of type '[^']*' on a base object of type '([^']+)'", msg))
            if mm:
                return await self._fix_member_typo(file, line_no, mm.group(1), mm.group(2), "property")
            mm = re.match(r"Invalid call\. Nonexistent function '(\w+)' in base '([^']+)'", msg)
            if mm:
                return await self._fix_member_typo(file, line_no, mm.group(1), mm.group(2), "function")
        mm = re.match(r'Node not found: "([^"]+)" \(relative to "([^"]+)"\)', msg)
        if mm:
            return await self.fix_missing_node(mm.group(1), mm.group(2), file, line_no)
        return False

    async def _fix_member_typo(self, file: str, line_no: int, name: str, type_str: str, kind: str) -> bool:
        lines = self._read(file)
        cands = await self.members_for_type(type_str) | await self.members_for_script(file)
        cands.discard(name)
        close = difflib.get_close_matches(name, sorted(cands), n=1, cutoff=0.8)
        if not close:
            return False
        for i in range(max(0, line_no - 2), min(len(lines), line_no + 1)):
            new = G.replace_member(lines[i], name, close[0])
            if new != lines[i]:
                before = lines[i]
                lines[i] = new
                return self._commit(file, lines, f"{kind}_typo", f"존재하지 않는 {kind} '{name}' 를 '{close[0]}' 로 수정했습니다", i, before)
        return False

    async def fix_missing_node(self, node: str, base: str, file: str | None, line_no: int | None) -> bool:
        """누락된 노드: (1) 같은 이름의 씬 파일이 있으면 인스턴스 추가 (2) 유사한 노드로 스크립트 참조 수정."""
        scene_res = None
        owner_name = base.rstrip("/").rsplit("/", 1)[-1]
        for p in static.list_files(self.root, (".tscn",)):
            sc = tscn.parse(p)
            if sc.nodes and sc.nodes[0].name == owner_name:
                scene_res = security.to_res(self.root, p)
                break
        if scene_res is None:
            return False
        leaf = node.rsplit("/", 1)[-1]
        snake = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", leaf).lower()
        for p in static.list_files(self.root, (".tscn",)):
            if p.stem == snake:
                res = security.to_res(self.root, p)
                parent = node.rsplit("/", 1)[0] if "/" in node else "."
                if self.apply:
                    await editor_rt.editor_call(self.root, "patch_scene", {"path": scene_res, "ops": [{"op": "add", "parent": parent, "node": {"name": leaf, "instance": res}}]})
                self._record(scene_res, "missing_node_instance", f"누락된 노드 '{node}' 를 씬 파일 {res} 의 인스턴스로 추가했습니다", None, None, f"{leaf} -> {res}")
                return True
        if file and file.endswith(".gd"):
            sc = tscn.parse(self.root / scene_res[6:])
            names = {n.path for n in sc.nodes} | {n.name for n in sc.nodes}
            close = difflib.get_close_matches(node, sorted(names), n=1, cutoff=0.85)
            if close and close[0] != node and line_no:
                lines = self._read(file)
                for i in range(max(0, line_no - 2), min(len(lines), line_no + 1)):
                    if node in lines[i]:
                        before = lines[i]
                        lines[i] = lines[i].replace(node, close[0])
                        return self._commit(file, lines, "node_path_typo", f"존재하지 않는 노드 경로 '{node}' 를 '{close[0]}' 로 수정했습니다", i, before)
        return False

    # ---- validate 오류 규칙 --------------------------------------------------
    async def fix_validate(self, err: dict) -> bool:
        fix = err.get("fix")
        if fix == "main_scene":
            scenes = static.list_files(self.root, (".tscn",))
            pick = next((s for s in scenes if s.stem == "main"), scenes[0] if len(scenes) == 1 else None)
            if not pick:
                return False
            res = security.to_res(self.root, pick)
            if self.apply:
                await editor_rt.editor_call(self.root, "set_settings", {"settings": {"application/run/main_scene": res}})
            self._record("res://project.godot", "main_scene", f"존재하지 않는 메인 씬 대신 {res} 를 지정했습니다", None, str(err.get("path")), res)
            return True
        if fix == "input_action":
            name = err.get("action", "")
            ev = INPUT_DEFAULTS.get(name)
            if not ev:
                return False
            if self.apply:
                await editor_rt.editor_call(self.root, "add_input_actions", {"actions": {name: {"events": [ev]}}})
            self._record("res://project.godot", "input_action", f"누락된 입력 액션 '{name}' 를 {ev['key']} 키로 추가했습니다", None, None, f"{name}={ev['key']}")
            return True
        if fix == "autoload":
            moved = self._find_moved(err.get("path", ""))
            if not moved:
                return False
            if self.apply:
                await editor_rt.editor_call(self.root, "set_autoloads", {"autoloads": {err["autoload"]: moved}})
            self._record("res://project.godot", "autoload_path", f"autoload '{err['autoload']}' 경로를 {moved} 로 갱신했습니다", None, err.get("path"), moved)
            return True
        if fix == "ext_resource":
            moved = self._find_moved(err.get("path", ""))
            file = err.get("file")
            if not moved or not file:
                return False
            lines = self._read(file)
            old = err["path"]
            changed = False
            for k, l in enumerate(lines):
                if old in l:
                    nl = l.replace(old, moved)
                    if nl.lstrip().startswith("[ext_resource"):
                        nl = re.sub(r'\s+uid="[^"]*"', "", nl)  # 옛 uid 는 더 이상 유효하지 않음
                    lines[k] = nl
                    changed = True
            if changed:
                self._write(file, lines)
                self._record(file, "resource_path", f"깨진 리소스 경로 {old} 를 {moved} 로 수정했습니다", None, old, moved)
            return changed
        if fix == "node_ref":
            ref, scene_file = err.get("node", ""), err.get("scene", "")
            base = "/root/" + (tscn.parse(self.root / scene_file[6:]).nodes[0].name if scene_file else "")
            return await self.fix_missing_node(ref, base, err.get("file"), err.get("line"))
        return False

    def _find_moved(self, old: str) -> str | None:
        name = Path(old).name
        if not name:
            return None
        hits = [p for p in self.root.rglob(name) if p.is_file() and not p.relative_to(self.root).as_posix().startswith((".godot", "addons/godot_mcp"))]
        return security.to_res(self.root, hits[0]) if len(hits) == 1 else None


def normalize_indentation(lines: list[str]) -> list[str]:
    """탭/스페이스 혼용 정규화: 스페이스 단위를 추정해 탭으로 변환하고, 어긋난 들여쓰기는 가장 가까운 유효 단계로 맞춘다."""
    space_lines = [len(l) - len(l.lstrip(" ")) for l in lines if l.startswith(" ") and l.strip() and not l.lstrip(" ").startswith("\t")]
    unit = 4
    if space_lines:
        from math import gcd
        g = 0
        for n in space_lines:
            g = gcd(g, n)
        unit = g if g in (2, 4, 8) else 4
    depths: list[int] = []
    for l in lines:
        if not l.strip():
            depths.append(-1)
            continue
        ws = l[: len(l) - len(l.lstrip(" \t"))]
        depths.append(ws.count("\t") + (len(ws.replace("\t", "")) + unit - 1) // unit)
    stack = [0]
    out: list[str] = []
    for l, d in zip(lines, depths):
        if d < 0:
            out.append(l.strip() if not l.strip() else l)
            continue
        if d > stack[-1]:
            stack.append(d)
        elif d < stack[-1]:
            # 정확히 일치하는 단계가 없으면 가장 가까운 단계(동률이면 더 깊은 쪽 = 현재 블록 유지)로 맞춘다
            d = min(stack, key=lambda lvl: (abs(lvl - d), -lvl))
            while stack[-1] > d:
                stack.pop()
        out.append("\t" * d + l.lstrip(" \t"))
    return out


# --------------------------------------------------------------------------- #

DIAGNOSES = [
    (r'Cannot infer the type of "(\w+)"', "타입 추론 실패: ':=' 대신 명시적 타입 또는 '=' 를 사용하거나 초기값의 타입을 확정하세요."),
    (r"Cannot (?:assign|return)", "타입 불일치: 값의 타입과 선언된 타입이 다릅니다. 값 또는 타입 어노테이션을 수정하세요."),
    (r"Too (?:few|many) arguments", "호출 인자 개수가 함수 선언과 다릅니다. 선언된 시그니처에 맞게 호출을 수정하세요."),
    (r'Identifier "(\w+)" not declared', "선언되지 않은 식별자: 유사한 이름이 없습니다. 변수/함수를 선언하거나 올바른 이름을 사용하세요."),
    (r'Function "(\w+)\(\)" not found', "존재하지 않는 함수 호출: 유사한 함수가 없습니다. 함수를 정의하거나 올바른 이름을 사용하세요."),
    (r"Node not found", "씬 트리에 없는 노드를 스크립트가 참조합니다: 씬에 노드를 추가(modify_game attach/set 또는 create_scene)하거나 스크립트 경로를 수정하세요."),
    (r"Cannot call method '(\w+)' on a null value", "null 객체에서 메서드를 호출했습니다: 해당 변수가 초기화되는지(노드 경로/@onready/그룹) 확인하세요."),
    (r"Invalid access|Invalid call|Invalid (?:get|set) index", "존재하지 않는 속성/메서드 접근: 객체 타입과 이름을 확인하세요."),
    (r"signal|연결된 메서드", "시그널에 연결된 메서드가 대상 스크립트에 없습니다. 메서드를 정의하세요."),
    (r"없습니다|not found|missing", "참조된 파일/리소스가 없습니다. 경로를 수정하거나 리소스를 생성하세요."),
]


def diagnose(err: dict, root: Path) -> dict:
    msg = err.get("message", "")
    why = next((d for pat, d in DIAGNOSES if re.search(pat, msg, re.I)), "자동 수정 규칙이 없는 오류입니다. 소스 컨텍스트를 보고 직접 수정하세요 (modify_game).")
    out = {k: err.get(k) for k in ("type", "severity", "file", "line", "column", "message", "node", "scene") if err.get(k) is not None}
    out["diagnosis"] = why
    ctx = err.get("context") or logparse.source_context(root, err.get("file"), err.get("line"))
    if ctx:
        out["context"] = ctx
    if err.get("type") == "input_action" or err.get("fix") == "input_action":
        out["suggestion"] = f"modify_game(settings=...) 가 아닌 add_mechanic 또는 직접 InputMap 구성 필요: 액션 '{err.get('action')}' 의 키를 정하세요"
    return out


async def fix(project: str | None = None, *, scope: str = "errors", max_iterations: int = 6, dry_run: bool = False, run_if_needed: bool = True) -> dict:
    root = context.resolve_project(project)
    if scope not in ("errors", "all", "syntax", "runtime", "validate"):
        raise McpError("INVALID_ARGUMENT", "scope 는 errors|all|syntax|runtime|validate 중 하나여야 합니다")
    t0 = time.monotonic()
    fx = Fixer(root, apply=not dry_run)
    do_syntax = scope in ("errors", "all", "syntax")
    do_validate = scope in ("errors", "all", "validate")
    do_runtime = scope in ("errors", "all", "runtime")
    iterations = 0
    tried: set[tuple] = set()
    for iterations in range(1, (1 if dry_run else max_iterations) + 1):
        progress = 0
        if do_syntax:
            await api_check.refresh_class_cache(root)
            res = await syntax.syntax_check(root)
            seen_files: set[str] = set()
            for e in sorted(res["errors"], key=lambda x: (x.get("file") or "", x.get("line") or 0)):
                f = e.get("file")
                key = (f, e.get("line"), e["message"])
                if f in seen_files or key in tried:
                    continue
                tried.add(key)
                seen_files.add(f)
                if await fx.fix_syntax(e):
                    progress += 1
        if do_validate and progress == 0:
            val = await api_check.validate(str(root), engine=False)
            for e in val["errors"]:
                key = ("validate", e.get("file"), e.get("line"), e["message"])
                if key in tried:
                    continue
                tried.add(key)
                if await fx.fix_validate(e):
                    progress += 1
        if do_runtime and progress == 0:
            log = game_rt.read_log(root)
            if not log and run_if_needed and not dry_run:
                from .. import api_runtime
                await api_runtime.run(str(root), timeout=6, observe=3)
                log = game_rt.read_log(root)
            for e in logparse.link_script_context(logparse.parse_log(log), root):
                if e["severity"] != "error":
                    continue
                key = ("runtime", e.get("file"), e.get("line"), e["message"])
                if key in tried:
                    continue
                tried.add(key)
                if await fx.fix_runtime(e):
                    progress += 1
        if progress and not dry_run:
            try:
                await editor_rt.editor_call(root, "scan", timeout=60)
            except McpError:
                pass
        if progress == 0 or dry_run:
            break
    # 최종 재검사
    remaining: list[dict] = []
    if not dry_run or True:
        if do_syntax:
            await api_check.refresh_class_cache(root)
            s = await syntax.syntax_check(root)
            context.save_check(root, "syntax", s) if not dry_run else None
            remaining += s["errors"]
        if do_validate:
            v = await api_check.validate(str(root), engine=not dry_run)
            remaining += v["errors"]
        if do_runtime and not dry_run:
            if fx.changes and any(c.rule in ("property_typo", "function_typo", "node_path_typo", "missing_node_instance") for c in fx.changes):
                from .. import api_runtime
                await api_runtime.run(str(root), timeout=6, observe=3)
            remaining += [e for e in logparse.parse_log(game_rt.read_log(root)) if e["severity"] == "error"]
    seen = set()
    uniq = []
    for e in remaining:
        k = (e["type"], e.get("file"), e.get("line"), e["message"])
        if k not in seen:
            seen.add(k)
            uniq.append(e)
    unfixable = [diagnose(e, root) for e in uniq]
    return {"success": not unfixable, "dry_run": dry_run, "fixed": len(fx.changes), "changes": [c.to_dict() for c in fx.changes],
            "remaining_errors": len(unfixable), "unfixable": unfixable[:15], "iterations": iterations, "scope": scope,
            "duration": round(time.monotonic() - t0, 2),
            **({"next": "unfixable 의 diagnosis/context 를 보고 modify_game(files=/edits=) 으로 직접 수정한 뒤 syntax_check() 를 실행하세요"} if unfixable else {})}
