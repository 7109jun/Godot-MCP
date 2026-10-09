"""Godot 플러그인/게임 브릿지와 통신하는 비동기 IPC 클라이언트.

프레임: u32 big-endian 길이 + UTF-8 JSON. 요청마다 id, 프로토콜 버전 포함.
연결당 요청은 asyncio.Lock 으로 직렬화(동시 요청 보호), 타임아웃/재연결 지원.
"""
from __future__ import annotations
import asyncio
import itertools
import json
import struct

from .. import PROTOCOL_VERSION
from ..errors import McpError

MAX_MESSAGE = 32 * 1024 * 1024


class IpcClient:
    def __init__(self, port: int, token: str, role: str = "editor", host: str = "127.0.0.1"):
        self.host, self.port, self.token, self.role = host, port, token, role
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._lock = asyncio.Lock()
        self._ids = itertools.count(1)
        self.hello: dict = {}

    @property
    def connected(self) -> bool:
        return self._writer is not None and not self._writer.is_closing()

    async def connect(self, timeout: float = 3.0) -> dict:
        await self.close()
        try:
            self._reader, self._writer = await asyncio.wait_for(asyncio.open_connection(self.host, self.port), timeout)
        except (OSError, asyncio.TimeoutError) as e:
            raise McpError("IPC_UNAVAILABLE", f"{self.role} 에 연결할 수 없습니다 (127.0.0.1:{self.port}): {e}")
        res = await self._roundtrip("hello", {}, timeout, auth=self.token)
        self.hello = res
        return res

    async def close(self) -> None:
        if self._writer is not None:
            try:
                self._writer.close()
                await self._writer.wait_closed()
            except Exception:
                pass
        self._reader = self._writer = None

    async def _roundtrip(self, cmd: str, args: dict, timeout: float, auth: str | None = None) -> dict:
        assert self._reader and self._writer
        rid = next(self._ids)
        msg = {"v": PROTOCOL_VERSION, "id": rid, "cmd": cmd, "args": args}
        if auth is not None:
            msg["auth"] = auth
        body = json.dumps(msg).encode()
        if len(body) > MAX_MESSAGE:
            raise McpError("REQUEST_TOO_LARGE", "요청이 너무 큽니다")
        self._writer.write(struct.pack(">I", len(body)) + body)
        await self._writer.drain()
        try:
            hdr = await asyncio.wait_for(self._reader.readexactly(4), timeout)
            (n,) = struct.unpack(">I", hdr)
            if n <= 0 or n > MAX_MESSAGE:
                raise McpError("IPC_PROTOCOL", f"잘못된 프레임 길이 {n}")
            data = await asyncio.wait_for(self._reader.readexactly(n), timeout)
        except asyncio.TimeoutError:
            await self.close()  # 늦게 도착할 응답과 섞이지 않도록 연결 폐기
            raise McpError("IPC_TIMEOUT", f"{self.role} '{cmd}' 응답 시간 초과 ({timeout}s)")
        except (asyncio.IncompleteReadError, ConnectionError) as e:
            await self.close()
            raise McpError("IPC_DISCONNECTED", f"{self.role} 연결이 끊겼습니다: {e}")
        try:
            resp = json.loads(data)
        except ValueError:
            raise McpError("IPC_PROTOCOL", "응답 JSON 파싱 실패")
        if resp.get("id") != rid:
            await self.close()
            raise McpError("IPC_PROTOCOL", f"응답 id 불일치 (기대 {rid}, 수신 {resp.get('id')})")
        if not resp.get("ok"):
            err = resp.get("error", {})
            raise McpError(err.get("code", "REMOTE_ERROR"), err.get("message", "remote error"),
                           **{k: v for k, v in err.items() if k not in ("code", "message")})
        return resp.get("result", {})

    async def call(self, cmd: str, args: dict | None = None, timeout: float = 30.0) -> dict:
        """연결이 없으면 (재)연결 후 요청. 연결 오류 시 1회 재시도."""
        import os, sys, time
        t0 = time.monotonic()
        try:
            return await self._call(cmd, args, timeout)
        finally:
            if os.environ.get("GODOT_MCP_TRACE"):
                print(f"[ipc:{self.role}] {cmd} {time.monotonic() - t0:.2f}s", file=sys.stderr, flush=True)

    async def _call(self, cmd: str, args: dict | None, timeout: float) -> dict:
        async with self._lock:
            for attempt in (0, 1):
                try:
                    if not self.connected:
                        await self.connect()
                    return await self._roundtrip(cmd, args or {}, timeout)
                except McpError as e:
                    if e.code in ("IPC_DISCONNECTED", "IPC_UNAVAILABLE") and attempt == 0:
                        await self.close()
                        continue
                    raise
        raise McpError("IPC_UNAVAILABLE", "unreachable")
