"""Godot MCP 서버 (FastMCP). AI Agent 에게 고수준 Tool 만 노출한다."""
import asyncio
import json
import os
import time
import traceback
from typing import Annotated, Any, Optional

from fastmcp import FastMCP
from fastmcp.utilities.types import Image
from mcp.types import ToolAnnotations
from pydantic import Field

from . import __version__, api, config, context
from .errors import McpError

INSTRUCTIONS = """\
Godot MCP — AI Agent 가 Godot 4 로 게임을 스스로 만들고, 검사/실행/관찰/수정/테스트/빌드하는 고수준 도구 모음.
저수준 노드/속성 조작 도구는 없습니다. 한 번의 호출이 여러 Godot 작업을 수행합니다.

기본 루프:
  create_game → add_mechanic(…) → syntax_check → validate → run → game_state/screenshot/errors → fix → test → build
코드를 직접 쓸 때는 modify_game(files=…) 한 번으로 쓰기+즉시 검증(문법/참조)까지 수행됩니다.
모든 결과는 구조화된 JSON 입니다 (success, errors[], next 힌트). 오류는 file/line/message/context 를 포함합니다.
project 인자를 생략하면 마지막으로 사용한 프로젝트가 대상입니다.
"""

mcp = FastMCP("godot-mcp", instructions=INSTRUCTIONS, version=__version__)

_locks: dict[str, asyncio.Lock] = {}
_counter = 0

READ = ToolAnnotations(readOnlyHint=True, openWorldHint=False)
WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=False)
DESTRUCTIVE = ToolAnnotations(readOnlyHint=False, destructiveHint=True, openWorldHint=False)

Project = Annotated[Optional[str], Field(description="프로젝트 경로 또는 작업공간 내 프로젝트 이름. 생략하면 현재(마지막) 프로젝트")]


def _log_request(rid: str, tool: str, args: dict, ok: bool, dur: float, err: Optional[str]) -> None:
    try:
        line = json.dumps({"id": rid, "t": time.time(), "tool": tool, "args": {k: (v if len(str(v)) < 200 else str(v)[:200] + "…") for k, v in args.items() if v is not None},
                           "ok": ok, "ms": round(dur * 1000), "error": err}, ensure_ascii=False, default=str)
        with open(config.state_dir() / "requests.log", "a") as f:
            f.write(line + "\n")
    except OSError:
        pass


async def _call(tool: str, fn, args: dict, *, project: Optional[str] = None, mutating: bool = False, timeout: float = 300.0) -> Any:
    """요청 추적/검증/타임아웃/오류 구조화를 담당하는 공통 래퍼."""
    global _counter
    _counter += 1
    rid = f"r{os.getpid()}-{_counter}"
    t0 = time.monotonic()
    lock = None
    try:
        key = "default"
        if mutating:
            try:
                key = str(context.resolve_project(project)) if project or context.get_current() else "default"
            except McpError:
                key = "default"
            lock = _locks.setdefault(key, asyncio.Lock())
            await lock.acquire()
        try:
            result = await asyncio.wait_for(fn(), timeout)
        finally:
            if lock:
                lock.release()
        ok = not (isinstance(result, dict) and result.get("success") is False)
        if isinstance(result, dict):
            result.setdefault("request_id", rid)
        _log_request(rid, tool, args, ok, time.monotonic() - t0, None)
        return result
    except McpError as e:
        _log_request(rid, tool, args, False, time.monotonic() - t0, e.code)
        d = e.to_dict()
        d["request_id"] = rid
        return d
    except asyncio.TimeoutError:
        _log_request(rid, tool, args, False, time.monotonic() - t0, "TOOL_TIMEOUT")
        return {"success": False, "error": {"code": "TOOL_TIMEOUT", "message": f"{tool} 가 {timeout}s 안에 끝나지 않았습니다"}, "request_id": rid}
    except Exception as e:  # noqa: BLE001 - 내부 오류도 구조화해서 반환
        _log_request(rid, tool, args, False, time.monotonic() - t0, type(e).__name__)
        return {"success": False, "error": {"code": "INTERNAL_ERROR", "message": f"{type(e).__name__}: {e}", "trace": traceback.format_exc().splitlines()[-4:]}, "request_id": rid}


# ----------------------------------------------------------------- ACTION --

