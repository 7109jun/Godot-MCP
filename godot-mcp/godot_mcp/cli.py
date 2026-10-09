"""godot-mcp CLI — MCP Tool 과 1:1 대응하는 AI Agent 용 고수준 CLI.

  godot-mcp create-game "Platformer" --mechanic "double jump"
  godot-mcp add-mechanic "double jump"
  godot-mcp syntax-check / validate / run / errors / fix / test / build ...
"""
from __future__ import annotations
import os
import argparse
import asyncio
import json
import sys
from pathlib import Path

from . import api, __version__
from .errors import McpError
from .util_yaml import dump


def _common() -> argparse.ArgumentParser:
    c = argparse.ArgumentParser(add_help=False)
    c.add_argument("--yaml", action="store_true", default=argparse.SUPPRESS, help="결과를 YAML 로 출력")
    c.add_argument("--project", "-C", default=argparse.SUPPRESS, help="대상 프로젝트 (기본: 현재 프로젝트)")
    return c


_COMMON = _common()


def _p(sub, name, help, aliases=()):
    return sub.add_parser(name, help=help, aliases=list(aliases), description=help, parents=[_COMMON])


def _kv(items: list[str] | None, cast_json: bool = True) -> dict:
    out = {}
    for it in items or []:
        if "=" not in it:
            raise McpError("INVALID_ARGUMENT", f"'키=값' 형식이어야 합니다: {it!r}")
        k, v = it.split("=", 1)
        if cast_json:
            try:
                v = json.loads(v)
            except ValueError:
                pass
        out[k] = v
    return out


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="godot-mcp", description="AI Agent 용 고수준 Godot 제작 CLI (MCP Tool 과 동일한 개념)")
    ap.add_argument("--version", action="version", version=f"godot-mcp {__version__}")
    ap.add_argument("--yaml", action="store_true", help="결과를 YAML 로 출력")
    ap.add_argument("--project", "-C", help="대상 프로젝트 (기본: 현재 프로젝트)")
    sub = ap.add_subparsers(dest="cmd", required=True, metavar="<command>")

    s = _p(sub, "create-project", "빈 Godot 프로젝트 생성")
    s.add_argument("name"); s.add_argument("--type", default="2d", choices=["2d", "3d"]); s.add_argument("--path"); s.add_argument("--overwrite", action="store_true")

    s = _p(sub, "create-game", "실행 가능한 게임 기본 구조 생성")
    s.add_argument("name"); s.add_argument("--type", default="2d", choices=["2d", "3d"]); s.add_argument("--description", default="")
    s.add_argument("--genre", choices=["platformer", "topdown", "3d"]); s.add_argument("--mechanic", action="append", dest="mechanics", help="추가 메카닉 (반복 가능)")
    s.add_argument("--path"); s.add_argument("--overwrite", action="store_true")

    s = _p(sub, "create-scene", "템플릿 기반 씬 생성")
    s.add_argument("name"); s.add_argument("--template", choices=["empty", "menu", "level", "ui"]); s.add_argument("--root-type"); s.add_argument("--overwrite", action="store_true")
    s.add_argument("--set-main", action="store_true")

    s = _p(sub, "add-mechanic", "메카닉 추가 (예: 'double jump', 'enemy AI', '5 coins')")
    s.add_argument("mechanic", nargs="?"); s.add_argument("--option", "-o", action="append", help="키=값 (예: count=12)"); s.add_argument("--force", action="store_true")
    s.add_argument("--list", action="store_true", help="지원 메카닉 목록")

    s = _p(sub, "modify-game", "코드/속성/설정 수정 후 즉시 검증")
    s.add_argument("--spec", help="JSON 파일 또는 '-'(stdin): {files, edits, set, settings, attach, delete}")
    s.add_argument("--write", action="append", metavar="RES_PATH=LOCAL_FILE", help="로컬 파일 내용을 프로젝트 경로에 기록")
    s.add_argument("--set", action="append", dest="set_", metavar="[SCENE:]NODE.PROP=VALUE")
    s.add_argument("--setting", action="append", metavar="KEY=VALUE")
    s.add_argument("--attach", action="append", metavar="[SCENE:]NODE=res://script.gd")
    s.add_argument("--delete", action="append")

    s = _p(sub, "syntax-check", "GDScript 문법 검사 (Godot 실제 파서)", aliases=["syntax_check"])
    g = s.add_mutually_exclusive_group()
    g.add_argument("--changed", action="store_true"); g.add_argument("--all", action="store_true"); s.add_argument("--file", action="append", dest="files")

    s = _p(sub, "validate", "프로젝트 전체 구조 검증")
    s.add_argument("--no-engine", action="store_true", help="엔진 로드 검사 생략 (더 빠름)")

    s = _p(sub, "run", "게임 실행")
    s.add_argument("--scene"); s.add_argument("--timeout", type=float); s.add_argument("--observe", type=float, default=1.5); s.add_argument("--headless", action="store_true")
    _p(sub, "stop", "게임 종료").add_argument("--editor", action="store_true")

    s = _p(sub, "play", "실행 중인 게임에 입력 주입")
    s.add_argument("actions", nargs="+", help="액션 또는 액션:hold_ms (예: move_right:600 jump)")

    s = _p(sub, "inspect", "씬 구조 조회")
    s.add_argument("--scene"); s.add_argument("--depth", type=int, default=6); s.add_argument("--no-props", action="store_true")
    _p(sub, "project-state", "프로젝트 개요", aliases=["project_state"])
    s = _p(sub, "errors", "수집된 오류 조회")
    g = s.add_mutually_exclusive_group()
    g.add_argument("--runtime", action="store_true"); g.add_argument("--script", action="store_true"); g.add_argument("--editor", action="store_true"); g.add_argument("--static", action="store_true")
    s.add_argument("--limit", type=int, default=20)
    s = _p(sub, "game-state", "실행 중인 게임 상태", aliases=["game_state"])
    s.add_argument("--depth", type=int, default=3)
    s = _p(sub, "screenshot", "화면 캡처 (파일로 저장)")
    g = s.add_mutually_exclusive_group()
    g.add_argument("--game", action="store_true"); g.add_argument("--editor", action="store_true"); s.add_argument("--max-width", type=int, default=960); s.add_argument("--no-auto-run", action="store_true")

    s = _p(sub, "test", "실제 검증 (문법→구조→실행→관찰→게임플레이)")
    s.add_argument("--all", action="store_true"); s.add_argument("--scene"); s.add_argument("--gameplay", action="store_true")
    s = _p(sub, "fix", "오류 자동 수정")
    g = s.add_mutually_exclusive_group()
    g.add_argument("--errors", action="store_true"); g.add_argument("--syntax", action="store_true"); g.add_argument("--runtime", action="store_true"); g.add_argument("--validate", action="store_true")
    s.add_argument("--dry-run", action="store_true")
    s = _p(sub, "build", "프로젝트 빌드(export)")
    g = s.add_mutually_exclusive_group()
    g.add_argument("--debug", action="store_true"); g.add_argument("--release", action="store_true")
    s.add_argument("--target", default="linux", choices=["linux", "pck"]); s.add_argument("--force", action="store_true")

    s = _p(sub, "serve", "MCP 서버 실행 (기본 stdio)")
    s.add_argument("--transport", choices=["stdio", "http", "sse"], default="stdio"); s.add_argument("--host", default="127.0.0.1"); s.add_argument("--port", type=int, default=8765)
    s.add_argument("--token", help="Bearer 토큰 (기본: $GODOT_MCP_REMOTE_TOKEN 또는 자동 생성 파일)")
    s.add_argument("--certfile"); s.add_argument("--keyfile"); s.add_argument("--path", default="/mcp")
    ap.add_argument("--remote", metavar="URL", default=argparse.SUPPRESS, help="원격 godot-mcp 서버(예: https://host:8765/mcp) 로 실행 ($GODOT_MCP_REMOTE_URL)")
    ap.add_argument("--remote-token", metavar="TOKEN", default=argparse.SUPPRESS, help="원격 Bearer 토큰 ($GODOT_MCP_REMOTE_TOKEN)")
    return ap


