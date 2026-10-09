"""실제 MCP 프로토콜(stdio) 로 Godot MCP 를 호출하는 End-to-End 자기 테스트 (지시서 29장 시나리오).

사용: python tests/e2e_mcp.py [작업공간]
요구: Godot 4.x, Xvfb, (build 단계) export templates.
"""
import asyncio
import json
import os
import shutil
import sys
import time
from pathlib import Path

from fastmcp import Client
from fastmcp.client.transports import StdioTransport

WORK = Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/gmcp_e2e").resolve()
RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, cond: bool, detail: str = "") -> bool:
    RESULTS.append((name, bool(cond), detail))
    print(("  PASS " if cond else "  FAIL ") + name + (f"  — {detail}" if detail and not cond else ""))
    return bool(cond)


async def call(c: Client, tool: str, **args):
    t0 = time.monotonic()
    r = await c.call_tool(tool, {k: v for k, v in args.items() if v is not None}, raise_on_error=False)
    data = r.structured_content
    if data is None:
        texts = [b.text for b in r.content if getattr(b, "text", None)]
        data = json.loads(texts[0]) if texts else {}
    if isinstance(data, dict) and "result" in data and len(data) == 1:
        data = data["result"]
    print(f"[{tool}] {time.monotonic() - t0:.1f}s success={data.get('success') if isinstance(data, dict) else '-'}")
    return data, r