@mcp.tool(annotations=WRITE)
async def create_project(
    name: Annotated[str, Field(description="프로젝트 이름")],
    type: Annotated[str, Field(description="'2d' 또는 '3d'")] = "2d",
    path: Annotated[Optional[str], Field(description="생성 위치(허용된 작업공간 내). 생략하면 작업공간/<name>")] = None,
    overwrite: Annotated[bool, Field(description="이미 있으면 덮어쓰기")] = False,
) -> dict:
    """실행 가능한 빈 Godot 프로젝트를 만든다 (project.godot, 메인 씬, scripts/, assets/, 입력/디스플레이/렌더링 설정, MCP 플러그인 설치, 에디터 기동).
    게임 구조까지 필요하면 create_game 을 쓰세요."""
    return await _call("create_project", lambda: api.create_project(name, type, path, overwrite), dict(name=name, type=type, path=path), mutating=False, timeout=240)


@mcp.tool(annotations=WRITE)
async def create_game(
    name: Annotated[str, Field(description="게임 이름")],
    type: Annotated[str, Field(description="'2d' 또는 '3d'")] = "2d",
    description: Annotated[str, Field(description="게임 설명. 장르 추론에 사용 (예: 'top-down dungeon crawler', 'simple platform game')")] = "",
    genre: Annotated[Optional[str], Field(description="명시적 장르: platformer | topdown | 3d (생략하면 name/description 으로 추론)")] = None,
    mechanics: Annotated[Optional[list[str]], Field(description="바로 적용할 메카닉 목록, 예: ['double jump','health system','coin collection','enemy AI']")] = None,
    path: Annotated[Optional[str], Field(description="생성 위치")] = None,
    project: Project = None,
    overwrite: Annotated[bool, Field(description="기존 프로젝트/게임 구조를 다시 생성")] = False,
) -> dict:
    """실제 실행 가능한 게임의 기본 구조를 생성한다: 프로젝트 + 플레이어 이동 + 월드 레벨 + HUD + 입력 + GameState, 그리고 선택한 메카닉들.
    생성 직후 문법/프로젝트 검증 결과(checks)까지 반환한다. 이후 run() 으로 바로 실행 가능."""
    return await _call("create_game", lambda: api.create_game(name, type, description, genre, mechanics, path, project, overwrite),
                       dict(name=name, type=type, description=description, genre=genre, mechanics=mechanics), timeout=300)


@mcp.tool(annotations=WRITE)
async def create_scene(
    name: Annotated[str, Field(description="씬 이름 (scenes/<snake_case>.tscn 로 저장)")],
    project: Project = None,
    template: Annotated[Optional[str], Field(description="empty | menu | level | ui (생략하면 이름으로 추론: menu/level 등)")] = None,
    root_type: Annotated[Optional[str], Field(description="empty 템플릿의 루트 노드 타입 (예: Node2D, Control)")] = None,
    overwrite: bool = False,
    set_main: Annotated[bool, Field(description="이 씬을 프로젝트 메인 씬으로 지정")] = False,
) -> dict:
    """템플릿 기반으로 씬을 생성한다 (menu: Start/Quit 타이틀 메뉴, level: 플레이어/HUD/월드 포함 레벨, ui: 전체화면 Control, empty)."""
    return await _call("create_scene", lambda: api.create_scene(name, project, template, root_type, overwrite, set_main),
                       dict(name=name, template=template, set_main=set_main), project=project, mutating=True, timeout=120)


@mcp.tool(annotations=WRITE)
async def add_mechanic(
    mechanic: Annotated[str, Field(description="자연어에 가까운 기능명. 지원: player movement, double jump (triple jump), dash, health system, coin collection, "
                                               "enemy AI, inventory, pause menu, main menu. 개수/수치 포함 가능: '5 coins', 'triple jump'")],
    project: Project = None,
    options: Annotated[Optional[dict], Field(description="세부 옵션. 예: {'count': 12} (coins/enemies), {'max_health': 8}, {'extra_jumps': 2}")] = None,
    force: Annotated[bool, Field(description="이미 적용된 메카닉도 다시 적용")] = False,
) -> dict:
    """게임에 메카닉을 추가한다. 스크립트/씬/입력 액션/HUD 를 한 번에 구성하고 의존 메카닉(예: enemy AI → health system)도 자동 적용한다.
    적용 직후 문법+프로젝트 검증 결과를 반환한다. 지원하지 않는 기능은 supported 목록이 담긴 구조화된 오류를 반환한다 (그 경우 modify_game 으로 직접 구현)."""
    return await _call("add_mechanic", lambda: api.add_mechanic(mechanic, project, options, force), dict(mechanic=mechanic, options=options),
                       project=project, mutating=True, timeout=240)


