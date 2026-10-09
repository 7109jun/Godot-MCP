"""플러그인(GDScript) 자체를 Godot 실제 파서로 검사한다. (plugin/ 수정 후 반드시 실행)"""
import asyncio
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import os
os.environ.setdefault("GODOT_MCP_ALLOWED_ROOTS", tempfile.gettempdir())

from godot_mcp.project import scaffold  # noqa: E402
from godot_mcp.validation import syntax  # noqa: E402


async def main() -> int:
    root = Path(tempfile.mkdtemp(prefix="gmcp_plugin_"))
    try:
        (root / "project.godot").write_text('config_version=5\n[application]\nconfig/name="p"\n')
        from godot_mcp.runtime import editor
        editor.install_plugin(root)
        files = sorted((root / "addons/godot_mcp").rglob("*.gd"))
        r = await syntax.check_scripts(root, files)
        print(f"checked {r['checked_files']} plugin scripts: {'OK' if r['success'] else 'FAIL'}")
        for e in r["errors"]:
            print(f"  {e.get('file')}:{e.get('line')}: {e['message']}")
        return 0 if r["success"] else 1
    finally:
        shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
