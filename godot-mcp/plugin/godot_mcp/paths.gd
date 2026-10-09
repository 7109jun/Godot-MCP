@tool
extends RefCounted
## res:// 경로 검증. 프로젝트 밖으로의 탈출(../)과 보호 영역 쓰기를 차단한다.

static func normalize(p: String) -> String:
	if not p.begins_with("res://"):
		return ""
	var parts := p.substr(6).split("/", false)
	if parts.is_empty():
		return ""
	for part in parts:
		if part == ".." or part == "." or part.contains("\\") or part.contains("\u0000"):
			return ""
	return "res://" + "/".join(parts)


static func is_protected(p: String) -> bool:
	var n := normalize(p)
	return n == "" or n.begins_with("res://.godot") or n.begins_with("res://addons/godot_mcp") or n.begins_with("res://.godot_mcp")
