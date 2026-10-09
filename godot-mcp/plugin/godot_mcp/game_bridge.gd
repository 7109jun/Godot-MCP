extends Node
## 실행 중인 게임 안에서 상태/스크린샷/입력 주입을 제공하는 브릿지(autoload).
## GODOT_MCP_SESSION 환경변수가 없으면(=일반 실행/export) 즉시 스스로 제거된다.

const IpcServer := preload("res://addons/godot_mcp/ipc_server.gd")
const Value := preload("res://addons/godot_mcp/value_codec.gd")

const TRACK_GROUPS := ["player", "enemy", "enemies", "coin", "coins", "pickup", "npc", "projectile", "boss"]

var _ipc
var _events: Array = []
var _start_ms := 0


func _ready() -> void:
	if Engine.is_editor_hint():
		queue_free()
		return
	var spath := OS.get_environment("GODOT_MCP_SESSION")
	if spath == "" or not FileAccess.file_exists(spath):
		queue_free()
		return
	var session = JSON.parse_string(FileAccess.get_file_as_string(spath))
	if typeof(session) != TYPE_DICTIONARY or not session.has("game_port"):
		queue_free()
		return
	process_mode = Node.PROCESS_MODE_ALWAYS
	_start_ms = Time.get_ticks_msec()
	_ipc = IpcServer.new()
	_ipc.role = "game"
	var err: Error = _ipc.start(int(session["game_port"]), str(session["token"]), Callable(self, "_handle"))
	if err != OK:
		push_error("[godot_mcp] game bridge cannot listen (err %d)" % err)
		_ipc = null
		return
	call_deferred("_hook_events")


func _hook_events() -> void:
	var gs := get_node_or_null("/root/GameState")
	if gs != null and gs.has_signal("event"):
		gs.connect("event", Callable(self, "_on_event"))


func _on_event(event_name: String, data: Variant = null) -> void:
	_events.append({"t_ms": Time.get_ticks_msec() - _start_ms, "name": event_name, "data": Value.encode(data)})
	if _events.size() > 100:
		_events.pop_front()


func _process(_delta: float) -> void:
	if _ipc != null:
		_ipc.poll()


func _exit_tree() -> void:
	if _ipc != null:
		_ipc.stop()


func _handle(cmd: String, args: Dictionary) -> Dictionary:
	match cmd:
		"ping":
			return {"pong": true}
		"game_state":
			return _game_state(args)
		"screenshot":
			return _screenshot(args)
		"input":
			return await _simulate_input(args)
		"node_state":
			return _node_state(args)
		"pause":
			get_tree().paused = true
			return {"paused": true}
		"resume":
			get_tree().paused = false
			return {"paused": false}
		"events":
			return {"events": _events.duplicate()}
		"quit":
			get_tree().call_deferred("quit")
			return {"quitting": true}
	return {"__error": {"code": "UNKNOWN_COMMAND", "message": "unknown game command '%s'" % cmd}}


func _script_vars(node: Node, limit: int = 14) -> Dictionary:
	var out := {}
	if node.get_script() == null:
		return out
	for p in node.get_property_list():
		if (int(p["usage"]) & PROPERTY_USAGE_SCRIPT_VARIABLE) and not str(p["name"]).begins_with("_"):
			var t: int = p["type"]
			if t in [TYPE_BOOL, TYPE_INT, TYPE_FLOAT, TYPE_STRING, TYPE_VECTOR2, TYPE_VECTOR3, TYPE_COLOR, TYPE_STRING_NAME]:
				out[str(p["name"])] = Value.encode(node.get(str(p["name"])))
				if out.size() >= limit:
					break
	return out


func _entity(node: Node) -> Dictionary:
	var d := {"name": str(node.name), "path": str(node.get_path()), "type": node.get_class()}
	if node is Node2D:
		d["position"] = Value.encode((node as Node2D).global_position)
		d["visible"] = (node as Node2D).visible
	elif node is Node3D:
		d["position"] = Value.encode((node as Node3D).global_position)
		d["visible"] = (node as Node3D).visible
	if node is CharacterBody2D:
		d["velocity"] = Value.encode((node as CharacterBody2D).velocity)
		d["on_floor"] = (node as CharacterBody2D).is_on_floor()
	elif node is CharacterBody3D:
		d["velocity"] = Value.encode((node as CharacterBody3D).velocity)
		d["on_floor"] = (node as CharacterBody3D).is_on_floor()
	var vars := _script_vars(node)
	if not vars.is_empty():
		d["vars"] = vars
	for ch in node.get_children():
		if ch.get_script() != null and ch is Node and not (ch is CanvasItem and ch.get_child_count() > 3):
			var cv := _script_vars(ch, 8)
			if not cv.is_empty():
				if not d.has("components"):
					d["components"] = {}
				d["components"][str(ch.name)] = cv
	return d


