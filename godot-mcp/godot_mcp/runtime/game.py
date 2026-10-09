"""실행 중 게임 프로세스 관리 (시작/중지/상태/로그/브릿지 통신)."""
from __future__ import annotations
import asyncio
import os
import shlex
import signal
import subprocess
import time
from pathlib import Path

from .. import config
from ..errors import McpError
from ..ipc.client import IpcClient
from ..session import Session
from ..util import pid_alive, tail
from ..validation import logparse
from . import display

_clients: dict[str, IpcClient] = {}


def paths(project: Path) -> dict:
    d = project / ".godot_mcp"
    return {"log": d / "game.log", "exit": d / "game.exit", "dir": d}


def status(project: Path) -> dict:
    sess = Session(project)
    g = sess.load().get("game") or {}
    if not g:
        return {"running": False, "has_run": False}
    alive = pid_alive(g.get("pid"))
    exit_code = None
    if not alive:
        ef = Path(g.get("exit_file", ""))
        if ef.exists():
            try:
                exit_code = int(ef.read_text().strip())
            except ValueError:
                exit_code = None
        if exit_code is None:
            exit_code = g.get("exit_code")
    started = g.get("started", time.time())
    ended = g.get("ended")
    if not alive and not ended:
        try:
            ended = Path(g["log"]).stat().st_mtime
        except OSError:
            ended = time.time()
    duration = round((time.time() if alive else (ended or time.time())) - started, 2)
    out = {"running": alive, "has_run": True, "pid": g.get("pid"), "scene": g.get("scene"), "duration": duration, "log": g.get("log")}
    if not alive:
        out["exit_code"] = exit_code
        if exit_code == 124:
            out["ended_by"] = "timeout"
    return out


def read_log(project: Path) -> str:
    try:
        return paths(project)["log"].read_text(errors="replace")
    except OSError:
        return ""


def runtime_errors(project: Path) -> list[dict]:
    return logparse.link_script_context(logparse.parse_log(read_log(project)), project)


async def _bridge(project: Path, timeout: float = 2.0) -> IpcClient:
    d = Session(project).load()
    g = d.get("game") or {}
    if not pid_alive(g.get("pid")):
        raise McpError("GAME_NOT_RUNNING", "실행 중인 게임이 없습니다. run() 을 먼저 호출하세요.")
    cl = _clients.get(str(project))
    if cl is None or cl.port != g.get("port") or cl.token != d["token"]:
        cl = IpcClient(int(g["port"]), d["token"], "game")
        _clients[str(project)] = cl
    if not cl.connected:
        await cl.connect(timeout=timeout)
    return cl


async def game_call(project: Path, cmd: str, args: dict | None = None, timeout: float = 15.0) -> dict:
    cl = await _bridge(project)
    return await cl.call(cmd, args or {}, timeout=timeout)


async def start(project: Path, *, scene: str | None = None, timeout: float | None = None,
                ready_timeout: float = 25.0, observe: float = 1.5, headless: bool = False,
                extra_args: list[str] | None = None) -> dict:
    sess = Session(project)
    await stop(project, quiet=True)
    d = sess.ensure()
    port = sess.reset_port("game_port")
    d = sess.load()
    p = paths(project)
    p["dir"].mkdir(mode=0o700, exist_ok=True)
    for f in (p["log"], p["exit"]):
        f.unlink(missing_ok=True)
    env = {**os.environ, "GODOT_MCP_SESSION": str(sess.path)}
    godot = [config.godot_binary(), "--path", str(project), "--audio-driver", "Dummy"]
    if headless:
        godot.append("--headless")
    else:
        env["DISPLAY"] = display.ensure_display()
        godot += ["--rendering-driver", "opengl3"]
    godot += extra_args or []
    if scene:
        godot.append(scene)
    cmd = godot
    cap = timeout or float(os.environ.get("GODOT_MCP_GAME_MAX_S", "1800"))  # 방치된 게임 프로세스 방지용 상한
    cmd = ["timeout", "--signal=TERM", "--kill-after=5", str(int(cap))] + godot
    wrapper = f'{" ".join(shlex.quote(c) for c in cmd)} >> {shlex.quote(str(p["log"]))} 2>&1; echo $? > {shlex.quote(str(p["exit"]))}'
    t0 = time.time()
    proc = subprocess.Popen(["sh", "-c", wrapper], env=env, stdin=subprocess.DEVNULL,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    sess.update(game={"pid": proc.pid, "port": port, "started": t0, "scene": scene or "main", "log": str(p["log"]),
                      "exit_file": str(p["exit"]), "timeout": timeout, "headless": headless})
    bridge_ok = False
    deadline = time.monotonic() + ready_timeout
    cl = IpcClient(port, d["token"], "game")
    _clients[str(project)] = cl
    while time.monotonic() < deadline:
        if not pid_alive(proc.pid):
            break
        try:
            await cl.connect(timeout=1.0)
            bridge_ok = True
            break
        except McpError:
            await asyncio.sleep(0.3)
    if bridge_ok and observe > 0:
        end = time.monotonic() + observe
        while time.monotonic() < end and pid_alive(proc.pid):
            await asyncio.sleep(0.1)
    await asyncio.sleep(0.1)
    st = status(project)
    errs = runtime_errors(project)
    for e in errs:
        e["context"] = logparse.source_context(project, e.get("file"), e.get("line"))
    result = {
        "running": st["running"], "process": {"pid": proc.pid}, "scene": scene or "main (project main scene)",
        "bridge": bridge_ok, "duration": st["duration"], "exit_code": st.get("exit_code"),
        "runtime_errors": [e for e in errs if e["severity"] == "error"],
        "warnings": [e for e in errs if e["severity"] == "warning"][:10],
    }
    if not st["running"]:
        result["crashed_or_exited"] = True
        result["log_tail"] = tail(p["log"], 25)
    elif not bridge_ok:
        result["note"] = "게임은 실행 중이지만 브릿지 연결에 실패했습니다 (game_state/screenshot 사용 불가)"
    return result


async def stop(project: Path, *, quiet: bool = False) -> dict:
    sess = Session(project)
    d = sess.load()
    g = d.get("game") or {}
    pid = g.get("pid")
    cl = _clients.pop(str(project), None)
    if cl:
        await cl.close()
    if not pid_alive(pid):
        st = status(project) if g else {"running": False}
        return {"stopped": False, "was_running": False, **({"exit_code": st.get("exit_code")} if g else {})}
    errs_before = len(runtime_errors(project))
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(os.getpgid(pid), sig)
        except (ProcessLookupError, PermissionError):
            break
        for _ in range(20):
            if not pid_alive(pid):
                break
            await asyncio.sleep(0.15)
        if not pid_alive(pid):
            break
    g["ended"] = time.time()
    g["exit_code"] = -15
    d["game"] = g
    sess.save(d)
    return {"stopped": True, "was_running": True, "duration": round(g["ended"] - g.get("started", g["ended"]), 2),
            "runtime_errors_total": errs_before}