@mcp.tool(annotations=DESTRUCTIVE)
async def modify_game(
    project: Project = None,
    files: Annotated[Optional[dict[str, str]], Field(description="{경로: 전체 내용} — GDScript/셰이더/JSON 등 생성·덮어쓰기. 예: {'res://scripts/boss.gd': 'extends Node…'}")] = None,
    edits: Annotated[Optional[list[dict]], Field(description="[{file, find, replace, count?}] 정확 문자열 치환. find 는 정확히 1번 일치해야 함(count=0 이면 전부)")] = None,
    set: Annotated[Optional[dict[str, Any]], Field(description="씬 노드 속성 설정 {'[씬경로:]노드.속성': 값}. 예: {'Player.speed': 320, 'res://scenes/enemy.tscn:Enemy.detect_range': 400}")] = None,
    settings: Annotated[Optional[dict[str, Any]], Field(description="ProjectSettings 키/값, 예: {'display/window/size/viewport_width': 1920}")] = None,
    attach: Annotated[Optional[dict[str, str]], Field(description="노드에 스크립트 부착 {'[씬경로:]노드': 'res://scripts/x.gd'}")] = None,
    delete: Annotated[Optional[list[str]], Field(description="삭제할 파일 경로")] = None,
) -> dict:
    """게임을 수정한다. 코드/리소스 쓰기, 정확 치환, 노드 속성, 프로젝트 설정, 스크립트 부착을 한 번의 호출로 적용하고,
    변경된 .gd 는 즉시 문법 검사, 변경된 씬/참조는 프로젝트 검증까지 수행해 결과(syntax/validate)와 함께 반환한다. 경로는 프로젝트 내부로 제한된다."""
    return await _call("modify_game", lambda: api.modify_game(project, files=files, edits=edits, set=set, settings=settings, attach=attach, delete=delete),
                       dict(files=list((files or {}).keys()), edits=len(edits or []), set=set, settings=settings, attach=attach, delete=delete),
                       project=project, mutating=True, timeout=180)


@mcp.tool(annotations=WRITE)
async def run(
    project: Project = None,
    scene: Annotated[Optional[str], Field(description="실행할 씬(이름 또는 res:// 경로). 생략하면 프로젝트 메인 씬")] = None,
    timeout: Annotated[Optional[float], Field(description="초 단위 실행 제한. 지나면 게임이 자동 종료(exit_code 124). 생략하면 stop() 전까지 계속 실행")] = None,
    observe: Annotated[float, Field(description="시작 후 런타임 오류를 관찰하는 시간(초)")] = 1.5,
    headless: Annotated[bool, Field(description="렌더링 없이 실행 (screenshot 불가)")] = False,
) -> dict:
    """실제 Godot 게임을 실행하고(가상 디스플레이) 시작 직후 런타임 오류를 수집해 반환한다. 결과: process, scene, running, exit_code, runtime_errors, duration.
    실행 중에는 game_state/screenshot/play/errors 로 관찰할 수 있다."""
    return await _call("run", lambda: api.run(project, scene=scene, timeout=timeout, observe=observe, headless=headless),
                       dict(scene=scene, timeout=timeout), project=project, mutating=True, timeout=120)


@mcp.tool(annotations=WRITE)
async def stop(
    project: Project = None,
    editor: Annotated[bool, Field(description="Godot 에디터 프로세스도 함께 종료")] = False,
) -> dict:
    """실행 중인 게임을 종료한다 (editor=true 면 에디터도 종료)."""
    return await _call("stop", lambda: api.stop(project, editor=editor), dict(editor=editor), project=project, mutating=True, timeout=60)


