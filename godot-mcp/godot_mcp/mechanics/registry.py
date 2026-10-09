"""add_mechanic 레지스트리: 자연어에 가까운 기능명 -> 실제 Godot 구조(스크립트+씬+입력+HUD) 구성."""
from __future__ import annotations
import difflib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Awaitable, Callable

from .. import security
from ..errors import McpError
from ..runtime import editor as editor_rt
from . import gdscripts as G
from . import scenes as SC
from . import specs as S

COMP_DIR = "scripts/mechanics"


@dataclass
class Ctx:
    project: Path
    genre: str
    layout: dict
    options: dict
    files: list[str] = field(default_factory=list)
    scenes: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    inputs: list[str] = field(default_factory=list)

    def write(self, rel: str, content: str, *, overwrite: bool = True) -> bool:
        p = security.resolve(self.project, rel, write=True)
        p.parent.mkdir(parents=True, exist_ok=True)
        if p.exists():
            if p.read_text() == content:
                return False
            if not overwrite:
                return False
        p.write_text(content)
        self.files.append(f"res://{p.relative_to(self.project).as_posix()}")
        return True

    async def edit(self, cmd: str, args: dict | None = None, timeout: float = 60) -> dict:
        return await editor_rt.editor_call(self.project, cmd, args or {}, timeout)

    dirty: bool = False

    async def scan(self) -> None:
        """파일시스템 스캔은 비용이 크고(에디터 progress task 충돌 위험) 스크립트/씬은 load() 로 바로 읽히므로 모아서 한 번만 수행한다."""
        self.dirty = True

    async def flush_scan(self) -> None:
        if self.dirty:
            self.dirty = False
            await self.edit("scan", timeout=90)

    async def ensure_scene(self, res_path: str, spec: dict) -> bool:
        """씬이 없을 때만 생성."""
        if (self.project / res_path[6:]).exists():
            return False
        r = await self.edit("create_scene", {"path": res_path, "root": spec})
        self.scenes.append(res_path)
        self.warnings += r.get("warnings", [])
        return True

    async def patch(self, scene: str, ops: list[dict]) -> dict:
        r = await self.edit("patch_scene", {"path": scene, "ops": ops})
        if scene not in self.scenes:
            self.scenes.append(scene)
        self.warnings += r.get("warnings", [])
        return r

    async def add_inputs(self, names: list[str]) -> None:
        acts = {n: SC.DEFAULT_INPUTS[n] for n in names}
        r = await self.edit("add_input_actions", {"actions": acts})
        self.inputs += r.get("added", [])


@dataclass
class Mechanic:
    key: str
    title: str
    summary: str
    aliases: list[str]
    genres: set[str]
    requires: list[str]
    apply: Callable[[Ctx], Awaitable[None]]


_REG: dict[str, Mechanic] = {}


def register(key, title, summary, aliases, genres, requires=()):
    def deco(fn):
        _REG[key] = Mechanic(key, title, summary, [a.lower() for a in aliases], set(genres), list(requires), fn)
        return fn
    return deco


