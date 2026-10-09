from __future__ import annotations
import asyncio
import hashlib
import os
import time
from pathlib import Path


def sha1_file(p: Path) -> str:
    return hashlib.sha1(p.read_bytes()).hexdigest()


def tail(path: Path, n: int = 40) -> str:
    try:
        lines = path.read_text(errors="replace").splitlines()
    except OSError:
        return ""
    return "\n".join(lines[-n:])


def pid_alive(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        os.waitpid(pid, os.WNOHANG)  # 자식이면 좀비 수거
    except (ChildProcessError, OSError):
        pass
    try:
        with open(f"/proc/{pid}/stat") as f:
            state = f.read().rsplit(")", 1)[1].split()[0]
        return state != "Z"
    except (FileNotFoundError, ProcessLookupError, IndexError, PermissionError):
        return False


async def run_cmd(argv: list[str], *, timeout: float = 120, env: dict | None = None, cwd: str | None = None) -> tuple[int, str, float]:
    """명령 실행 후 (exit_code, 합쳐진 출력, 소요시간). 타임아웃 시 exit_code=-9."""
    t0 = time.monotonic()
    proc = await asyncio.create_subprocess_exec(
        *argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
        env={**os.environ, **(env or {})}, cwd=cwd, start_new_session=True)
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout)
        code = proc.returncode if proc.returncode is not None else -1
    except asyncio.TimeoutError:
        try:
            os.killpg(proc.pid, 9)
        except ProcessLookupError:
            pass
        out, _ = await proc.communicate()
        code = -9
    return code, out.decode("utf-8", errors="replace"), time.monotonic() - t0