async def dispatch(a: argparse.Namespace):
    c = a.cmd.replace("_", "-")
    P = a.project
    if c == "create-project":
        return await api.create_project(a.name, a.type, a.path, a.overwrite)
    if c == "create-game":
        return await api.create_game(a.name, a.type, a.description, a.genre, a.mechanics, a.path, P, a.overwrite)
    if c == "create-scene":
        return await api.create_scene(a.name, P, a.template, a.root_type, a.overwrite, a.set_main)
    if c == "add-mechanic":
        if a.list:
            return {"success": True, "mechanics": api.supported_mechanics()}
        if not a.mechanic:
            raise McpError("INVALID_ARGUMENT", "메카닉 이름이 필요합니다 (godot-mcp add-mechanic --list 로 목록 확인)")
        return await api.add_mechanic(a.mechanic, P, _kv(a.option), a.force)
    if c == "modify-game":
        spec: dict = {}
        if a.spec:
            spec = json.loads(sys.stdin.read() if a.spec == "-" else Path(a.spec).read_text())
        files = dict(spec.get("files", {}))
        for item in a.write or []:
            dest, src = item.split("=", 1)
            files[dest] = Path(src).read_text()
        return await api.modify_game(P, files=files or None, edits=spec.get("edits"), set={**spec.get("set", {}), **_kv(a.set_)} or None,
                                     settings={**spec.get("settings", {}), **_kv(a.setting)} or None, attach={**spec.get("attach", {}), **_kv(a.attach, False)} or None,
                                     delete=[*spec.get("delete", []), *(a.delete or [])] or None)
    if c == "syntax-check":
        return await api.syntax_check(P, files=a.files, changed=a.changed)
    if c == "validate":
        return await api.validate(P, engine=not a.no_engine)
    if c == "run":
        return await api.run(P, scene=a.scene, timeout=a.timeout, observe=a.observe, headless=a.headless)
    if c == "stop":
        return await api.stop(P, editor=a.editor)
    if c == "play":
        acts = []
        for x in a.actions:
            name, _, hold = x.partition(":")
            acts.append({"action": name, "hold_ms": int(hold)} if hold else name)
        return await api.play(acts, P)
    if c == "inspect":
        return await api.inspect(P, scene=a.scene, depth=a.depth, props=not a.no_props, format="json")
    if c == "project-state":
        return await api.project_state(P)
    if c == "errors":
        scope = "runtime" if a.runtime else "script" if a.script else "editor" if a.editor else "static" if a.static else "all"
        return await api.errors(P, scope=scope, limit=a.limit)
    if c == "game-state":
        return await api.game_state(P, depth=a.depth)
    if c == "screenshot":
        r = await api.screenshot(P, target="editor" if a.editor else "game", max_width=a.max_width, auto_run=not a.no_auto_run)
        r.pop("png", None)
        return r
    if c == "test":
        return await api.test(P, scene=a.scene, all=a.all, gameplay=a.gameplay)
    if c == "fix":
        scope = "syntax" if a.syntax else "runtime" if a.runtime else "validate" if a.validate else "errors"
        return await api.fix(P, scope=scope, dry_run=a.dry_run)
    if c == "build":
        return await api.build(P, target=a.target, mode="debug" if a.debug else "release", force=a.force)
    raise McpError("INVALID_ARGUMENT", f"알 수 없는 명령: {a.cmd}")


