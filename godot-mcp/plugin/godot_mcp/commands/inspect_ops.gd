@tool
extends RefCounted
## 씬 구조/클래스 정보 조회 (VIEW 계층용).

const Value := preload("res://addons/godot_mcp/value_codec.gd")
const Paths := preload("res://addons/godot_mcp/paths.gd")

const SKIP_PROPS := ["script", "owner", "name", "scene_file_path", "multiplayer", "process_priority", "process_physics_priority", "unique_name_in_owner"]


static func _err(code: String, msg: String) -> Dictionary:
	return {"__error": {"code": code, "message": msg}}


static func inspect_scene(args: Dictionary) -> Dictionary:
	var path := Paths.normalize(str(args.get("path", "")))
	if path == "":
		return _err("INVALID_PATH", "invalid scene path")
	if not ResourceLoader.exists(path):
		return _err("SCENE_NOT_FOUND", "scene not found: %s" % path)
	var ps = ResourceLoader.load(path, "PackedScene", ResourceLoader.CACHE_MODE_REPLACE)
	if not (ps is PackedScene):
		return _err("SCENE_LOAD_FAILED", "cannot load scene: %s" % path)
	var root: Node = ps.instantiate()
	if root == null:
		return _err("SCENE_LOAD_FAILED", "cannot instantiate: %s" % path)
	var max_depth := int(args.get("depth", 6))
	var with_props := bool(args.get("props", true))
	var tree := _node_dict(root, root, 0, max_depth, with_props)
	var count := _count(root)
	root.free()
	return {"path": path, "node_count": count, "tree": tree}


static func _count(n: Node) -> int:
	var c := 1
	for ch in n.get_children():
		c += _count(ch)
	return c


static func _node_dict(node: Node, root: Node, depth: int, max_depth: int, with_props: bool) -> Dictionary:
	var d := {"name": str(node.name), "type": node.get_class()}
	if node != root:
		d["path"] = str(root.get_path_to(node))
		if node.scene_file_path != "":
			d["instance"] = node.scene_file_path
	var sc = node.get_script()
	if sc != null and sc is Script:
		d["script"] = (sc as Script).resource_path
	var groups: Array = []
	for g in node.get_groups():
		if not str(g).begins_with("_"):
			groups.append(str(g))
	if not groups.is_empty():
		d["groups"] = groups
	if with_props:
		var props := _changed_props(node)
		if not props.is_empty():
			d["props"] = props
	var kids: Array = []
	if depth < max_depth and (node == root or node.scene_file_path == ""):
		for ch in node.get_children():
			kids.append(_node_dict(ch, root, depth + 1, max_depth, with_props))
	if not kids.is_empty():
		d["children"] = kids
	return d


static func _changed_props(node: Node) -> Dictionary:
	var out := {}
	var cls := node.get_class()
	var script = node.get_script()
	for p in node.get_property_list():
		var pname: String = p["name"]
		var usage: int = p["usage"]
		if not (usage & PROPERTY_USAGE_STORAGE) or SKIP_PROPS.has(pname) or pname.begins_with("metadata/"):
			continue
		var value = node.get(pname)
		var default = null
		var has_default := false
		if usage & PROPERTY_USAGE_SCRIPT_VARIABLE and script != null:
			default = (script as Script).get_property_default_value(pname)
			has_default = true
		elif ClassDB.class_has_method(cls, "get") or true:
			default = ClassDB.class_get_property_default_value(cls, pname)
			has_default = true
		var differs := true
		if has_default and typeof(value) == typeof(default) and typeof(value) != TYPE_OBJECT:
			differs = value != default
		elif typeof(value) == TYPE_OBJECT and value == null:
			differs = false
		if differs:
			out[pname] = Value.encode(value)
		if out.size() >= 24:
			break
	return out


static func class_info(args: Dictionary) -> Dictionary:
	var cls := str(args.get("class", ""))
	if not ClassDB.class_exists(cls):
		return {"exists": false, "class": cls}
	var props: Array = []
	for p in ClassDB.class_get_property_list(cls):
		if not str(p["name"]).begins_with("_") and not (int(p["usage"]) & PROPERTY_USAGE_CATEGORY) and not (int(p["usage"]) & PROPERTY_USAGE_GROUP):
			props.append(str(p["name"]))
	var methods: Array = []
	for m in ClassDB.class_get_method_list(cls):
		if not str(m["name"]).begins_with("_"):
			methods.append(str(m["name"]))
	var sigs: Array = []
	for s in ClassDB.class_get_signal_list(cls):
		sigs.append(str(s["name"]))
	return {"exists": true, "class": cls, "parent": str(ClassDB.get_parent_class(cls)), "properties": props, "methods": methods, "signals": sigs}
