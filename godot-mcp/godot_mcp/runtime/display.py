"""가상 디스플레이(Xvfb) 관리. 모든 프로젝트가 하나의 Xvfb 를 공유한다."""
from __future__ import annotations
import json
import os
import select
import shutil
import subprocess
import time

from .. import config
from ..errors import McpError
from ..util import pid_alive


def _state_file():
    return config.state_dir() / "display.json"


def ensure_display() -> str:
    """사용 가능한 DISPLAY 문자열(예: ':99')을 반환한다. 필요하면 Xvfb 를 기동."""
    sf = _state_file()
    try:
        st = json.loads(sf.read_text())
        num = st["display"].lstrip(":")
        if pid_alive(st.get("pid")) and os.path.exists(f"/tmp/.X11-unix/X{num}"):
            return st["display"]
    except (OSError, ValueError, KeyError):
        pass
    xvfb = shutil.which("Xvfb")
    if not xvfb:
        raise McpError("XVFB_MISSING", "Xvfb 가 필요합니다 (sudo apt install xvfb)")
    r, w = os.pipe()
    proc = subprocess.Popen([xvfb, "-displayfd", str(w), "-screen", "0", "1280x720x24", "-nolisten", "tcp"],
                            pass_fds=(w,), start_new_session=True,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    os.close(w)
    line = b""
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and not line.endswith(b"\n"):
        ready, _, _ = select.select([r], [], [], 0.5)
        if ready:
            chunk = os.read(r, 64)
            if not chunk:
                break
            line += chunk
        if proc.poll() is not None:
            break
    os.close(r)
    if not line.strip():
        raise McpError("XVFB_FAILED", "Xvfb 기동 실패")
    display = ":" + line.decode().strip()
    sf.write_text(json.dumps({"display": display, "pid": proc.pid}))
    return display
