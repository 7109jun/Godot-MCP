"""장르별 기본 씬 스펙 (player/hud/main/pause_menu/enemy/coin)."""
from __future__ import annotations

from . import specs as S

PLAYER_SCENE = "res://scenes/player.tscn"
HUD_SCENE = "res://scenes/hud.tscn"
MAIN_SCENE = "res://scenes/main.tscn"
ENEMY_SCENE = "res://scenes/enemy.tscn"
COIN_SCENE = "res://scenes/coin.tscn"
PAUSE_SCENE = "res://scenes/pause_menu.tscn"
PLAYER_SCRIPT = "res://scripts/player.gd"

DEFAULT_INPUTS = {
    "move_left": {"events": [{"type": "key", "key": "A"}, {"type": "key", "key": "Left"}]},
    "move_right": {"events": [{"type": "key", "key": "D"}, {"type": "key", "key": "Right"}]},
    "move_up": {"events": [{"type": "key", "key": "W"}, {"type": "key", "key": "Up"}]},
    "move_down": {"events": [{"type": "key", "key": "S"}, {"type": "key", "key": "Down"}]},
    "jump": {"events": [{"type": "key", "key": "Space"}, {"type": "key", "key": "W"}, {"type": "key", "key": "Up"}]},
    "dash": {"events": [{"type": "key", "key": "Shift"}, {"type": "key", "key": "K"}]},
    "pause": {"events": [{"type": "key", "key": "Escape"}, {"type": "key", "key": "P"}]},
    "interact": {"events": [{"type": "key", "key": "E"}]},
    "attack": {"events": [{"type": "key", "key": "J"}, {"type": "mouse_button", "index": 1}]},
}


def inputs_for(genre: str) -> dict:
    if genre == "platformer":
        names = ["move_left", "move_right", "jump"]
    elif genre == "topdown":
        names = ["move_left", "move_right", "move_up", "move_down"]
    else:
        names = ["move_left", "move_right", "move_up", "move_down", "jump"]
    out = {n: DEFAULT_INPUTS[n] for n in names}
    if genre == "3d":
        out["jump"] = {"events": [{"type": "key", "key": "Space"}]}
    if genre == "platformer":
        out["jump"] = {"events": [{"type": "key", "key": "Space"}, {"type": "key", "key": "W"}, {"type": "key", "key": "Up"}]}
    return out


def player_scene(genre: str) -> dict:
    if genre == "platformer":
        props = {"collision_layer": 2, "collision_mask": 1}
        cam = {"name": "Camera", "type": "Camera2D", "props": {"position_smoothing_enabled": True, "limit_left": 0, "limit_right": S.LEVEL_W, "limit_top": 0, "limit_bottom": 720}}
        script = PLAYER_SCRIPT
        body = [S.collision(28, 44), S.visual(28, 44, S.COLORS["player"])]
    elif genre == "topdown":
        props = {"collision_layer": 2, "collision_mask": 1, "motion_mode": 1}
        cam = {"name": "Camera", "type": "Camera2D", "props": {"limit_left": 0, "limit_right": 1280, "limit_top": 0, "limit_bottom": 720, "anchor_mode": 1}}
        script = PLAYER_SCRIPT
        body = [S.collision(26, 26), S.visual(26, 26, S.COLORS["player"])]
    else:  # 3d
        shape = {"_t": "Resource", "type": "CapsuleShape3D", "props": {"radius": 0.4, "height": 1.8}}
        mesh = {"_t": "Resource", "type": "CapsuleMesh", "props": {"radius": 0.4, "height": 1.8}}
        mat = {"_t": "Resource", "type": "StandardMaterial3D", "props": {"albedo_color": S.COLORS["player"]}}
        return {"name": "Player", "type": "CharacterBody3D", "script": PLAYER_SCRIPT, "groups": ["player"], "props": {"collision_layer": 2, "collision_mask": 1},
                "children": [{"name": "CollisionShape3D", "type": "CollisionShape3D", "props": {"shape": shape}},
                             {"name": "Mesh", "type": "MeshInstance3D", "props": {"mesh": mesh, "material_override": mat}},
                             {"name": "Camera", "type": "Camera3D", "props": {"position": [0, 4.5, 7], "rotation_degrees": [-25, 0, 0], "current": True}}]}
    return {"name": "Player", "type": "CharacterBody2D", "script": script, "groups": ["player"], "props": props, "children": body + [cam]}


def hud_scene(genre: str) -> dict:
    hint = {"platformer": "A/D or ←/→: move   Space: jump", "topdown": "WASD / arrows: move", "3d": "WASD: move   Space: jump"}[genre]
    return {"name": "HUD", "type": "CanvasLayer", "script": "res://scripts/hud.gd", "props": {"layer": 10},
            "children": [{"name": "Stats", "type": "VBoxContainer", "props": {"position": [16, 12]},
                          "children": [S.hud_label("InfoLabel", hint)]}]}


