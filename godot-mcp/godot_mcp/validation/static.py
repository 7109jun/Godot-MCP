"""validate(): 프로젝트 전체 구조 정적 검증 (스크립트/씬/리소스/참조/오토로드/입력/설정)."""
from __future__ import annotations
import re
import time
from collections import defaultdict
from pathlib import Path

from ..project import projectfile, tscn
from . import syntax

BUILTIN_ACTIONS_PREFIX = "ui_"
_INPUT_FUNCS = r"(?:is_action_pressed|is_action_just_pressed|is_action_just_released|is_action_released|get_action_strength|get_axis|get_vector|action_press|action_release)"
_STR = r"\"([^\"\n]+)\"|'([^'\n]+)'"
_RES_LITERAL = re.compile(r"\b(?:preload|load)\(\s*[\"'](res://[^\"'\n]+)[\"']\s*\)")
_NODE_DOLLAR = re.compile(r"(?<![\w$])\$(?:\"([^\"\n]+)\"|'([^'\n]+)'|([A-Za-z_][\w/]*))")
_GET_NODE = re.compile(r"\bget_node\(\s*(?:\"([^\"\n]+)\"|'([^'\n]+)')\s*\)")
_CLASS_NAME = re.compile(r"^\s*class_name\s+(\w+)", re.M)
SCENE_EXT = (".tscn",)
RES_EXT = (".tres",)


def _strip_comment(line: str) -> str:
    out, q = [], None
    for i, ch in enumerate(line):
        if q:
            out.append(ch)
            if ch == q and line[i - 1] != "\\":
                q = None
        elif ch in "\"'":
            q = ch
            out.append(ch)
        elif ch == "#":
            break
        else:
            out.append(ch)
    return "".join(out)


def _issue(severity: str, type_: str, file: str | None, line: int | None, message: str, **extra) -> dict:
    d = {"type": type_, "severity": severity, "message": message}
    if file:
        d["file"] = file
    if line:
        d["line"] = line
    d.update(extra)
    return d


def list_files(project: Path, suffixes: tuple) -> list[Path]:
    out = []
    for p in sorted(project.rglob("*")):
        if not p.is_file() or p.suffix not in suffixes:
            continue
        rel = p.relative_to(project).as_posix()
        if rel.startswith((".godot/", ".godot_mcp/", "build/", "addons/godot_mcp/")):
            continue
        out.append(p)
    return out


def res(project: Path, p: Path) -> str:
    return "res://" + p.relative_to(project).as_posix()


def exists(project: Path, res_path: str) -> bool:
    return res_path.startswith("res://") and (project / res_path[6:]).exists()


