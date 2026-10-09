"""프로젝트 파일 생성 (project.godot 초기본, 디렉터리, .gitignore, 플러그인 설치)."""
from __future__ import annotations
from pathlib import Path

from ..runtime import editor as editor_rt

GODOT_FEATURE = "4.4"


def project_godot_text(name: str, type_: str, width: int = 1280, height: int = 720) -> str:
    safe = name.replace('"', "'")
    stretch = 'window/stretch/mode="canvas_items"\nwindow/stretch/aspect="keep"\n' if type_ == "2d" else ""
    layers = ""
    if type_ == "2d":
        layers = ('[layer_names]\n\n2d_physics/layer_1="world"\n2d_physics/layer_2="player"\n'
                  '2d_physics/layer_3="enemy"\n2d_physics/layer_4="pickup"\n\n')
    return f'''; Engine configuration file.
; (godot-mcp 가 생성. 이후 수정은 Godot 플러그인을 통해 수행된다.)

config_version=5

[application]

config/name="{safe}"
run/main_scene="res://scenes/main.tscn"
config/features=PackedStringArray("{GODOT_FEATURE}", "GL Compatibility")

[autoload]

McpBridge="*res://addons/godot_mcp/game_bridge.gd"

[display]

window/size/viewport_width={width}
window/size/viewport_height={height}
{stretch}
[editor_plugins]

enabled=PackedStringArray("res://addons/godot_mcp/plugin.cfg")

{layers}[rendering]

renderer/rendering_method="gl_compatibility"
renderer/rendering_method.mobile="gl_compatibility"
environment/defaults/default_clear_color=Color(0.09, 0.1, 0.14, 1)
'''


def create_project_files(root: Path, name: str, type_: str) -> list[str]:
    root.mkdir(parents=True, exist_ok=True)
    created = []
    for d in ("scenes", "scripts", "assets"):
        (root / d).mkdir(exist_ok=True)
        keep = root / d / ".gdignore" if False else None
    (root / "project.godot").write_text(project_godot_text(name, type_))
    created.append("project.godot")
    (root / ".gitignore").write_text(".godot/\n.godot_mcp/\nbuild/\n*.translation\n")
    editor_rt.install_plugin(root)
    created.append("addons/godot_mcp/")
    return created