def remote_args(a: argparse.Namespace) -> tuple[str, dict]:
    """CLI 인자 → (MCP 도구 이름, 도구 인자). 서버 파일시스템 기준으로 실행된다."""
    c = a.cmd.replace("_", "-")
    P = a.project
    if c == "create-project":
        return "create_project", dict(name=a.name, type=a.type, path=a.path, overwrite=a.overwrite)
    if c == "create-game":
        return "create_game", dict(name=a.name, type=a.type, description=a.description, genre=a.genre, mechanics=a.mechanics, path=a.path, project=P, overwrite=a.overwrite)
    if c == "create-scene":
        return "create_scene", dict(name=a.name, project=P, template=a.template, root_type=a.root_type, overwrite=a.overwrite, set_main=a.set_main)
    if c == "add-mechanic":
        if a.list or not a.mechanic:
            raise McpError("INVALID_ARGUMENT", "원격에서는 메카닉 이름이 필요합니다 (목록은 로컬에서 add-mechanic --list)")
        return "add_mechanic", dict(mechanic=a.mechanic, project=P, options=_kv(a.option) or None, force=a.force)
    if c == "modify-game":
        spec: dict = {}
        if a.spec:
            spec = json.loads(sys.stdin.read() if a.spec == "-" else Path(a.spec).read_text())
        files = dict(spec.get("files", {}))
        for item in a.write or []:
            dest, src = item.split("=", 1)
            files[dest] = Path(src).read_text()  # 로컬 파일 내용을 서버로 전송
        return "modify_game", dict(project=P, files=files or None, edits=spec.get("edits"), set={**spec.get("set", {}), **_kv(a.set_)} or None,
                                   settings={**spec.get("settings", {}), **_kv(a.setting)} or None, attach={**spec.get("attach", {}), **_kv(a.attach, False)} or None,
                                   delete=[*spec.get("delete", []), *(a.delete or [])] or None)
    if c == "syntax-check":
        return "syntax_check", dict(project=P, files=a.files, changed=a.changed)
    if c == "validate":
        return "validate", dict(project=P, engine=not a.no_engine)
    if c == "run":
        return "run", dict(project=P, scene=a.scene, timeout=a.timeout, observe=a.observe, headless=a.headless)
    if c == "stop":
        return "stop", dict(project=P, editor=a.editor)
    if c == "play":
        acts = []
        for x in a.actions:
            name, _, hold = x.partition(":")
            acts.append({"action": name, "hold_ms": int(hold)} if hold else name)
        return "play", dict(actions=acts, project=P)
    if c == "inspect":
        return "inspect", dict(project=P, scene=a.scene, depth=a.depth, props=not a.no_props, format="json")
    if c == "project-state":
        return "project_state", dict(project=P)
    if c == "errors":
        scope = "runtime" if a.runtime else "script" if a.script else "editor" if a.editor else "static" if a.static else "all"
        return "errors", dict(project=P, scope=scope, limit=a.limit)
    if c == "game-state":
        return "game_state", dict(project=P, depth=a.depth)
    if c == "screenshot":
        return "screenshot", dict(project=P, target="editor" if a.editor else "game", max_width=a.max_width, auto_run=not a.no_auto_run)
    if c == "test":
        return "test", dict(project=P, scene=a.scene, all=a.all, gameplay=a.gameplay)
    if c == "fix":
        scope = "syntax" if a.syntax else "runtime" if a.runtime else "validate" if a.validate else "errors"
        return "fix", dict(project=P, scope=scope, dry_run=a.dry_run)
    if c == "build":
        return "build", dict(project=P, target=a.target, mode="debug" if a.debug else "release", force=a.force)
    raise McpError("INVALID_ARGUMENT", f"원격에서 지원하지 않는 명령: {a.cmd}")


