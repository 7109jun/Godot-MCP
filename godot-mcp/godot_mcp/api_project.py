"""ACTION(생성/수정): create_project, create_game, create_scene, add_mechanic, modify_game."""
from __future__ import annotations
import re
import shutil
import time
from pathlib import Path

from . import api_check, config, context, security
from .errors import McpError
from .mechanics import registry as R
from .mechanics import scenes as SC
from .mechanics import specs as S
from .project import projectfile, scaffold, tscn
from .project.manifest import Manifest
from .runtime import editor as editor_rt

TOPDOWN_WORDS = ("top-down", "top down", "topdown", "dungeon", "rpg", "zelda", "roguelike", "shooter", "twin-stick", "adventure", "maze", "pacman", "puzzle")
PLATFORMER_WORDS = ("platform", "mario", "metroidvania", "side-scroll", "side scroll", "jump", "runner")


def infer_genre(type_: str, name: str, description: str, genre: str | None) -> str:
    if genre:
        g = genre.lower().replace("-", "").replace("_", "").replace(" ", "")
        if g in ("platformer", "topdown", "3d"):
            return g
        raise McpError("INVALID_ARGUMENT", "genre 는 platformer|topdown|3d 중 하나여야 합니다 (지원 장르)")
    if type_.lower() == "3d":
        return "3d"
    text = f"{name} {description}".lower()
    if any(w in text for w in PLATFORMER_WORDS):
        return "platformer"
    if any(w in text for w in TOPDOWN_WORDS):
        return "topdown"
    return "platformer"


def layout_for(genre: str) -> dict:
    return {"platformer": S.platformer_layout, "topdown": S.topdown_layout, "3d": S.layout_3d}[genre]()


def _minimal_main(name: str, type_: str) -> dict:
    if type_ == "3d":
        return {"name": "Main", "type": "Node3D", "children": [
            {"name": "Camera3D", "type": "Camera3D", "props": {"position": [0, 1.5, 4]}},
            {"name": "Light", "type": "DirectionalLight3D", "props": {"rotation_degrees": [-50, -30, 0]}}]}
    return {"name": "Main", "type": "Node2D", "children": [
        {"name": "Background", "type": "ColorRect", "props": {"size": [1280, 720], "color": S.COLORS["bg"], "mouse_filter": 2}},
        {"name": "Title", "type": "Label", "props": {"text": name, "position": [24, 20], "theme_override_font_sizes/font_size": 32}}]}


def _target_dir(name: str, path: str | None) -> Path:
    if path:
        return security.check_allowed_root(Path(path))
    return security.check_allowed_root(config.workspace() / security.safe_name(name))


async def _init_project(root: Path, name: str, type_: str) -> list[str]:
    created = scaffold.create_project_files(root, name, type_)
    await editor_rt.get_editor(root)
    created += ["scenes/", "scripts/", "assets/"]
    return created


async def create_project(name: str, type: str = "2d", path: str | None = None, overwrite: bool = False) -> dict:
    if type.lower() not in ("2d", "3d"):
        raise McpError("INVALID_ARGUMENT", "type 은 '2d' 또는 '3d' 여야 합니다")
    type_ = type.lower()
    root = _target_dir(name, path)
    if (root / "project.godot").exists() and not overwrite:
        raise McpError("PROJECT_EXISTS", f"이미 프로젝트가 있습니다: {root} (overwrite=true 로 덮어쓰기)")
    if (root / "project.godot").exists():
        await editor_rt.stop_editor(root)
        for sub in ("scenes", "scripts", ".godot_mcp"):
            shutil.rmtree(root / sub, ignore_errors=True)
    t0 = time.monotonic()
    created = await _init_project(root, name, type_)
    ed = await editor_rt.editor_call(root, "create_scene", {"path": "res://scenes/main.tscn", "overwrite": True, "root": _minimal_main(name, type_)})
    await editor_rt.editor_call(root, "scan", timeout=60)
    Manifest(root).save({"version": 1, "name": name, "type": type_, "genre": None, "mechanics": {}, "created": time.time()})
    context.set_current(root)
    info = await editor_rt.editor_call(root, "info")
    return {"success": True, "project": str(root), "name": name, "type": type_, "main_scene": "res://scenes/main.tscn",
            "created": created + ["scenes/main.tscn"], "godot": info.get("godot"), "editor": {"running": True, "display": info.get("display_driver")},
            "warnings": ed.get("warnings", []), "duration": round(time.monotonic() - t0, 1),
            "next": "create_game(...) 또는 add_mechanic(...) 으로 게임을 구성하세요"}


