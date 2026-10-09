# Godot MCP — AI Agent 사용 가이드

너는 이 MCP 도구들로 Godot 4 게임을 **직접 만들고, 실행해서 관찰하고, 오류를 고치고, 테스트하고, 빌드**한다.
저수준 도구(create_node, set_property 등)는 없다. 한 번의 호출이 여러 Godot 작업을 수행한다.

## 1. 기본 원칙
1. **만들었으면 반드시 검증한다.** 코드/씬을 바꾼 뒤에는 `syntax_check` → `validate` → (필요 시) `run`/`test`. 검증 없이 "완료"라고 하지 않는다.
2. **추측하지 말고 관찰한다.** 상태는 `project_state`, `inspect`, `game_state`, `screenshot`, `errors`로 확인한다.
3. **모든 결과는 구조화된 JSON**이다. `success`를 먼저 보고, 실패면 `error.code`/`errors[]`(file, line, column, message, context)를 읽는다. 결과의 `next` 힌트가 있으면 따른다.
4. `project` 인자를 생략하면 마지막으로 쓴 프로젝트가 대상이다. 프로젝트가 여러 개일 때는 **항상 명시**한다.
5. 경로는 프로젝트 기준 `res://...`를 쓴다. 프로젝트 밖(`../`, 절대경로)과 보호 경로(`.godot/`, `.godot_mcp/`, `addons/godot_mcp/`, `.git/`)는 거부된다.

## 2. 표준 루프
```
create_game ─▶ add_mechanic(…) ─▶ syntax_check ─▶ validate ─▶ run
   ─▶ game_state / screenshot / errors ─▶ (오류 있으면) fix ─▶ syntax_check ─▶ test(all) ─▶ build
```
- 새 게임: `create_game(name, description, mechanics=[...])`. 장르는 name/description에서 추론되며(`platformer|topdown|3d`) 명시도 가능하다.
- 기능 추가: 먼저 `add_mechanic`(검증된 템플릿)으로 해결되는지 본다. 안 되면 `modify_game`으로 직접 코드를 쓴다.
- 끝낼 때: `test(all=true)`가 통과하고 `build`가 성공해야 완료다.

## 3. 도구 선택표
| 하고 싶은 일 | 도구 |
|---|---|
| 빈 프로젝트 | `create_project` |
| 실행 가능한 게임 뼈대 + 메카닉 | `create_game` |
| 씬 추가(menu/level/ui/empty) | `create_scene` |
| 검증된 기능 추가 | `add_mechanic` |
… (+152 more rows)
… (the count above is the only elision — earlier marker-like lines are part of the command)