async def main() -> int:
    shutil.rmtree(WORK, ignore_errors=True)
    WORK.mkdir(parents=True)
    env = {**os.environ, "GODOT_MCP_WORKSPACE": str(WORK), "GODOT_MCP_STATE_DIR": str(WORK / ".state")}
    transport = StdioTransport(command=sys.executable, args=["-m", "godot_mcp.server"], env=env)
    async with Client(transport, timeout=600) as c:
        tools = {t.name for t in await c.list_tools()}
        check("MCP: 필수 도구 노출", {"create_project", "create_game", "create_scene", "add_mechanic", "modify_game", "syntax_check", "validate", "run", "stop", "inspect",
                                      "screenshot", "game_state", "project_state", "errors", "test", "fix", "build"} <= tools, str(tools))
        check("MCP: 저수준 도구 미노출", not ({"create_node", "set_property", "attach_script", "move_node", "set_position"} & tools))

        print("\n== 1. create_project / create_game / create_scene")
        d, _ = await call(c, "create_project", name="Scratch", type="2d")
        check("create_project", d.get("success") and (WORK / "Scratch/project.godot").exists(), json.dumps(d)[:300])
        d, _ = await call(c, "create_game", name="Platformer", type="2d", description="A simple platform game")
        proj = d.get("project", "")
        check("create_game (실행 가능 구조)", d.get("success") and d.get("genre") == "platformer", json.dumps(d.get("checks"))[:400])
        d, _ = await call(c, "create_scene", name="Main Menu", project=proj)
        check("create_scene (menu)", d.get("success") and (Path(proj) / "scenes/main_menu.tscn").exists(), json.dumps(d)[:300])

        print("\n== 2. add_mechanic")
        for m in ("player movement", "double jump"):
            d, _ = await call(c, "add_mechanic", mechanic=m, project=proj)
            check(f"add_mechanic: {m}", d.get("success"), json.dumps(d.get("checks"))[:300])
        d, _ = await call(c, "add_mechanic", mechanic="time travel", project=proj)
        check("add_mechanic: 미지원 → 구조화 오류", d.get("success") is False and d["error"]["code"] == "UNSUPPORTED_MECHANIC" and d["error"].get("supported"))
        for m in ("health system", "coin collection", "enemy AI"):
            d, _ = await call(c, "add_mechanic", mechanic=m, project=proj)
            check(f"add_mechanic: {m}", d.get("success"), json.dumps(d.get("checks"))[:300])

        print("\n== 3. syntax_check / validate")
        d, _ = await call(c, "syntax_check", project=proj)
        check("syntax_check 통과", d.get("success") and d["checked_files"] >= 8, json.dumps(d)[:300])
        d, _ = await call(c, "validate", project=proj)
        check("validate 통과", d.get("success"), json.dumps(d.get("errors"))[:300])

        print("\n== 4. run / inspect / game_state / screenshot / errors")
        d, _ = await call(c, "run", project=proj)
        check("run: 실행 중 + 오류 없음", d.get("running") and d.get("bridge") and not d.get("runtime_errors"), json.dumps(d)[:400])
        d, _ = await call(c, "inspect", project=proj, scene="main", depth=2, props=False)
        names = {n["name"] for n in d.get("nodes", [])}
        check("inspect: Player/HUD/Coins/Enemies", {"Player", "HUD", "Coins", "Enemies"} <= names, str(names))
        d, _ = await call(c, "game_state", project=proj)
        pl = [e for e in d.get("entities", []) if e["group"] == "player"]
        check("game_state: 플레이어/적 엔티티", pl and any(e["group"] == "enemy" for e in d["entities"]) and d.get("fps", 0) > 0, json.dumps(d)[:300])
        check("game_state: DoubleJump 컴포넌트 관찰", pl and "DoubleJump" in pl[0].get("components", {}))
        d, r = await call(c, "screenshot", project=proj)
        imgs = [b for b in r.content if b.type == "image"]
        check("screenshot: 이미지 반환", len(imgs) == 1 and len(imgs[0].data) > 2000)
        d, _ = await call(c, "play", actions=[{"action": "move_right", "hold_ms": 500}], project=proj)
        b, a = d.get("entities_before", [{}])[0], d.get("entities_after", [{}])[0]
        check("play: 입력으로 플레이어 이동", a.get("position", [0])[0] > b.get("position", [0])[0] + 10, json.dumps(d)[:300])
        d, _ = await call(c, "errors", project=proj)
        check("errors: 정상 상태 0건", d.get("count") == 0, json.dumps(d)[:300])
        await call(c, "stop", project=proj)

        print("\n== 5. 의도적 문법 오류 → syntax_check → fix → 재검사")
        bad = Path(proj) / "scripts/broken.gd"
        bad.write_text("extends Node\nvar speed = 10\nfunc _ready():\n\tif speed > 5\n\t\tprint(\"fast\")\n\tprint(speedd)\n")
        d, _ = await call(c, "syntax_check", project=proj)
        errs = d.get("errors", [])
        check("syntax_check: 실제 오류 검출(콜론 누락, file/line)", d.get("success") is False and any(e["file"] == "res://scripts/broken.gd" and e["line"] == 4 for e in errs), json.dumps(errs)[:400])
        d, _ = await call(c, "fix", project=proj, scope="syntax")
        check("fix: 오류 수정 + 변경 내역", d.get("success") and d["fixed"] >= 2 and d["remaining_errors"] == 0 and d["changes"], json.dumps(d)[:500])
        src = bad.read_text()
        check("fix: 실제 파일이 수정됨", "if speed > 5:" in src and "speedd" not in src, src)
        d, _ = await call(c, "syntax_check", project=proj)
        check("fix 후 syntax_check 통과", d.get("success"))

        print("\n== 6. 의도적 런타임 오류 → errors")
        rt = Path(proj) / "scripts/runtime_bug.gd"
        d, _ = await call(c, "modify_game", project=proj, files={"res://scripts/runtime_bug.gd":
            "extends Node\nvar data: Node\nfunc _ready():\n\tdata = get_node(\"EnemySpawner\")\n\tprint(data.name)\n"},
            attach={"res://scenes/main.tscn:Main/..": "res://scripts/runtime_bug.gd"} if False else None)
        check("modify_game: 파일 작성 + 즉시 문법검사", d.get("success") and d["syntax"]["checked_files"] == 1, json.dumps(d)[:300])
        d, _ = await call(c, "modify_game", project=proj, set={"res://scenes/main.tscn:Main.script": "res://scripts/runtime_bug.gd"})
        d, _ = await call(c, "run", project=proj, observe=2)
        errs = d.get("runtime_errors", [])
        check("run: 런타임 오류 수집", errs and any("EnemySpawner" in e["message"] for e in errs), json.dumps(d)[:500])
        d, _ = await call(c, "errors", project=proj, scope="runtime")
        e0 = d.get("errors", [{}])[0]
        check("errors: file/line/node/scene/context 포함", d.get("count", 0) >= 1 and e0.get("file") and e0.get("line") and e0.get("context"), json.dumps(d)[:500])
        await call(c, "stop", project=proj)
        # 되돌리기: 원래 main.gd 로 복구
        d, _ = await call(c, "modify_game", project=proj, set={"res://scenes/main.tscn:Main.script": "res://scripts/main.gd"}, delete=["res://scripts/runtime_bug.gd"])
        check("modify_game: 복구", d.get("success"), json.dumps(d)[:300])

        print("\n== 7. 경로 탈출/보안")
        d, _ = await call(c, "modify_game", project=proj, files={"res://../../evil.gd": "extends Node"})
        check("경로 탈출 차단 (../)", d.get("success") is False and d["error"]["code"] == "PATH_ESCAPE")
        d, _ = await call(c, "modify_game", project=proj, files={"/etc/cron.d/x.gd": "extends Node"})
        check("절대 경로 탈출 차단", d.get("success") is False and d["error"]["code"] in ("PATH_ESCAPE", "PATH_PROTECTED"))
        d, _ = await call(c, "validate", project="/etc")
        check("허용되지 않은 프로젝트 차단", d.get("success") is False and d["error"]["code"] in ("PATH_NOT_ALLOWED",))

        print("\n== 8. test / build")
        d, _ = await call(c, "test", project=proj, all=True)
        check("test --all 통과", d.get("success"), json.dumps(d)[:700])
        d, _ = await call(c, "build", project=proj)
        check("build 성공 + 산출물 실행 검증", d.get("success") and d.get("output") and Path(d["output"]).exists() and d.get("verify", {}).get("launched"), json.dumps(d)[:600])
        await call(c, "stop", project=proj, editor=True)

    fails = [r for r in RESULTS if not r[1]]
    print(f"\n{len(RESULTS) - len(fails)}/{len(RESULTS)} 통과")
    for n, _, dt in fails:
        print("  FAIL:", n, dt[:300])
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