async def create_game(name: str, type: str = "2d", description: str = "", genre: str | None = None, mechanics: list[str] | None = None,
                      path: str | None = None, project: str | None = None, overwrite: bool = False) -> dict:
    t0 = time.monotonic()
    type_ = type.lower()
    if type_ not in ("2d", "3d"):
        raise McpError("INVALID_ARGUMENT", "type 은 '2d' 또는 '3d' 여야 합니다")
    g = infer_genre(type_, name, description, genre)
    if g == "3d":
        type_ = "3d"
    if project:
        root = context.resolve_project(project)
        if (root / "scripts" / "player.gd").exists() and not overwrite:
            raise McpError("GAME_EXISTS", "이미 게임 구조가 있습니다. add_mechanic/modify_game 을 사용하거나 overwrite=true 를 지정하세요.")
    else:
        root = _target_dir(name, path)
        if (root / "project.godot").exists() and not overwrite:
            raise McpError("PROJECT_EXISTS", f"이미 프로젝트가 있습니다: {root} (overwrite=true 또는 project= 로 기존 프로젝트에 적용)")
        if (root / "project.godot").exists():
            await editor_rt.stop_editor(root)
            for sub in ("scenes", "scripts", ".godot_mcp"):
                shutil.rmtree(root / sub, ignore_errors=True)
        await _init_project(root, name, type_)
        context.set_current(root)
    layout = layout_for(g)
    man = Manifest(root)
    data = man.load()
    data.update({"name": name, "type": type_, "genre": g, "description": description, "layout": layout, "mechanics": {}})
    man.save(data)
    if overwrite:  # 기존 파일 재생성 허용
        for scene in ("main", "player", "hud"):
            (root / "scenes" / f"{scene}.tscn").unlink(missing_ok=True)
    keys = ["player_movement"]
    extra: list[str] = []
    for m in mechanics or []:
        mech, _ = R.resolve(m)
        extra.append(m)
        keys.append(mech.key)
    res = await _apply_mechanics(root, g, layout, [(k, {}) for k in keys], extra_opts=_collect_opts(mechanics or []), title=name)
    checks = await _post_checks(root, res["files"])
    return {"success": checks["syntax"]["success"] and checks["validate"]["success"], "project": str(root), "name": name, "type": type_, "genre": g,
            "main_scene": "res://scenes/main.tscn", "mechanics": res["applied"], "files": res["files"], "scenes": res["scenes"], "inputs": res["inputs"],
            "notes": res["notes"], "checks": checks, "duration": round(time.monotonic() - t0, 1),
            "next": "run() 으로 실행하거나 add_mechanic()/modify_game() 으로 기능을 추가하세요"}