func _tree_summary(node: Node, depth: int, budget: Array) -> Dictionary:
	budget[0] -= 1
	var d := {"name": str(node.name), "type": node.get_class()}
	if depth > 0 and budget[0] > 0:
		var kids: Array = []
		for ch in node.get_children():
			if budget[0] <= 0:
				break
			kids.append(_tree_summary(ch, depth - 1, budget))
		if not kids.is_empty():
			d["children"] = kids
	elif node.get_child_count() > 0:
		d["child_count"] = node.get_child_count()
	return d


func _game_state(args: Dictionary) -> Dictionary:
	var tree := get_tree()
	var cs := tree.current_scene
	var st := {
		"running": true,
		"paused": tree.paused,
		"fps": Engine.get_frames_per_second(),
		"frames": Engine.get_process_frames(),
		"uptime_ms": Time.get_ticks_msec() - _start_ms,
		"time_scale": Engine.time_scale,
		"scene": {"name": str(cs.name) if cs else "", "file": cs.scene_file_path if cs else ""},
		"window": [get_viewport().get_visible_rect().size.x, get_viewport().get_visible_rect().size.y],
	}
	var entities: Array = []
	var seen := {}
	for g in TRACK_GROUPS:
		for n in tree.get_nodes_in_group(g):
			if seen.has(n) or entities.size() >= 60:
				continue
			seen[n] = true
			var e := _entity(n)
			e["group"] = g
			entities.append(e)
	st["entities"] = entities
	var autos := {}
	for ch in tree.root.get_children():
		if ch != cs and ch != self and ch.get_script() != null:
			autos[str(ch.name)] = _script_vars(ch)
	st["autoloads"] = autos
	if cs != null:
		st["tree"] = _tree_summary(cs, int(args.get("depth", 3)), [int(args.get("max_nodes", 60))])
	st["recent_events"] = _events.slice(max(0, _events.size() - 15))
	return st


func _node_state(args: Dictionary) -> Dictionary:
	var n := get_tree().root.get_node_or_null(NodePath(str(args.get("path", ""))))
	if n == null and get_tree().current_scene != null:
		n = get_tree().current_scene.get_node_or_null(NodePath(str(args.get("path", ""))))
	if n == null:
		return {"__error": {"code": "NODE_NOT_FOUND", "message": "node not found: %s" % str(args.get("path"))}}
	return _entity(n)


func _screenshot(args: Dictionary) -> Dictionary:
	if DisplayServer.get_name() == "headless":
		return {"__error": {"code": "UNAVAILABLE", "message": "game runs headless; no rendering"}}
	var tex := get_viewport().get_texture()
	if tex == null:
		return {"__error": {"code": "UNAVAILABLE", "message": "viewport texture unavailable"}}
	var img := tex.get_image()
	if img == null or img.is_empty():
		return {"__error": {"code": "UNAVAILABLE", "message": "empty frame"}}
	var max_w := int(args.get("max_width", 960))
	if max_w > 0 and img.get_width() > max_w:
		img.resize(max_w, int(img.get_height() * float(max_w) / img.get_width()))
	var colors := {}
	for gx in range(24):
		for gy in range(14):
			var c := img.get_pixel(int((gx + 0.5) * img.get_width() / 24.0), int((gy + 0.5) * img.get_height() / 14.0))
			colors[c.to_html(false)] = true
	return {"width": img.get_width(), "height": img.get_height(), "distinct_colors": colors.size(),
			"png_base64": Marshalls.raw_to_base64(img.save_png_to_buffer())}


func _simulate_input(args: Dictionary) -> Dictionary:
	var steps: Array = args.get("steps", [])
	if steps.is_empty() and args.has("action"):
		steps = [args]
	for s in steps:
		var action := str(s.get("action", ""))
		if not InputMap.has_action(action):
			return {"__error": {"code": "UNKNOWN_ACTION", "message": "InputMap has no action '%s'" % action}}
		var hold_ms := int(s.get("hold_ms", 100))
		var press := InputEventAction.new()
		press.action = action
		press.pressed = true
		press.strength = float(s.get("strength", 1.0))
		Input.parse_input_event(press)
		await get_tree().create_timer(hold_ms / 1000.0, true, false, true).timeout
		var rel := InputEventAction.new()
		rel.action = action
		rel.pressed = false
		Input.parse_input_event(rel)
		var gap := int(s.get("wait_ms", 0))
		if gap > 0:
			await get_tree().create_timer(gap / 1000.0, true, false, true).timeout
	return {"done": steps.size()}