def norm(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[_\-]+", " ", text.lower())).strip()


def resolve(text: str) -> tuple[Mechanic, dict]:
    """자연어 -> (Mechanic, 추출된 옵션)."""
    t = norm(text)
    opts: dict = {}
    m = re.search(r"\b(triple|quadruple)\s+jump", t)
    if m:
        opts["extra_jumps"] = {"triple": 2, "quadruple": 3}[m.group(1)]
        t = "double jump"
    m = re.search(r"(\d+)\s*(?:x\s*)?(coins?|enem(?:y|ies)|hearts?|hp|health|lives)", t)
    if m:
        n = int(m.group(1))
        kind = m.group(2)
        if kind.startswith("coin"):
            opts["count"] = n
        elif kind.startswith("enem"):
            opts["count"] = n
        else:
            opts["max_health"] = n
    for mech in _REG.values():
        for a in [mech.key.replace("_", " ")] + mech.aliases:
            if t == a:
                return mech, opts
    for mech in _REG.values():
        for a in [mech.key.replace("_", " ")] + mech.aliases:
            if re.search(rf"\b{re.escape(a)}\b", t):
                return mech, opts
    names = [m.key.replace("_", " ") for m in _REG.values()] + [a for m in _REG.values() for a in m.aliases]
    close = difflib.get_close_matches(t, names, n=3, cutoff=0.55)
    raise McpError("UNSUPPORTED_MECHANIC", f"지원하지 않는 메카닉입니다: {text!r}",
                   supported=[{"name": m.key.replace("_", " "), "summary": m.summary, "genres": sorted(m.genres)} for m in _REG.values()],
                   did_you_mean=close,
                   hint="지원되지 않는 기능은 modify_game(files=...) 로 직접 GDScript 를 작성하세요.")


def supported() -> list[dict]:
    return [{"name": m.key.replace("_", " "), "summary": m.summary, "genres": sorted(m.genres), "requires": m.requires, "aliases": m.aliases} for m in _REG.values()]


def player_contract(ctx: Ctx, needs: list[str]) -> None:
    p = ctx.project / "scripts" / "player.gd"
    if not p.exists():
        raise McpError("PLAYER_MISSING", "scripts/player.gd 가 없습니다. 먼저 add_mechanic('player movement') 를 적용하세요.")
    src = p.read_text()
    missing = [n for n in needs if not re.search(rf"^\s*(?:@export\s+)?var\s+{n}\b", src, re.M)]
    if missing:
        raise McpError("PLAYER_CONTRACT", f"player.gd 에 이 메카닉이 요구하는 변수가 없습니다: {missing}",
                       hint="player.gd 에 해당 변수를 추가하거나 add_mechanic('player movement') 로 기본 스크립트를 복원하세요.", missing=missing)


# ---------------------------------------------------------------- mechanics --

async def _ensure_hud_label(ctx: Ctx, name: str, text: str) -> None:
    await ctx.patch(SC.HUD_SCENE, [{"op": "add", "parent": "Stats", "node": S.hud_label(name, text)}])


async def _ensure_in_main(ctx: Ctx, node_spec: dict) -> None:
    await ctx.patch(SC.MAIN_SCENE, [{"op": "add", "parent": ".", "node": node_spec}])


@register("player_movement", "Player Movement", "플레이어 이동(플랫포머: 좌우+점프, 탑다운: 8방향, 3D: WASD+점프)과 기본 씬/입력/HUD",
          ["movement", "player", "player controller", "character controller", "move", "walk", "character movement", "controls"], {"platformer", "topdown", "3d"})
async def _player_movement(ctx: Ctx) -> None:
    g = ctx.genre
    src = {"platformer": G.PLAYER_PLATFORMER, "topdown": G.PLAYER_TOPDOWN, "3d": G.PLAYER_3D}[g]
    # 1) GameState 오토로드를 먼저 등록한다 (이후 스크립트가 컴파일될 때 전역 식별자로 보이도록)
    ctx.write("scripts/game_state.gd", G.GAME_STATE, overwrite=False)
    await ctx.scan()
    await ctx.edit("set_autoloads", {"autoloads": {"GameState": "res://scripts/game_state.gd"}})
    await ctx.scan()
    # 2) 나머지 스크립트
    existing = ctx.project / "scripts/player.gd"
    if not existing.exists() or ctx.options.get("reset"):
        ctx.write("scripts/player.gd", src)
    else:
        ctx.notes.append("scripts/player.gd 가 이미 있어 유지했습니다 (reset=true 로 기본 스크립트 복원 가능)")
    ctx.write("scripts/hud.gd", G.HUD, overwrite=False)
    ctx.write("scripts/main.gd", G.MAIN_3D if g == "3d" else G.MAIN, overwrite=False)
    await ctx.scan()
    await ctx.add_inputs(list(SC.inputs_for(g)))
    await ctx.ensure_scene(SC.PLAYER_SCENE, SC.player_scene(g))
    await ctx.ensure_scene(SC.HUD_SCENE, SC.hud_scene(g))
    await ctx.ensure_scene(SC.MAIN_SCENE, SC.main_scene(g, ctx.layout))


@register("double_jump", "Double Jump", "공중에서 추가 점프 (extra_jumps 설정 가능: 'triple jump' = 2)",
          ["double jumping", "air jump", "extra jump", "multi jump", "doublejump"], {"platformer"}, ["player_movement"])
async def _double_jump(ctx: Ctx) -> None:
    player_contract(ctx, ["input_locked", "last_jump_frame"])
    ctx.write(f"{COMP_DIR}/double_jump.gd", G.DOUBLE_JUMP)
    await ctx.scan()
    node = {"name": "DoubleJump", "type": "Node", "script": f"res://{COMP_DIR}/double_jump.gd"}
    if "extra_jumps" in ctx.options:
        node["props"] = {"extra_jumps": int(ctx.options["extra_jumps"])}
    await ctx.patch(SC.PLAYER_SCENE, [{"op": "add", "parent": ".", "node": node}])


@register("dash", "Dash", "대시(Shift/K, 쿨다운 포함)", ["dodge", "dash ability", "sprint burst", "roll"], {"platformer", "topdown"}, ["player_movement"])
async def _dash(ctx: Ctx) -> None:
    player_contract(ctx, ["input_locked", "facing"] + (["gravity_enabled"] if ctx.genre == "platformer" else []))
    await ctx.add_inputs(["dash"])
    ctx.write(f"{COMP_DIR}/dash.gd", G.DASH_PLATFORMER if ctx.genre == "platformer" else G.DASH_TOPDOWN)
    await ctx.scan()
    await ctx.patch(SC.PLAYER_SCENE, [{"op": "add", "parent": ".", "node": {"name": "Dash", "type": "Node", "script": f"res://{COMP_DIR}/dash.gd"}}])


@register("health_system", "Health System", "체력(Health 컴포넌트, 무적시간, 사망 시 재시작) + HUD 표시",
          ["health", "hp", "lives", "life system", "hit points", "player health", "damage system"], {"platformer", "topdown", "3d"}, ["player_movement"])
async def _health(ctx: Ctx) -> None:
    ctx.write(f"{COMP_DIR}/health.gd", G.HEALTH)
    await ctx.scan()
    node = {"name": "Health", "type": "Node", "script": f"res://{COMP_DIR}/health.gd"}
    if "max_health" in ctx.options:
        node["props"] = {"max_health": int(ctx.options["max_health"])}
    await ctx.patch(SC.PLAYER_SCENE, [{"op": "add", "parent": ".", "node": node}])
    await _ensure_hud_label(ctx, "HealthLabel", "HP")


@register("coin_collection", "Coin Collection", "코인 아이템 배치/수집, 점수, HUD 카운터", ["coin", "coins", "collectibles", "collectible", "pickups", "coin collecting", "coin pickup"],
          {"platformer", "topdown"}, ["player_movement"])
async def _coins(ctx: Ctx) -> None:
    ctx.write("scripts/coin.gd", G.COIN)
    await ctx.scan()
    await ctx.ensure_scene(SC.COIN_SCENE, SC.coin_scene())
    n = int(ctx.options.get("count", 10))
    pos = S.coin_positions(ctx.layout, n)
    ops = [{"op": "add", "parent": ".", "node": {"name": "Coins", "type": "Node2D"}}]
    ops += [{"op": "add", "parent": "Coins", "node": {"name": f"Coin{i + 1}", "instance": SC.COIN_SCENE, "props": {"position": p}}} for i, p in enumerate(pos)]
    await ctx.patch(SC.MAIN_SCENE, ops)
    await _ensure_hud_label(ctx, "CoinLabel", "Coins: 0")


@register("enemy_ai", "Enemy AI", "순찰/추적 적 (접촉 피해, 플랫포머는 밟아서 처치) — 체력 시스템 필요",
          ["enemy", "enemies", "ai enemy", "enemy ai behavior", "patrol enemy", "chase enemy", "monsters", "enemy patrol"], {"platformer", "topdown"}, ["player_movement", "health_system"])
async def _enemy(ctx: Ctx) -> None:
    ctx.write("scripts/enemy.gd", G.ENEMY_PLATFORMER if ctx.genre == "platformer" else G.ENEMY_TOPDOWN)
    await ctx.scan()
    await ctx.ensure_scene(SC.ENEMY_SCENE, SC.enemy_scene(ctx.genre))
    n = int(ctx.options.get("count", 4 if ctx.genre == "platformer" else 3))
    pos = S.enemy_positions(ctx.layout, n)
    ops = [{"op": "add", "parent": ".", "node": {"name": "Enemies", "type": "Node2D"}}]
    ops += [{"op": "add", "parent": "Enemies", "node": {"name": f"Enemy{i + 1}", "instance": SC.ENEMY_SCENE, "props": {"position": p}}} for i, p in enumerate(pos)]
    await ctx.patch(SC.MAIN_SCENE, ops)


@register("inventory", "Inventory", "인벤토리 컴포넌트(아이템 id->수량) + HUD 표시, 코인 수집 시 자동 연동", ["items", "inventory system", "backpack", "item system"],
          {"platformer", "topdown", "3d"}, ["player_movement"])
async def _inventory(ctx: Ctx) -> None:
    ctx.write(f"{COMP_DIR}/inventory.gd", G.INVENTORY)
    await ctx.scan()
    await ctx.patch(SC.PLAYER_SCENE, [{"op": "add", "parent": ".", "node": {"name": "Inventory", "type": "Node", "script": f"res://{COMP_DIR}/inventory.gd"}}])
    await _ensure_hud_label(ctx, "ItemsLabel", "Items: -")


@register("pause_menu", "Pause Menu", "일시정지 메뉴(ESC/P, Resume/Quit)", ["pause", "pause screen", "pause game", "pausing"], {"platformer", "topdown", "3d"}, ["player_movement"])
async def _pause(ctx: Ctx) -> None:
    ctx.write("scripts/pause_menu.gd", G.PAUSE_MENU)
    await ctx.add_inputs(["pause"])
    await ctx.scan()
    await ctx.ensure_scene(SC.PAUSE_SCENE, SC.pause_scene())
    await ctx.patch(SC.MAIN_SCENE, [{"op": "add", "parent": ".", "node": {"name": "PauseMenu", "instance": SC.PAUSE_SCENE}}])


@register("main_menu", "Main Menu", "타이틀 메뉴 씬(Start/Quit) — 프로젝트 메인 씬으로 지정", ["title screen", "menu", "start menu", "title menu"], {"platformer", "topdown", "3d"})
async def _menu(ctx: Ctx) -> None:
    ctx.write("scripts/menu.gd", G.MENU)
    await ctx.scan()
    title = str(ctx.options.get("title", "Game"))
    await ctx.ensure_scene("res://scenes/menu.tscn", SC.menu_scene(title))
    await ctx.edit("set_settings", {"settings": {"application/run/main_scene": "res://scenes/menu.tscn"}})
    ctx.notes.append("메인 씬이 res://scenes/menu.tscn 으로 변경되었습니다 (Start -> res://scenes/main.tscn)")


def order(keys: list[str]) -> list[str]:
    """의존성 순서 정렬 (requires 우선)."""
    out: list[str] = []

    def visit(k: str):
        if k in out:
            return
        for r in _REG[k].requires:
            visit(r)
        out.append(k)

    for k in keys:
        visit(k)
    return out


def get(key: str) -> Mechanic:
    return _REG[key]