def _collect_opts(requests: list[str]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for r in requests:
        try:
            mech, opts = R.resolve(r)
            out.setdefault(mech.key, {}).update(opts)
        except McpError:
            pass
    return out


async def _apply_mechanics(root: Path, genre: str, layout: dict, requested: list[tuple[str, dict]], *, extra_opts: dict | None = None,
                           force: bool = False, title: str = "Game") -> dict:
    man = Manifest(root)
    data = man.load()
    done = data.setdefault("mechanics", {})
    ordered = R.order([k for k, _ in requested])
    applied, skipped = [], []
    ctx = R.Ctx(root, genre, layout, {})
    req_opts = {k: o for k, o in requested}
    for key in ordered:
        mech = R.get(key)
        if genre not in mech.genres:
            raise McpError("MECHANIC_UNSUPPORTED_FOR_GENRE", f"'{mech.key.replace('_', ' ')}' 는 {genre} 게임에서 지원되지 않습니다 (지원 장르: {sorted(mech.genres)})",
                           hint="modify_game(files=...) 로 직접 구현하거나 다른 장르/메카닉을 선택하세요.")
        if key in done and not force and key not in req_opts:
            continue  # 의존성으로 이미 적용된 것
        if key in done and not force:
            skipped.append(key)
            continue
        ctx.options = {**(extra_opts or {}).get(key, {}), **req_opts.get(key, {}), "title": title}
        await mech.apply(ctx)
        done[key] = {"applied": time.time(), "options": {k: v for k, v in ctx.options.items() if k != "title"}}
        applied.append(key)
        data["mechanics"] = done
        man.save(data)  # 단계별 저장(중간 실패 대비)
    if ctx.files or ctx.scenes:
        ctx.dirty = True
    await ctx.flush_scan()
    return {"applied": applied, "skipped": skipped, "files": ctx.files, "scenes": ctx.scenes, "inputs": ctx.inputs, "notes": ctx.notes, "warnings": ctx.warnings}


async def _post_checks(root: Path, files: list[str]) -> dict:
    gd = [f for f in files if f.endswith(".gd")]
    await api_check.refresh_class_cache(root)
    from .validation import syntax as syn
    sres = await syn.syntax_check(root, files=gd) if gd else {"success": True, "errors": [], "checked_files": 0}
    vres = await api_check.validate(str(root))
    return {"syntax": {"success": sres["success"], "errors": sres["errors"], "checked_files": sres["checked_files"]},
            "validate": {"success": vres["success"], "errors": vres["errors"], "warnings": vres["warnings"][:5]}}


async def add_mechanic(mechanic: str, project: str | None = None, options: dict | None = None, force: bool = False) -> dict:
    root = context.resolve_project(project)
    t0 = time.monotonic()
    mech, parsed = R.resolve(mechanic)
    opts = {**parsed, **(options or {})}
    man = Manifest(root).load()
    genre = man.get("genre") or _detect_genre(root)
    layout = man.get("layout") or layout_for(genre)
    if not man.get("genre"):
        man.update({"genre": genre, "layout": layout})
        Manifest(root).save({**Manifest(root).load(), "genre": genre, "layout": layout})
    if genre not in mech.genres:
        raise McpError("MECHANIC_UNSUPPORTED_FOR_GENRE", f"'{mech.key.replace('_', ' ')}' 는 {genre} 게임에서 지원되지 않습니다 (지원 장르: {sorted(mech.genres)})",
                       hint="modify_game(files=...) 로 직접 구현할 수 있습니다.")
    res = await _apply_mechanics(root, genre, layout, [(mech.key, opts)], force=force, title=man.get("name", "Game"))
    checks = await _post_checks(root, res["files"])
    ok = checks["syntax"]["success"] and checks["validate"]["success"]
    status = "already_applied" if not res["applied"] else "applied"
    out = {"success": ok, "status": status, "mechanic": mech.key.replace("_", " "), "applied": [a.replace("_", " ") for a in res["applied"]], "genre": genre,
           "files": res["files"], "scenes": res["scenes"], "inputs": res["inputs"], "notes": res["notes"] + ([f"이미 적용됨 (force=true 로 재적용)"] if status == "already_applied" else []),
           "checks": checks, "duration": round(time.monotonic() - t0, 1)}
    if not ok:
        out["next"] = "fix() 로 자동 수정하거나 errors() 로 상세 확인"
    return out


def _detect_genre(root: Path) -> str:
    main = projectfile.get(projectfile.parse(root / "project.godot"), "application", "run/main_scene", "res://scenes/main.tscn")
    p = root / main[6:]
    if p.exists():
        sc = tscn.parse(p)
        if sc.nodes and sc.nodes[0].type == "Node3D":
            return "3d"
    return "platformer"


def _snake(name: str) -> str:
    s = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", name.strip())
    return re.sub(r"[^A-Za-z0-9]+", "_", s).strip("_").lower() or "scene"


async def create_scene(name: str, project: str | None = None, template: str | None = None, root_type: str | None = None,
                       overwrite: bool = False, set_main: bool = False) -> dict:
    root = context.resolve_project(project)
    man = Manifest(root).load()
    genre = man.get("genre") or _detect_genre(root)
    snake = _snake(name)
    low = name.lower()
    if template is None:
        template = "menu" if "menu" in low or "title" in low else "level" if any(w in low for w in ("level", "stage", "world", "map")) else "empty"
    if template not in ("empty", "menu", "level", "ui"):
        raise McpError("INVALID_ARGUMENT", "template 은 empty|menu|level|ui 중 하나여야 합니다")
    res_path = f"res://scenes/{snake}.tscn"
    pascal = "".join(w.capitalize() for w in snake.split("_"))
    files: list[str] = []
    ctx = R.Ctx(root, genre, man.get("layout") or layout_for(genre), {})
    if template == "menu":
        ctx.write("scripts/menu.gd", R.G.MENU, overwrite=False)
        await ctx.scan()
        spec = SC.menu_scene(name)
        spec["name"] = pascal
    elif template == "level":
        if "player_movement" not in man.get("mechanics", {}):
            raise McpError("PLAYER_MISSING", "level 템플릿은 플레이어가 필요합니다. 먼저 create_game/add_mechanic('player movement') 를 호출하세요.")
        spec = SC.main_scene(genre, ctx.layout)
        spec["name"] = pascal
    elif template == "ui":
        spec = {"name": pascal, "type": "Control", "props": {"anchor_right": 1.0, "anchor_bottom": 1.0, "offset_right": 0.0, "offset_bottom": 0.0}}
    else:
        rt = root_type or ("Node3D" if genre == "3d" else "Node2D")
        spec = {"name": pascal, "type": rt}
    r = await ctx.edit("create_scene", {"path": res_path, "overwrite": overwrite, "root": spec})
    if set_main:
        await ctx.edit("set_settings", {"settings": {"application/run/main_scene": res_path}})
    ctx.dirty = True
    await ctx.flush_scan()
    checks = await _post_checks(root, ctx.files)
    return {"success": checks["validate"]["success"], "scene": res_path, "root": pascal, "template": template, "node_count": r.get("node_count"),
            "files": ctx.files, "main_scene_changed": set_main, "warnings": r.get("warnings", []), "checks": checks}


# ------------------------------------------------------------------ modify --

async def modify_game(project: str | None = None, *, files: dict[str, str] | None = None, edits: list[dict] | None = None,
                      set: dict | None = None, settings: dict | None = None, attach: dict[str, str] | None = None, delete: list[str] | None = None) -> dict:
    """AI 가 직접 작성한 코드/리소스 변경을 한 번의 호출로 적용하고 즉시 검증한다.

    files:   {경로: 전체 내용}  — 스크립트/셰이더/JSON 등 생성·덮어쓰기
    edits:   [{file, find, replace, count?}] — 정확 문자열 치환 (find 는 정확히 1회 일치해야 함, count=0 이면 전부)
    set:     {"Player.speed": 300, "scenes/enemy.tscn:Enemy.detect_range": 400} — 씬 노드 속성 설정 ("[씬:]노드.속성")
    settings: ProjectSettings 키/값 (input/ autoload/ 키 제외)
    attach:  {"[씬:]노드": "res://scripts/x.gd"} — 노드에 스크립트 부착
    delete:  [경로] — 파일 삭제 (보호 영역 불가)
    """
    root = context.resolve_project(project)
    changes: list[dict] = []
    written_gd: list[str] = []
    touched_scenes: list[str] = []
    if not any([files, edits, set, settings, attach, delete]):
        raise McpError("INVALID_ARGUMENT", "files/edits/set/settings/attach/delete 중 하나 이상이 필요합니다")
    for rel, content in (files or {}).items():
        p = security.resolve(root, rel, write=True)
        if p.suffix not in (".gd", ".gdshader", ".json", ".txt", ".cfg", ".md", ".tres", ".csv", ".toml", ".gdshaderinc"):
            raise McpError("UNSUPPORTED_FILE_TYPE", f"허용되지 않는 파일 형식: {p.suffix} (씬은 create_scene/set/attach 사용)", path=rel)
        existed = p.exists()
        old = p.read_text() if existed else None
        if old == content:
            changes.append({"file": security.to_res(root, p.parent) + "/" + p.name, "action": "unchanged"})
            continue
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
        res = security.to_res(root, p)
        changes.append({"file": res, "action": "updated" if existed else "created", "lines": content.count("\n") + 1})
        if p.suffix == ".gd":
            written_gd.append(res)
    for ed in edits or []:
        p = security.resolve(root, ed.get("file", ""), write=True)
        if not p.is_file():
            raise McpError("FILE_NOT_FOUND", f"파일이 없습니다: {ed.get('file')}")
        text = p.read_text()
        find, rep = ed.get("find", ""), ed.get("replace", "")
        n = text.count(find) if find else 0
        want = int(ed.get("count", 1))
        if n == 0:
            raise McpError("EDIT_NO_MATCH", f"find 문자열을 찾지 못했습니다: {find[:80]!r}", file=ed.get("file"))
        if want == 1 and n > 1:
            raise McpError("EDIT_AMBIGUOUS", f"find 문자열이 {n}번 일치합니다. 더 긴 문맥을 지정하거나 count=0(전체) 를 사용하세요.", file=ed.get("file"))
        p.write_text(text.replace(find, rep) if want == 0 else text.replace(find, rep, want))
        res = security.to_res(root, p)
        changes.append({"file": res, "action": "edited", "replacements": n if want == 0 else min(n, want)})
        if p.suffix == ".gd":
            written_gd.append(res)
    for rel in delete or []:
        p = security.resolve(root, rel, write=True)
        if p.is_file():
            p.unlink()
            changes.append({"file": security.to_res(root, p), "action": "deleted"})
        else:
            changes.append({"file": rel, "action": "not_found"})
    if files or edits or delete:
        await editor_rt.editor_call(root, "scan", timeout=90)
    if settings:
        r = await editor_rt.editor_call(root, "set_settings", {"settings": settings})
        changes.append({"settings": r.get("applied")})
    by_scene: dict[str, list[dict]] = {}
    for key, props_val in (set or {}).items():
        scene, node, prop = _parse_target(root, key)
        by_scene.setdefault(scene, []).append({"op": "set", "node": node, "props": {prop: props_val}})
    for key, script in (attach or {}).items():
        scene, node = _parse_node_target(root, key)
        by_scene.setdefault(scene, []).append({"op": "script", "node": node, "script": script})
    for scene, ops in by_scene.items():
        r = await editor_rt.editor_call(root, "patch_scene", {"path": scene, "ops": _merge_set_ops(ops)})
        touched_scenes.append(scene)
        changes.append({"scene": scene, "changes": r.get("changes"), "warnings": r.get("warnings")})
    syn = None
    if written_gd:
        await api_check.refresh_class_cache(root)
        from .validation import syntax as syn_mod
        syn = await syn_mod.syntax_check(root, files=list(dict.fromkeys(written_gd)))
    val = await api_check.validate(str(root)) if (touched_scenes or files or attach) else None
    ok = (syn is None or syn["success"]) and (val is None or val["success"])
    out = {"success": ok, "changes": changes}
    if syn is not None:
        out["syntax"] = {"success": syn["success"], "errors": syn["errors"], "checked_files": syn["checked_files"]}
    if val is not None:
        out["validate"] = {"success": val["success"], "errors": val["errors"], "warnings": val["warnings"][:5]}
    if not ok:
        out["next"] = "fix() 로 자동 수정하거나 오류를 보고 modify_game 으로 다시 수정하세요"
    return out


def _merge_set_ops(ops: list[dict]) -> list[dict]:
    merged: list[dict] = []
    for op in ops:
        if op["op"] == "set" and merged and merged[-1]["op"] == "set" and merged[-1]["node"] == op["node"]:
            merged[-1]["props"].update(op["props"])
        else:
            merged.append(op)
    return merged


def _scene_candidates(root: Path) -> list[Path]:
    return [p for p in sorted((root).rglob("*.tscn")) if not p.relative_to(root).as_posix().startswith((".godot", "addons/"))]


def _parse_node_target(root: Path, key: str) -> tuple[str, str]:
    rest = key[6:] if key.startswith("res://") else key
    if ":" in rest:
        scene_part, node = rest.split(":", 1)
        scene = "res://" + scene_part.lstrip("/")
        if not (root / scene[6:]).exists():
            raise McpError("SCENE_NOT_FOUND", f"씬을 찾을 수 없습니다: {scene}")
        return scene, node
    for p in _scene_candidates(root):
        sc = tscn.parse(p)
        for n in sc.nodes:
            if n.name == key or n.path == key:
                return security.to_res(root, p), n.path
    raise McpError("NODE_NOT_FOUND", f"노드 '{key}' 를 어떤 씬에서도 찾지 못했습니다. '씬경로:노드' 형식을 사용하세요.")


def _parse_target(root: Path, key: str) -> tuple[str, str, str]:
    if "." not in key:
        raise McpError("INVALID_ARGUMENT", f"set 키는 '[씬:]노드.속성' 형식이어야 합니다: {key!r}")
    target, prop = key.rsplit(".", 1)
    if prop in ("tscn",):  # 'scenes/x.tscn' 만 준 경우
        raise McpError("INVALID_ARGUMENT", f"속성 이름이 없습니다: {key!r}")
    scene, node = _parse_node_target(root, target)
    return scene, node, prop