def validate_static(project: Path) -> dict:
    t0 = time.monotonic()
    issues: list[dict] = []
    pf = projectfile.parse(project / "project.godot")
    scripts = syntax.list_scripts(project)
    scenes = list_files(project, SCENE_EXT)
    resources = list_files(project, RES_EXT)

    # --- project settings ---------------------------------------------------
    main = projectfile.get(pf, "application", "run/main_scene")
    if not main:
        issues.append(_issue("error", "project_settings", "res://project.godot", None, "application/run/main_scene 이 설정되지 않았습니다", fix="main_scene"))
    elif not exists(project, main):
        issues.append(_issue("error", "project_settings", "res://project.godot", pf["lines"].get(("application", "run/main_scene")),
                             f"main scene 파일이 없습니다: {main}", fix="main_scene", path=main))
    for section, key, ln in pf["duplicates"]:
        issues.append(_issue("warning", "project_settings", "res://project.godot", ln, f"중복 설정 키 [{section}] {key}"))
    for dim in ("viewport_width", "viewport_height"):
        v = projectfile.get(pf, "display", f"window/size/{dim}")
        if v is not None and (not re.fullmatch(r"\d+", v) or int(v) <= 0):
            issues.append(_issue("error", "project_settings", "res://project.godot", None, f"display/window/size/{dim} 값이 올바르지 않습니다: {v}"))
    ep = pf["sections"].get("editor_plugins", {}).get("enabled", "")
    for cfg in re.findall(r'"(res://[^"]+plugin\.cfg)"', ep):
        if not exists(project, cfg):
            issues.append(_issue("error", "project_settings", "res://project.godot", None, f"활성화된 플러그인이 없습니다: {cfg}", fix="editor_plugin", path=cfg))

    # --- autoloads ------------------------------------------------------------
    autoloads = projectfile.autoloads(pf)
    class_names: dict[str, list[str]] = defaultdict(list)
    for s in scripts:
        for m in _CLASS_NAME.finditer(s.read_text(errors="replace")):
            class_names[m.group(1)].append(res(project, s))
    for name, path in autoloads.items():
        if not exists(project, path):
            issues.append(_issue("error", "autoload", "res://project.godot", pf["lines"].get(("autoload", name)),
                                 f"autoload '{name}' 경로가 없습니다: {path}", fix="autoload", autoload=name, path=path))
        if name in class_names:
            issues.append(_issue("error", "autoload", class_names[name][0], None, f"class_name '{name}' 이(가) autoload 싱글톤과 이름이 충돌합니다"))
    for cname, files in class_names.items():
        if len(files) > 1:
            issues.append(_issue("error", "script_reference", files[1], None, f"class_name '{cname}' 중복 정의: {', '.join(files)}"))

    # --- scenes / resources: ext_resource, nodes, scripts ------------------------
    parsed_scenes: dict[Path, tscn.Scene] = {}
    uids: dict[str, str] = {}
    for p in scenes + resources:
        sc = tscn.parse(p)
        rp = res(project, p)
        if p in scenes:
            parsed_scenes[p] = sc
        if sc.uid:
            if sc.uid in uids and uids[sc.uid] != rp:
                issues.append(_issue("error", "scene_reference", rp, 1, f"uid 중복 ({sc.uid}) — {uids[sc.uid]} 와 충돌", fix="duplicate_uid"))
            uids.setdefault(sc.uid, rp)
        for er in sc.ext.values():
            if er.path.startswith("res://") and not exists(project, er.path):
                kind = "script_reference" if er.type == "Script" else "scene_reference" if er.type == "PackedScene" else "missing_asset"
                issues.append(_issue("error", kind, rp, er.line, f"{er.type} 리소스를 찾을 수 없습니다: {er.path}",
                                     fix="ext_resource", path=er.path, resource_type=er.type))
        if p in scenes:
            if not sc.nodes:
                issues.append(_issue("error", "scene_structure", rp, None, "씬에 노드가 없습니다"))
                continue
            roots = [n for n in sc.nodes if n.parent is None]
            if len(roots) != 1:
                issues.append(_issue("error", "scene_structure", rp, None, f"씬 루트가 {len(roots)}개입니다 (1개여야 함)"))
            known = sc.node_paths()
            for n in sc.nodes:
                if n.parent is not None and n.parent != "." and n.parent not in known:
                    issues.append(_issue("error", "node_reference", rp, n.line, f"노드 '{n.name}' 의 부모 경로가 없습니다: {n.parent}"))
            for c in sc.connections:
                fp = tscn.normalize_rel(".", c.get("from", "."))
                tp = tscn.normalize_rel(".", c.get("to", "."))
                if fp not in known or tp not in known:
                    issues.append(_issue("error", "node_reference", rp, c.get("line"), f"시그널 연결 대상 노드가 없습니다: {c.get('from')} -> {c.get('to')}"))
                    continue
                tn = sc.by_path(tp)
                if tn and tn.script_path and exists(project, tn.script_path):
                    src = (project / tn.script_path[6:]).read_text(errors="replace")
                    if not re.search(rf"^\s*func\s+{re.escape(c.get('method', ''))}\b", src, re.M):
                        issues.append(_issue("error", "script_reference", rp, c.get("line"),
                                             f"연결된 메서드 '{c.get('method')}' 가 {tn.script_path} 에 없습니다"))

    # --- scripts: res literals, input actions, node refs ------------------------
    actions = projectfile.input_actions(pf)
    attach: dict[str, list[tuple[tscn.Scene, tscn.Node]]] = defaultdict(list)
    for sc in parsed_scenes.values():
        for n in sc.nodes:
            if n.script_path:
                attach[n.script_path].append((sc, n))
    for s in scripts:
        rp = res(project, s)
        lines = s.read_text(errors="replace").splitlines()
        seen_nodes: set[tuple] = set()
        for i, raw in enumerate(lines, 1):
            line = _strip_comment(raw)
            for m in _RES_LITERAL.finditer(line):
                if not exists(project, m.group(1)):
                    issues.append(_issue("error", "script_reference", rp, i, f"preload/load 대상이 없습니다: {m.group(1)}",
                                         fix="ext_resource", path=m.group(1), resource_type="Resource"))
            for m in re.finditer(rf"\b{_INPUT_FUNCS}\(\s*({_STR})(?:\s*,\s*({_STR}))?(?:\s*,\s*({_STR}))?(?:\s*,\s*({_STR}))?", line):
                names = re.findall(_STR, m.group(0))
                for a, b in names:
                    nm = a or b
                    if nm and not nm.startswith(BUILTIN_ACTIONS_PREFIX) and nm not in actions:
                        issues.append(_issue("error", "input_action", rp, i, f"InputMap 에 없는 액션 '{nm}'", fix="input_action", action=nm))
            refs = []
            for m in _NODE_DOLLAR.finditer(line):
                refs.append(m.group(1) or m.group(2) or m.group(3))
            for m in _GET_NODE.finditer(line):
                refs.append(m.group(1) or m.group(2))
            for ref in refs:
                if not ref or ref.startswith("/") or ref.startswith("%") or "%" in ref:
                    continue
                for sc, n in attach.get(rp, []):
                    full = tscn.normalize_rel(n.path, ref)
                    key = (sc.path, n.path, ref)
                    if key in seen_nodes:
                        continue
                    seen_nodes.add(key)
                    if full is None:
                        continue
                    known = sc.node_paths()
                    if full in known or any(full.startswith(pre + "/") for pre in sc.instance_prefixes()):
                        continue
                    issues.append(_issue("error", "node_reference", rp, i,
                                         f"노드 '{ref}' 가 씬 {res(project, sc.path)} 의 '{n.path}' 아래에 없습니다 (스크립트가 이 노드에 부착됨)",
                                         fix="node_ref", node=ref, scene=res(project, sc.path), owner_node=n.path))
    unattached = [res(project, s) for s in scripts if res(project, s) not in attach and not _is_autoload_or_class(project, s, autoloads, class_names)]
    errors = [i for i in issues if i["severity"] == "error"]
    warnings = [i for i in issues if i["severity"] == "warning"]
    for u in unattached[:20]:
        warnings.append(_issue("warning", "script_reference", u, None, "어떤 씬에도 부착되지 않은 스크립트입니다(오토로드/클래스 아님)"))
    categories: dict[str, int] = defaultdict(int)
    for i in errors:
        categories[i["type"]] += 1
    return {"success": not errors, "errors": errors, "warnings": warnings,
            "checked": {"scripts": len(scripts), "scenes": len(scenes), "resources": len(resources), "autoloads": len(autoloads), "input_actions": len(actions)},
            "error_categories": dict(categories), "duration": round(time.monotonic() - t0, 2)}


def _is_autoload_or_class(project: Path, s: Path, autoloads: dict, class_names: dict) -> bool:
    rp = res(project, s)
    if rp in autoloads.values():
        return True
    if any(rp in v for v in class_names.values()):
        return True
    return rp.startswith("res://scripts/mechanics/")  # 컴포넌트는 씬에 부착되지만 검사 소음 방지용
