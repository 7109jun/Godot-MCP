#!/usr/bin/env bash
# Godot 4.x 설치 스크립트 (Ubuntu/POSIX). 사용: scripts/install_godot.sh [버전] [--templates]
set -euo pipefail
VER="${1:-4.4.1}"
PREFIX="${GODOT_PREFIX:-$HOME/.local/godot}"
BIN="$PREFIX/godot"
URL="https://github.com/godotengine/godot/releases/download/${VER}-stable"
mkdir -p "$PREFIX"
if [ ! -x "$BIN" ]; then
  tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
  curl -fSL -o "$tmp/g.zip" "$URL/Godot_v${VER}-stable_linux.x86_64.zip"
  unzip -oq "$tmp/g.zip" -d "$PREFIX"
  mv "$PREFIX/Godot_v${VER}-stable_linux.x86_64" "$BIN"; chmod +x "$BIN"
fi
if [ "${2:-}" = "--templates" ] || [ "${1:-}" = "--templates" ]; then
  T="$HOME/.local/share/godot/export_templates/${VER}.stable"
  if [ ! -f "$T/linux_release.x86_64" ]; then
    tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
    curl -fSL -o "$tmp/t.tpz" "$URL/Godot_v${VER}-stable_export_templates.tpz"
    mkdir -p "$T"; unzip -oqj "$tmp/t.tpz" 'templates/linux_*' 'templates/version.txt' -d "$T"
  fi
fi
echo "godot: $BIN"; "$BIN" --version --headless
echo "export GODOT_MCP_GODOT=$BIN"
