# Godot MCP

AI Agent가 Godot 프로젝트를 자율적으로 만들고, 실행·관찰·수정·테스트·빌드하는 고수준 MCP 서버 (Python + FastMCP) + CLI + GDScript 에디터 플러그인.

```
AI Agent ─MCP(stdio)→ godot-mcp server ─loopback IPC(토큰 인증)→ Godot 플러그인 → Godot Editor / Game
```

## 설치 (Ubuntu / POSIX)
```bash
bash scripts/install_godot.sh 4.4.1 --templates   # Godot + export templates → ~/.local/godot
pip install -e .
```
Xvfb 필요 (게임 스크린샷): `sudo apt install xvfb`

## MCP 클라이언트 설정
`examples/mcp.json` 참고. 실행: `godot-mcp serve` 또는 `python -m godot_mcp.server`.

## 도구 ↔ CLI (1:1)
create_project, create_game, create_scene, add_mechanic, modify_game, syntax_check, validate, run, stop, inspect, screenshot, game_state, project_state, errors, test, fix, build (+ play: 입력 시뮬레이션).
CLI: `godot-mcp <command> [-C project] [--yaml]` (예: `godot-mcp syntax-check -C demo`). 저수준 node/property 도구는 노출하지 않는다.

Mechanics: player_movement, double_jump, dash, health_system, coin_collection, enemy_ai, inventory, pause_menu, main_menu (`add-mechanic --list`). 의존 메카닉 자동 적용.

## 환경 변수
GODOT_MCP_GODOT, GODOT_MCP_WORKSPACE(기본 ~/godot-projects), GODOT_MCP_ALLOWED_ROOTS, GODOT_MCP_STATE_DIR, GODOT_MCP_EDITOR_MODE(headless|xvfb), GODOT_MCP_EDITOR_IDLE_S(300), GODOT_MCP_GAME_MAX_S(1800), GODOT_MCP_MAX_EDITORS, GODOT_MCP_TRACE.

## 보안
- 경로 검증: `..`, 절대경로, 심볼릭 링크, NUL, 보호 경로(.godot, .godot_mcp, addons/godot_mcp, .git) 차단; 허용 루트 밖 프로젝트 거부
- IPC: 127.0.0.1 전용, `hello` 토큰 인증(상수시간 비교), 프로토콜 버전, 32MB 제한, 요청 검증
- 모든 결과는 구조화된 dict (`success`, `error{code,message,...}`)

## 에이전트 루프
create_game → add_mechanic → syntax_check → validate → run → game_state/screenshot/errors → fix → test → build. `examples/agent_loop.sh` 참고.
syntax_check 는 실제 Godot 파서(체커 씬, autoload 포함)를 사용하고, validate 는 구조(메인 씬, autoload, ext_resource, 노드 참조, input action 등)를 검사한다. fix 는 규칙 기반 수정 + 해결 불가 항목에 대한 진단 컨텍스트를 반환한다.

## 테스트
```bash
python3 tests/test_unit.py
python3 tests/plugin_syntax.py
GODOT_MCP_ALLOWED_ROOTS=/tmp python3 tests/e2e_mcp.py /tmp/gmcp_e2e   # 33 checks (spec §29 전체)
```

## 제약
Linux 타깃(빌드는 linux, pck)만 지원. Windows/macOS 미지원.

## 원격 사용
서버 머신(Godot·Xvfb 설치된 곳)에서:
```bash
godot-mcp serve --transport http --host 0.0.0.0 --port 8765 [--certfile c.pem --keyfile k.pem]
# 토큰: --token / $GODOT_MCP_REMOTE_TOKEN / 미지정 시 자동 생성되어 <state_dir>/remote_token (0600) 에 저장
```
- 모든 요청은 `Authorization: Bearer <token>` 필수 (없거나 틀리면 401). 기본 바인딩은 127.0.0.1.
- 외부 바인딩 시 TLS(`--certfile/--keyfile`) 또는 SSH 터널(`ssh -L 8765:127.0.0.1:8765 host`) / 리버스 프록시 권장.
- 프로젝트 경로·보안 검증(허용 루트 등)은 모두 서버 쪽 파일시스템 기준으로 적용된다.

MCP 클라이언트 (`examples/mcp_remote.json`):
```json
{"mcpServers": {"godot": {"url": "https://host:8765/mcp", "headers": {"Authorization": "Bearer <token>"}}}}
```
원격 CLI: `export GODOT_MCP_REMOTE_URL=https://host:8765/mcp GODOT_MCP_REMOTE_TOKEN=...` 후 평소처럼 `godot-mcp run -C Game` (또는 `--remote URL --remote-token T`). 스크린샷은 로컬 `./screenshot.png` 로 저장, `modify-game --write` 는 로컬 파일 내용을 서버로 전송한다.