@mcp.tool(annotations=WRITE)
async def play(
    actions: Annotated[list[Any], Field(description="입력 시퀀스. 예: ['jump', {'action':'move_right','hold_ms':600}, {'action':'jump','hold_ms':80,'wait_ms':200}]")],
    project: Project = None,
) -> dict:
    """실행 중인 게임에 입력(InputMap 액션)을 주입하고, 전/후 엔티티 상태·이벤트·런타임 오류를 비교해서 반환한다 (게임플레이 검증용)."""
    return await _call("play", lambda: api.play(actions, project), dict(actions=actions), project=project, mutating=True, timeout=120)


@mcp.tool(annotations=DESTRUCTIVE)
async def fix(
    project: Project = None,
    scope: Annotated[str, Field(description="errors(기본: 전부) | syntax | runtime | validate | all")] = "errors",
    dry_run: Annotated[bool, Field(description="적용하지 않고 수정 계획만 반환")] = False,
) -> dict:
    """현재 오류를 분석해서 직접 수정한다: 오류 탐지 → 소스/씬 분석 → 원인 추론 → 수정 → Godot 리로드 → syntax_check/validate 재검사 반복.
    자동 수정 가능: 콜론/괄호/따옴표 누락, 들여쓰기, 빈 블록, else-if, 오타 난 변수·함수·속성, 누락된 입력 액션/메인 씬/옮겨진 리소스 경로 등.
    고칠 수 없는 오류는 unfixable 에 원인(diagnosis)과 소스 컨텍스트와 함께 반환되므로 modify_game 으로 이어서 수정하세요."""
    return await _call("fix", lambda: api.fix(project, scope=scope, dry_run=dry_run), dict(scope=scope, dry_run=dry_run), project=project, mutating=True, timeout=420)


@mcp.tool(annotations=WRITE)
async def build(
    project: Project = None,
    target: Annotated[str, Field(description="linux(기본) | pck  (Ubuntu/POSIX 우선, Windows/macOS 미지원)")] = "linux",
    mode: Annotated[str, Field(description="release | debug")] = "release",
    force: Annotated[bool, Field(description="문법 오류가 있어도 빌드 시도")] = False,
) -> dict:
    """프로젝트를 export 한다 (export preset 자동 생성, 문법 검사 게이트, 산출물 실행 검증). 결과: success, target, output, exit_code, errors, warnings, duration."""
    return await _call("build", lambda: api.build(project, target=target, mode=mode, force=force), dict(target=target, mode=mode), project=project, mutating=True, timeout=600)


# ------------------------------------------------------------------- VIEW --

@mcp.tool(annotations=READ)
async def syntax_check(
    project: Project = None,
    files: Annotated[Optional[list[str]], Field(description="검사할 GDScript 파일(이름 또는 경로). 생략하면 전체")] = None,
    changed: Annotated[bool, Field(description="마지막 검사 이후 바뀐 파일만 검사")] = False,
) -> dict:
    """GDScript 문법/파싱/컴파일 오류를 Godot 의 실제 파서로 빠르게(게임 실행 없이) 검사한다. 오류마다 file/line/column/message/context 반환."""
    return await _call("syntax_check", lambda: api.syntax_check(project, files=files, changed=changed), dict(files=files, changed=changed), timeout=180)


@mcp.tool(annotations=READ)
async def validate(
    project: Project = None,
    engine: Annotated[bool, Field(description="Godot 엔진으로 씬/리소스 실제 로드 검사도 수행")] = True,
) -> dict:
    """프로젝트 전체 구조를 정적 검증한다: 스크립트/씬/리소스 참조, 노드 경로, 오토로드, 입력 액션, 프로젝트 설정, 누락 에셋, 깨진 리소스, 중복 설정.
    (문법은 syntax_check 가 담당)"""
    return await _call("validate", lambda: api.validate(project, engine=engine), dict(engine=engine), timeout=180)


@mcp.tool(annotations=READ)
async def inspect(
    project: Project = None,
    scene: Annotated[Optional[str], Field(description="조사할 씬(이름 또는 경로). 생략하면 메인 씬")] = None,
    depth: Annotated[int, Field(description="노드 트리 깊이")] = 6,
    props: Annotated[bool, Field(description="기본값과 다른 노드 속성 포함")] = True,
    format: Annotated[str, Field(description="json | yaml")] = "json",
) -> Any:
    """씬의 구조화된 상태를 반환한다: project/scene/nodes(타입·스크립트·그룹·속성)/errors/warnings."""
    return await _call("inspect", lambda: api.inspect(project, scene=scene, depth=depth, props=props, format=format), dict(scene=scene), timeout=120)


