"""외부 프로세스 없이 도는 빠른 단위 테스트: python tests/test_unit.py (또는 pytest)."""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from godot_mcp import security  # noqa: E402
from godot_mcp.errors import McpError  # noqa: E402
from godot_mcp.fixing.fixer import normalize_indentation  # noqa: E402
from godot_mcp.fixing import gdsrc  # noqa: E402
from godot_mcp.mechanics import registry as R  # noqa: E402
from godot_mcp.project import tscn  # noqa: E402
from godot_mcp.validation import logparse  # noqa: E402
from godot_mcp.util_yaml import dump  # noqa: E402


def raises(code, fn, *a, **kw):
    try:
        fn(*a, **kw)
    except McpError as e:
        assert e.code == code, f"{e.code} != {code}"
        return
    raise AssertionError(f"McpError({code}) not raised")


def test_path_security():
    root = Path(tempfile.mkdtemp()).resolve()
    (root / "scripts").mkdir()
    assert security.resolve(root, "res://scripts/a.gd") == root / "scripts/a.gd"
    raises("PATH_ESCAPE", security.resolve, root, "res://../../etc/passwd")
    raises("PATH_ESCAPE", security.resolve, root, "../x.gd")
    raises("PATH_ESCAPE", security.resolve, root, "/etc/passwd")
    raises("PATH_PROTECTED", security.resolve, root, "res://addons/godot_mcp/plugin.gd", write=True)
    raises("PATH_PROTECTED", security.resolve, root, "res://.godot_mcp/session.json", write=True)
    raises("INVALID_PATH", security.resolve, root, "res://a\\b.gd")
    (root / "link").symlink_to("/etc")
    raises("PATH_ESCAPE", security.resolve, root, "res://link/passwd")


def test_logparse():
    log = ("SCRIPT ERROR: Parse Error: Expected \":\" after \"if\" condition.\n          at: GDScript::reload (res://a.gd:3)\n"
           "ERROR: Node not found: \"X\" (relative to \"/root/Main\").\n   at: get_node (scene/main/node.cpp:1877)\n"
           "SCRIPT ERROR: Invalid access to property or key 'name' on a base object of type 'null instance'.\n          at: _ready (res://b.gd:5)\n"
           "WARNING: Could not set V-Sync mode\n     at: x (y.cpp:1)\nALSA lib pcm.c:1 junk\n")
    es = logparse.parse_log(log)
    assert [e["type"] for e in es] == ["syntax", "runtime", "script", "runtime"], es
    assert es[0]["file"] == "res://a.gd" and es[0]["line"] == 3
    assert es[1]["node"] == "X" and es[1]["scene"] == "Main"
    assert es[3]["severity"] == "warning"


def test_tscn_parse():
    d = Path(tempfile.mkdtemp())
    p = d / "s.tscn"
    p.write_text('[gd_scene load_steps=2 format=3 uid="uid://abc"]\n\n[ext_resource type="Script" path="res://p.gd" id="1"]\n\n'
                 '[node name="Main" type="Node2D"]\nscript = ExtResource("1")\n\n[node name="P" type="Node2D" parent="." groups=["player"]]\n\n'
                 '[node name="C" type="Node2D" parent="P"]\n\n[connection signal="ready" from="P" to="." method="_x"]\n')
    sc = tscn.parse(p)
    assert sc.uid == "uid://abc" and [n.path for n in sc.nodes] == [".", "P", "P/C"]
    assert sc.nodes[0].script_path == "res://p.gd" and sc.nodes[1].groups == ["player"]
    assert tscn.normalize_rel("P", "../Q") == "Q" and tscn.normalize_rel(".", "A/B") == "A/B" and tscn.normalize_rel(".", "..") is None


def test_gdsrc():
    assert gdsrc.code_part('a = "#" # c') == ('a = "#" ', "# c")
    assert gdsrc.replace_word("\tvelocityy.x = 3 # velocityy", "velocityy", "velocity") == "\tvelocity.x = 3 # velocityy"
    assert gdsrc.replace_member("\tmove_and_slid()", "move_and_slid", "move_and_slide") == "\tmove_and_slide()"
    assert gdsrc.unterminated_quote('print("hi)') == '"'


def test_indentation():
    out = normalize_indentation("extends Node\nfunc a():\n\t\tprint(1)\n\tprint(2)\n".split("\n"))
    assert out[3] == "\t\tprint(2)"
    out = normalize_indentation("extends Node\nfunc a():\n    if true:\n        print(1)\n    print(2)\n".split("\n"))
    assert out[2] == "\tif true:" and out[3] == "\t\tprint(1)" and out[4] == "\tprint(2)"


def test_mechanic_resolution():
    assert R.resolve("Double Jump")[0].key == "double_jump"
    assert R.resolve("add a triple jump")[1] == {"extra_jumps": 2}
    assert R.resolve("enemy AI")[0].key == "enemy_ai"
    m, o = R.resolve("8 coins")
    assert m.key == "coin_collection" and o["count"] == 8
    assert R.order(["enemy_ai"]) == ["player_movement", "health_system", "enemy_ai"]
    raises("UNSUPPORTED_MECHANIC", R.resolve, "teleport")


def test_yaml():
    assert dump({"a": {"b": [1, 2]}, "n": [{"x": 1, "y": "z w"}], "e": []}) == "a:\n  b: [1, 2]\nn:\n  - x: 1\n    y: z w\ne: []"


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for f in fns:
        f()
        print("PASS", f.__name__)
    print(f"{len(fns)} unit tests passed")