def main_scene(genre: str, layout: dict) -> dict:
    children: list[dict] = []
    if genre != "3d":
        children.append({"name": "Background", "type": "CanvasLayer", "props": {"layer": -1},
                         "children": [{"name": "Fill", "type": "ColorRect", "props": {"size": [1280, 720], "color": S.COLORS["bg"], "mouse_filter": 2}}]})
    if genre == "platformer":
        w = S.LEVEL_W
        world = [S.static_box("Ground", w / 2, 690, w, 60, S.COLORS["ground"]),
                 S.static_box("LeftWall", -16, 360, 32, 1000, S.COLORS["wall"]),
                 S.static_box("RightWall", w + 16, 360, 32, 1000, S.COLORS["wall"]),
                 {"name": "Platforms", "type": "Node2D",
                  "children": [S.static_box(f"Platform{i + 1}", *p, S.COLORS["platform"]) for i, p in enumerate(layout["platforms"])]}]
        children.append({"name": "World", "type": "Node2D", "children": world})
    elif genre == "topdown":
        walls = [S.static_box("WallTop", 640, 16, 1280, 32, S.COLORS["wall"]), S.static_box("WallBottom", 640, 704, 1280, 32, S.COLORS["wall"]),
                 S.static_box("WallLeft", 16, 360, 32, 720, S.COLORS["wall"]), S.static_box("WallRight", 1264, 360, 32, 720, S.COLORS["wall"])]
        obst = [S.static_box(f"Obstacle{i + 1}", *o, S.COLORS["platform"]) for i, o in enumerate(layout["obstacles"])]
        children.append({"name": "World", "type": "Node2D", "children": walls + obst})
    else:
        floor_shape = {"_t": "Resource", "type": "BoxShape3D", "props": {"size": [40, 1, 40]}}
        floor_mesh = {"_t": "Resource", "type": "BoxMesh", "props": {"size": [40, 1, 40]}}
        mat = {"_t": "Resource", "type": "StandardMaterial3D", "props": {"albedo_color": S.COLORS["ground"]}}
        children += [
            {"name": "Sun", "type": "DirectionalLight3D", "props": {"rotation_degrees": [-50, -30, 0], "shadow_enabled": False}},
            {"name": "Floor", "type": "StaticBody3D", "props": {"position": [0, -0.5, 0], "collision_layer": 1, "collision_mask": 0},
             "children": [{"name": "CollisionShape3D", "type": "CollisionShape3D", "props": {"shape": floor_shape}},
                          {"name": "Mesh", "type": "MeshInstance3D", "props": {"mesh": floor_mesh, "material_override": mat}}]},
        ]
    start = layout["player_start"]
    children.append({"name": "Player", "instance": PLAYER_SCENE, "props": {"position": start}})
    children.append({"name": "HUD", "instance": HUD_SCENE})
    root_type = "Node3D" if genre == "3d" else "Node2D"
    return {"name": "Main", "type": root_type, "script": "res://scripts/main.gd", "children": children}


def enemy_scene(genre: str) -> dict:
    return {"name": "Enemy", "type": "CharacterBody2D", "script": "res://scripts/enemy.gd", "groups": ["enemy"],
            "props": {"collision_layer": 4, "collision_mask": 1, **({"motion_mode": 1} if genre == "topdown" else {})},
            "children": [S.collision(28, 32), S.visual(28, 32, S.COLORS["enemy"]),
                         {"name": "Hurtbox", "type": "Area2D", "props": {"collision_layer": 0, "collision_mask": 2},
                          "children": [S.collision(36, 40)]}]}


def coin_scene() -> dict:
    return {"name": "Coin", "type": "Area2D", "script": "res://scripts/coin.gd", "groups": ["coin"],
            "props": {"collision_layer": 8, "collision_mask": 2},
            "children": [{"name": "CollisionShape2D", "type": "CollisionShape2D", "props": {"shape": S.circle_shape(11)}},
                         {"name": "Visual", "type": "ColorRect", "props": {"position": [-8, -8], "size": [16, 16], "pivot_offset": [8, 8], "rotation": 0.7854, "color": S.COLORS["coin"], "mouse_filter": 2}}]}


def pause_scene() -> dict:
    full = {"anchor_right": 1.0, "anchor_bottom": 1.0, "offset_right": 0.0, "offset_bottom": 0.0}
    return {"name": "PauseMenu", "type": "CanvasLayer", "script": "res://scripts/pause_menu.gd", "props": {"layer": 100, "process_mode": 3},
            "children": [{"name": "Dim", "type": "ColorRect", "props": {**full, "color": S.COLORS["ui_dim"]}},
                         {"name": "Center", "type": "CenterContainer", "props": full,
                          "children": [{"name": "VBox", "type": "VBoxContainer", "props": {"theme_override_constants/separation": 14},
                                        "children": [{"name": "Title", "type": "Label", "props": {"text": "PAUSED", "horizontal_alignment": 1, "theme_override_font_sizes/font_size": 36}},
                                                     {"name": "ResumeButton", "type": "Button", "props": {"text": "Resume", "custom_minimum_size": [180, 40]}},
                                                     {"name": "QuitButton", "type": "Button", "props": {"text": "Quit", "custom_minimum_size": [180, 40]}}]}]}]}


def menu_scene(title: str, start_scene: str = MAIN_SCENE) -> dict:
    full = {"anchor_right": 1.0, "anchor_bottom": 1.0, "offset_right": 0.0, "offset_bottom": 0.0}
    return {"name": "Menu", "type": "Control", "script": "res://scripts/menu.gd", "props": {**full, "start_scene": start_scene},
            "children": [{"name": "Background", "type": "ColorRect", "props": {**full, "color": S.COLORS["bg"]}},
                         {"name": "Center", "type": "CenterContainer", "props": full,
                          "children": [{"name": "VBox", "type": "VBoxContainer", "props": {"theme_override_constants/separation": 16},
                                        "children": [{"name": "Title", "type": "Label", "props": {"text": title, "horizontal_alignment": 1, "theme_override_font_sizes/font_size": 48}},
                                                     {"name": "StartButton", "type": "Button", "props": {"text": "Start", "custom_minimum_size": [200, 44]}},
                                                     {"name": "QuitButton", "type": "Button", "props": {"text": "Quit", "custom_minimum_size": [200, 44]}}]}]}]}