@mcp.tool(annotations=READ)
async def project_state(
    project: Project = None,
    format: Annotated[str, Field(description="json | yaml")] = "json",
) -> Any:
    """프로젝트 전체 개요: 장르, 적용된/추가 가능한 메카닉, 씬/스크립트 목록, 오토로드, 입력 액션, 에디터/게임 프로세스 상태, 마지막 검사 결과."""
    return await _call("project_state", lambda: api.project_state(project, format=format), dict(), timeout=60)


@mcp.tool(annotations=READ)
async def errors(
    project: Project = None,
    scope: Annotated[str, Field(description="all | runtime | script | editor | static")] = "all",
    limit: Annotated[int, Field(description="반환할 최대 오류 수")] = 20,
) -> dict:
    """수집된 오류를 계층화해서 반환한다 (summary → errors → warnings). 원본 로그 대신 type/severity/file/line/column/scene/node/message/stack/source/context 를 제공."""
    return await _call("errors", lambda: api.errors(project, scope=scope, limit=limit), dict(scope=scope, limit=limit), timeout=60)


@mcp.tool(annotations=READ)
async def game_state(
    project: Project = None,
    depth: Annotated[int, Field(description="씬 트리 요약 깊이")] = 3,
) -> dict:
    """실행 중인 게임의 상태: running/paused/scene/fps/플레이어·적 등 엔티티(위치, 속도, 변수, 컴포넌트)/오토로드 상태/최근 게임 이벤트/오류 수."""
    return await _call("game_state", lambda: api.game_state(project, depth=depth), dict(depth=depth), timeout=60)


@mcp.tool(annotations=WRITE)
async def screenshot(
    project: Project = None,
    target: Annotated[str, Field(description="game(기본) | editor")] = "game",
    max_width: Annotated[int, Field(description="최대 가로 픽셀")] = 960,
    auto_run: Annotated[bool, Field(description="게임이 실행 중이 아니면 자동으로 실행")] = True,
) -> list:
    """게임(또는 에디터) 화면을 캡처해 이미지로 반환한다. 게임의 시각적 결과를 직접 평가할 수 있다."""
    res = await _call("screenshot", lambda: api.screenshot(project, target=target, max_width=max_width, auto_run=auto_run), dict(target=target), project=project, mutating=True, timeout=90)
    if res.get("success") and "png" in res:
        png = res.pop("png")
        return [json.dumps({k: v for k, v in res.items()}, ensure_ascii=False), Image(data=png, format="png")]
    res.pop("png", None)
    return [json.dumps(res, ensure_ascii=False)]


@mcp.tool(annotations=WRITE)
async def test(
    project: Project = None,
    scene: Annotated[Optional[str], Field(description="이 씬을 대상으로 실행 테스트")] = None,
    all: Annotated[bool, Field(description="게임플레이 테스트까지 모두 수행")] = False,
    gameplay: Annotated[bool, Field(description="입력 주입으로 이동/점프 등 실제 게임플레이 검증")] = False,
) -> dict:
    """프로젝트를 실제로 검증한다: syntax_check → validate → 실행(launch) → 런타임 관찰(fps/frames) → 시각 검사(빈 화면 감지) → (gameplay) 입력 주입 검증 → 런타임 오류 확인.
    각 단계의 성공 여부와 실패 원인이 steps 에 담긴다."""
    return await _call("test", lambda: api.test(project, scene=scene, all=all, gameplay=gameplay), dict(scene=scene, all=all, gameplay=gameplay), project=project, mutating=True, timeout=300)


def main() -> None:
    import argparse
    ap = argparse.ArgumentParser(prog="godot-mcp-server", description="Godot MCP 서버")
    ap.add_argument("--transport", choices=["stdio", "http", "sse"], default="stdio")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--token"); ap.add_argument("--certfile"); ap.add_argument("--keyfile"); ap.add_argument("--path", default="/mcp")
    a = ap.parse_args()
    if a.transport == "stdio":
        mcp.run()
    else:
        from . import remote
        remote.serve(a.host, a.port, a.token, a.certfile, a.keyfile, a.transport, a.path)


if __name__ == "__main__":
    main()
