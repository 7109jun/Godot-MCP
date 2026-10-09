"""Godot 에디터 세션 관리: 플러그인이 로드된 에디터를 (필요 시) 띄우고 IPC 로 명령을 보낸다."""
from __future__ import annotations
import asyncio
import os
import signal
import subprocess
import time
from pathlib import Path

from .. import config
from ..errors import McpError
from ..ipc.client import IpcClient
from ..session import Session
from ..util import pid_alive, tail
from . import display

_clients: dict[str, IpcClient] = {}
_locks: dict[str, asyncio.Lock] = {}


def install_plugin(project: Path) -> bool:
    """addons/godot_mcp 에 플러그인을 설치/갱신한다. 변경 여부 반환."""
    import shutil
    src = config.plugin_source()
    dst = project / "addons" / "godot_mcp"

    def digest(root: Path) -> dict:
        return {p.relative_to(root).as_posix(): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file() and p.suffix in (".gd", ".cfg")}

    if dst.exists() and digest(src) == digest(dst):
        return False
    if dst.exists():
        shutil.rmtree(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(src, dst, ignore=shutil.ignore_patterns("*.uid", "__pycache__"))
    return True


def _lock(project: Path) -> asyncio.Lock:
    return _locks.setdefault(str(project), asyncio.Lock())


async def _try_connect(project: Path, sess: Session) -> IpcClient | None:
    d = sess.load()
    ed = d.get("editor") or {}
    if not pid_alive(ed.get("pid")):
        return None
    cl = _clients.get(str(project))
    if cl is None or cl.port != d.get("editor_port") or cl.token != d.get("token"):
        cl = IpcClient(int(d["editor_port"]), d["token"], "editor")
        _clients[str(project)] = cl
    try:
        if not cl.connected:
            await cl.connect(timeout=2)
        return cl
    except McpError:
        return None


async def get_editor(project: Path, *, start: bool = True, startup_timeout: float = 120.0, mode: str | None = None) -> IpcClient:
    async with _lock(project):
        sess = Session(project)
        ed = sess.load().get("editor") or {}
        if mode and ed.get("mode") and ed["mode"] != mode and pid_alive(ed.get("pid")):
            await stop_editor(project)  # 요청한 모드(예: 스크린샷용 xvfb)와 다르면 재기동
        cl = await _try_connect(project, sess)
        if cl:
            return cl
        if not start:
            raise McpError("EDITOR_NOT_RUNNING", "에디터 세션이 실행 중이 아닙니다")
        return await _launch(project, sess, startup_timeout, mode)


def _registry():
    return config.state_dir() / "editors.json"


def _load_registry() -> dict:
    import json
    try:
        return json.loads(_registry().read_text())
    except (OSError, ValueError):
        return {}


def _save_registry(d: dict) -> None:
    import json
    _registry().write_text(json.dumps(d))


async def _reap_other_editors(project: Path) -> list[str]:
    """프로젝트당 에디터 1개 정책: 다른 프로젝트의 에디터(우리가 띄운 것)는 정리한다 (에디터 1개 ≈ 800MB)."""
    if os.environ.get("GODOT_MCP_MAX_EDITORS", "1") != "1":
        return []
    reg = _load_registry()
    reaped = []
    for path, pid in list(reg.items()):
        if path == str(project):
            continue
        if pid_alive(pid):
            try:
                await stop_editor(Path(path))
            except Exception:
                _kill(pid, signal.SIGKILL)
            reaped.append(path)
        reg.pop(path, None)
    _save_registry(reg)
    return reaped


async def _launch(project: Path, sess: Session, startup_timeout: float, mode: str | None = None) -> IpcClient:
    await _reap_other_editors(project)
    install_plugin(project)
    d = sess.ensure()
    old = (d.get("editor") or {}).get("pid")
    if pid_alive(old):  # 응답 없는 에디터 정리
        _kill(old)
    port = sess.reset_port("editor_port")
    d = sess.load()
    mode = mode or config.editor_mode()
    env = {**os.environ, "GODOT_MCP_SESSION": str(sess.path)}
    argv = [config.godot_binary(), "--editor", "--path", str(project), "--rendering-driver", "opengl3", "--audio-driver", "Dummy", "--low-processor-mode"]
    if mode == "headless":
        argv.append("--headless")
    else:
        env["DISPLAY"] = display.ensure_display()
        argv += ["--resolution", "1280x720"]
    log = project / ".godot_mcp" / "editor.log"
    log.parent.mkdir(mode=0o700, exist_ok=True)
    if log.exists() and log.stat().st_size > 0:
        prev = log.with_name("editor.prev.log")
        prev.write_bytes(log.read_bytes())
        if b"crashed with signal" in prev.read_bytes():
            _note(project, "previous editor crashed (see editor.prev.log)")
    with open(log, "wb") as lf:
        proc = subprocess.Popen(argv, env=env, stdout=lf, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, start_new_session=True)
    sess.update(editor={"pid": proc.pid, "started": time.time(), "mode": mode, "log": str(log)},
                editor_idle_s=int(os.environ.get("GODOT_MCP_EDITOR_IDLE_S", "300")))
    reg = _load_registry()
    reg[str(project)] = proc.pid
    _save_registry(reg)
    cl = IpcClient(port, d["token"], "editor")
    _clients[str(project)] = cl
    deadline = time.monotonic() + startup_timeout
    last_err = ""
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise McpError("EDITOR_START_FAILED", f"Godot 에디터가 종료되었습니다 (exit {proc.returncode})", log_tail=tail(log, 30))
        try:
            await cl.connect(timeout=1.5)
            return cl
        except McpError as e:
            last_err = e.message
        await asyncio.sleep(0.5)
    _kill(proc.pid)
    raise McpError("EDITOR_START_TIMEOUT", f"에디터 IPC 연결 시간 초과 ({startup_timeout}s): {last_err}", log_tail=tail(log, 30))


def _kill(pid: int | None, sig: int = signal.SIGTERM) -> None:
    if not pid:
        return
    try:
        os.killpg(os.getpgid(pid), sig)
    except (ProcessLookupError, PermissionError):
        pass


async def editor_call(project: Path, cmd: str, args: dict | None = None, timeout: float = 60.0) -> dict:
    """에디터 명령 호출. 에디터가 (크래시 등으로) 죽었으면 자동으로 재기동하고 1회 재시도한다 (모든 명령은 멱등)."""
    for attempt in (0, 1):
        cl = await get_editor(project)
        try:
            return await cl.call(cmd, args or {}, timeout=timeout)
        except McpError as e:
            pid = (Session(project).load().get("editor") or {}).get("pid")
            if attempt == 0 and e.code in ("IPC_DISCONNECTED", "IPC_UNAVAILABLE", "IPC_TIMEOUT") and not pid_alive(pid):
                _note(project, f"editor died during '{cmd}' ({e.code}); restarting")
                _clients.pop(str(project), None)
                continue
            raise
    raise McpError("EDITOR_UNAVAILABLE", "에디터를 재기동했지만 명령이 실패했습니다")


def _note(project: Path, msg: str) -> None:
    try:
        with open(project / ".godot_mcp" / "events.log", "a") as f:
            f.write(f"{time.strftime('%H:%M:%S')} {msg}\n")
    except OSError:
        pass


async def editor_running(project: Path) -> bool:
    return (await _try_connect(project, Session(project))) is not None


async def stop_editor(project: Path) -> dict:
    sess = Session(project)
    d = sess.load()
    pid = (d.get("editor") or {}).get("pid")
    if not pid_alive(pid):
        return {"stopped": False, "reason": "not running"}
    cl = await _try_connect(project, sess)
    if cl:
        try:
            await cl.call("quit", timeout=5)
        except McpError:
            pass
        await cl.close()
    for _ in range(40):
        if not pid_alive(pid):
            break
        await asyncio.sleep(0.25)
    if pid_alive(pid):
        _kill(pid, signal.SIGTERM)
        await asyncio.sleep(1)
        if pid_alive(pid):
            _kill(pid, signal.SIGKILL)
    d = sess.load()
    d.pop("editor", None)
    sess.save(d)
    _clients.pop(str(project), None)
    return {"stopped": True}