def main(argv: list[str] | None = None) -> int:
    ap = build_parser()
    a = ap.parse_args(argv)
    if a.cmd == "serve":
        from . import server
        if a.transport == "stdio":
            server.mcp.run()
        else:
            from . import remote
            remote.serve(a.host, a.port, a.token, a.certfile, a.keyfile, "http" if a.transport == "http" else "sse", a.path)
        return 0
    url = getattr(a, "remote", None) or os.environ.get("GODOT_MCP_REMOTE_URL")
    if url:
        from . import remote
        tool, targs = remote_args(a)
        targs = {k: v for k, v in targs.items() if v is not None}
        tok = getattr(a, "remote_token", None) or os.environ.get("GODOT_MCP_REMOTE_TOKEN")
        try:
            result = asyncio.run(remote.call_remote(url, tok, tool, targs))
        except Exception as e:  # noqa: BLE001
            result = {"success": False, "error": {"code": "REMOTE_UNREACHABLE", "message": str(e)}}
        imgs = result.pop("_images", []) if isinstance(result, dict) else []
        for i, b in enumerate(imgs):
            f = Path(f"screenshot{'' if i == 0 else i}.png"); f.write_bytes(b); result["saved_local"] = str(f.resolve())
        return _emit(a, result)
    try:
        result = asyncio.run(dispatch(a))
    except McpError as e:
        result = e.to_dict()
    except KeyboardInterrupt:
        return 130
    return _emit(a, result)


def _emit(a, result) -> int:
    out = dump(result) if a.yaml and not isinstance(result, str) else (result if isinstance(result, str) else json.dumps(result, indent=2, ensure_ascii=False, default=str))
    try:
        print(out)
        sys.stdout.flush()
    except BrokenPipeError:
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        return 0
    return 0 if not (isinstance(result, dict) and result.get("success") is False) else 1


if __name__ == "__main__":
    raise SystemExit(main())
