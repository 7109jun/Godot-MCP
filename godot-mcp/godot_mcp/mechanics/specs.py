"""씬 스펙(JSON) 빌더 헬퍼 + 장르별 월드 레이아웃."""
from __future__ import annotations

COLORS = {"ground": "#3b4252", "platform": "#5e81ac", "player": "#a3be8c", "enemy": "#bf616a",
          "coin": "#ebcb8b", "bg": "#2e3440", "wall": "#434c5e", "ui_dim": [0, 0, 0, 0.6]}

LEVEL_W = 3200
GROUND_TOP = 660


def rect_shape(w: float, h: float) -> dict:
    return {"_t": "Resource", "type": "RectangleShape2D", "props": {"size": [w, h]}}


def circle_shape(r: float) -> dict:
    return {"_t": "Resource", "type": "CircleShape2D", "props": {"radius": r}}


def collision(w: float, h: float, name: str = "CollisionShape2D") -> dict:
    return {"name": name, "type": "CollisionShape2D", "props": {"shape": rect_shape(w, h)}}


def visual(w: float, h: float, color: str, name: str = "Visual") -> dict:
    return {"name": name, "type": "ColorRect", "props": {"position": [-w / 2, -h / 2], "size": [w, h], "color": color, "mouse_filter": 2}}


def static_box(name: str, cx: float, cy: float, w: float, h: float, color: str, groups: list | None = None) -> dict:
    return {"name": name, "type": "StaticBody2D", "props": {"position": [cx, cy], "collision_layer": 1, "collision_mask": 0},
            "groups": groups or [], "children": [collision(w, h), visual(w, h, color)]}


def platformer_layout() -> dict:
    platforms = [(520, 540, 220, 24), (900, 430, 200, 24), (1350, 520, 240, 24), (1800, 420, 220, 24), (2300, 500, 260, 24), (2750, 400, 200, 24)]
    return {"genre": "platformer", "width": LEVEL_W, "height": 720, "ground_top": GROUND_TOP, "player_start": [120, 610],
            "platforms": [list(p) for p in platforms]}


def topdown_layout() -> dict:
    obstacles = [(320, 200, 96, 96), (960, 200, 96, 96), (320, 520, 96, 96), (960, 520, 96, 96), (640, 360, 64, 64)]
    return {"genre": "topdown", "width": 1280, "height": 720, "player_start": [640, 560],
            "obstacles": [list(o) for o in obstacles]}


def layout_3d() -> dict:
    return {"genre": "3d", "player_start": [0, 1.2, 0]}


def coin_positions(layout: dict, n: int) -> list[list[float]]:
    out = []
    if layout["genre"] == "platformer":
        plats = layout["platforms"]
        # 각 플랫폼 위 코인 + 지상 코인
        for (cx, cy, w, h) in plats:
            out.append([cx, cy - h / 2 - 26])
        x = 300
        while len(out) < n:
            out.append([x, layout["ground_top"] - 28])
            x += 380
        out.sort(key=lambda p: p[0])
    else:
        grid = [(160, 120), (640, 120), (1120, 120), (160, 360), (1120, 360), (160, 600), (640, 640), (1120, 600), (480, 280), (800, 440), (480, 440), (800, 280)]
        out = [list(g) for g in grid]
    while len(out) < n:  # 부족하면 반복 배치(약간 이동)
        base = out[len(out) % max(1, len(out))]
        out.append([base[0] + 40, base[1]])
    return out[:n]


def enemy_positions(layout: dict, n: int) -> list[list[float]]:
    if layout["genre"] == "platformer":
        base = [(760, 640), (1560, 640), (2050, 640), (2560, 640), (1000, 640), (2900, 640)]
    else:
        base = [(160, 160), (1120, 160), (160, 600), (1120, 600), (640, 200), (640, 600)]
    out = [list(b) for b in base]
    while len(out) < n:
        out.append([out[len(out) % len(base)][0] + 30, out[len(out) % len(base)][1]])
    return out[:n]


def node_ref(path: str) -> str:
    return path


def hud_label(name: str, text: str) -> dict:
    return {"name": name, "type": "Label", "props": {"text": text, "theme_override_font_sizes/font_size": 20}}
