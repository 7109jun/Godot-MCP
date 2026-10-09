#!/usr/bin/env bash
# 자가 테스트 흐름 (spec §29) - CLI 버전
set -e
P=demo
godot-mcp create-project $P
godot-mcp create-game -C $P --help >/dev/null
godot-mcp add-mechanic --list
godot-mcp syntax-check -C $P
godot-mcp validate -C $P
godot-mcp run -C $P
godot-mcp game-state -C $P
godot-mcp screenshot -C $P
godot-mcp errors -C $P
godot-mcp stop -C $P
godot-mcp fix -C $P
godot-mcp test -C $P --all
godot-mcp build -C $P
