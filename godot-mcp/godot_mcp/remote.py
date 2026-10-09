"""원격 접속: HTTP(streamable) MCP 서버 (Bearer 토큰 + 선택적 TLS) 와 원격 CLI 클라이언트."""
from __future__ import annotations

import json
import os
import secrets
import sys
from pathlib import Path

from .config import state_dir

TOKEN_ENV = "GODOT_MCP_REMOTE_TOKEN"
URL_ENV = "GODOT_MCP_REMOTE_URL"


def _token_file() -> Path:
    return state_dir() / "remote_token"


def load_or_create_token(explicit: str | None = None) -> str:
    tok = explicit or os.environ.get(TOKEN_ENV)
    if tok:
        if len(tok) < 16:
            raise SystemExit("원격 토큰은 16자 이상이어야 합니다")
        return tok
    f = _token_file()
    if f.exists():
        return f.read_text().strip()
    f.parent.mkdir(parents=True, exist_ok=True)
    tok = secrets.token_urlsafe(32)
    fd = os.open(f, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(tok)
    return tok


def serve(host: str, port: int, token: str | None = None, certfile: str | None = None,
          keyfile: str | None = None, transport: str = "http", path: str = "/mcp") -> None:
    from fastmcp.server.auth import StaticTokenVerifier
    from . import server

    tok = load_or_create_token(token)
    server.mcp.auth = StaticTokenVerifier({tok: {"client_id": "godot-mcp-remote", "scopes": []}})
    loopback = host in ("127.0.0.1", "localhost", "::1")
    if not loopback and not (certfile and keyfile):
        print("경고: TLS 없이 외부 인터페이스에 바인딩합니다. 신뢰할 수 있는 네트워크/SSH 터널/리버스 프록시 뒤에서만 사용하세요 "
              "(--certfile/--keyfile 로 TLS 활성화).", file=sys.stderr)
    scheme = "https" if certfile else "http"
    print(f"[godot-mcp] {scheme}://{host}:{port}{path}  (Authorization: Bearer <token>)\n"
          f"[godot-mcp] 토큰 파일: {_token_file()}", file=sys.stderr)
    uv: dict = {}
    if certfile and keyfile:
        uv.update(ssl_certfile=certfile, ssl_keyfile=keyfile)
    kw = dict(transport=transport, host=host, port=port, uvicorn_config=uv or None)
    if transport == "http":
        kw["path"] = path
    server.mcp.run(**kw)


# ---- 원격 CLI 클라이언트 -------------------------------------------------------------

_TOOL_ALIASES = {"play": "play"}


async def call_remote(url: str, token: str | None, tool: str, args: dict, timeout: float = 900):
    from fastmcp import Client
    from fastmcp.client.auth import BearerAuth

    async with Client(url, auth=BearerAuth(token) if token else None, timeout=timeout) as c:
        res = await c.call_tool(tool, args, raise_on_error=False)
    if res.is_error:
        text = "".join(getattr(b, "text", "") for b in res.content)
        return {"success": False, "error": {"code": "REMOTE_ERROR", "message": text}}
    if res.structured_content is not None and not any(getattr(b, "type", "") == "image" for b in res.content):
        data = res.structured_content
        return data.get("result", data) if set(data) == {"result"} else data
    out: dict = {}
    for b in res.content:
        if getattr(b, "type", "") == "image":
            import base64
            out.setdefault("_images", []).append(base64.b64decode(b.data))
            continue
        t = getattr(b, "text", None)
        if t:
            try:
                out.update(json.loads(t))
            except ValueError:
                out["text"] = t
    return out
