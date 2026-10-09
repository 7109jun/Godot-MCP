"""간단한 .tscn/.tres 텍스트 파서 (정적 검증/수정용)."""
from __future__ import annotations
import re
from dataclasses import dataclass, field
from pathlib import Path

_HEADER = re.compile(r"^\[(\w+)\s*(.*)\]\s*$")
_ATTR = re.compile(r'(\w+)=("(?:[^"\\]|\\.)*"|\[[^\]]*\]|[^\s\]]+(?:\([^)]*\))?)')


def _unq(v: str) -> str:
    return v[1:-1] if len(v) >= 2 and v[0] == '"' and v[-1] == '"' else v


@dataclass
class ExtRes:
    id: str
    type: str
    path: str
    line: int
    uid: str = ""


@dataclass
class Node:
    name: str
    type: str
    parent: str | None
    line: int
    instance_id: str = ""
    script_id: str = ""
    script_path: str = ""
    instance_path: str = ""
    groups: list[str] = field(default_factory=list)

    @property
    def path(self) -> str:
        if self.parent is None:
            return "."
        return self.name if self.parent == "." else f"{self.parent}/{self.name}"


@dataclass
class Scene:
    path: Path
    uid: str = ""
    ext: dict[str, ExtRes] = field(default_factory=dict)
    nodes: list[Node] = field(default_factory=list)
    connections: list[dict] = field(default_factory=list)
    text: str = ""

    def node_paths(self) -> set[str]:
        return {n.path for n in self.nodes}

    def by_path(self, p: str) -> Node | None:
        for n in self.nodes:
            if n.path == p:
                return n
        return None

    def instance_prefixes(self) -> list[str]:
        return [n.path for n in self.nodes if n.instance_path and n.parent is not None]


def parse(path: Path) -> Scene:
    text = path.read_text(errors="replace")
    sc = Scene(path=path, text=text)
    cur_node: Node | None = None
    for i, line in enumerate(text.splitlines(), 1):
        m = _HEADER.match(line)
        if m:
            kind, rest = m.group(1), m.group(2)
            attrs = {k: _unq(v) for k, v in _ATTR.findall(rest)}
            cur_node = None
            if kind in ("gd_scene", "gd_resource"):
                sc.uid = attrs.get("uid", "")
            elif kind == "ext_resource":
                sc.ext[attrs.get("id", "")] = ExtRes(attrs.get("id", ""), attrs.get("type", ""), attrs.get("path", ""), i, attrs.get("uid", ""))
            elif kind == "node":
                n = Node(attrs.get("name", ""), attrs.get("type", ""), attrs.get("parent"), i)
                inst = re.search(r'instance=ExtResource\("([^"]+)"\)', rest)
                if inst:
                    n.instance_id = inst.group(1)
                    er = sc.ext.get(n.instance_id)
                    n.instance_path = er.path if er else ""
                g = re.search(r'groups=\[([^\]]*)\]', rest)
                if g:
                    n.groups = re.findall(r'"([^"]+)"', g.group(1))
                sc.nodes.append(n)
                cur_node = n
            elif kind == "connection":
                sc.connections.append({**attrs, "line": i})
            continue
        if cur_node is not None:
            sm = re.match(r'^script\s*=\s*ExtResource\("([^"]+)"\)', line)
            if sm:
                cur_node.script_id = sm.group(1)
                er = sc.ext.get(sm.group(1))
                cur_node.script_path = er.path if er else ""
    return sc


def normalize_rel(base: str, ref: str) -> str | None:
    """노드 경로 base 에서 상대 ref 를 정규화 ('.', '..' 처리). 루트 밖이면 None."""
    parts = [] if base in (".", "") else base.split("/")
    for seg in ref.split("/"):
        if seg in ("", "."):
            continue
        if seg == "..":
            if not parts:
                return None
            parts.pop()
        else:
            parts.append(seg)
    return "/".join(parts) if parts else "."
