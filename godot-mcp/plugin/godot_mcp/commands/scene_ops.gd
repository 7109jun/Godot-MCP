@tool
extends RefCounted
## 씬 생성/수정 (선언적 노드 트리 스펙 -> PackedScene).
## 스펙: {name, type | instance, script, props, groups, unique, children[], connections[]}

const Value := preload("res://addons/godot_mcp/value_codec.gd")
const Paths := preload("res://addons/godot_mcp/paths.gd")

var warnings: Array = []
var _connections: Array = []


func _err(code: String, msg: String, extra: Dictionary = {}) -> Dictionary:
	var e := {"code": code, "message": msg}
	e.merge(extra)
	return {"__error": e}


func create_scene(args: Dictionary) -> Dictionary:
	var path := Paths.normalize(str(args.get("path", "")))
	if path == "" or Paths.is_protected(path) or not path.ends_with(".tscn"):
		return _err("INVALID_PATH", "scene path must be a res:// .tscn path inside the project")
	if ResourceLoader.exists(path) and not bool(args.get("overwrite", false)):
		return _err("SCENE_EXISTS", "scene already exists: %s (pass overwrite=true)" % path)
	var spec = args.get("root", null)
	if typeof(spec) != TYPE_DICTIONARY:
		return _err("BAD_REQUEST", "root node spec required")
	Value.warnings = []
	var root := _build(spec, null, null)
	if root == null:
		return _err("BUILD_FAILED", "could not build scene root", {"warnings": warnings + Value.warnings})
	_resolve_connections(root)
	var res := _save(root, path)
	root.free()
	if res.has("__error"):
		return res
	res["warnings"] = warnings + Value.warnings
	return res


func patch_scene(args: Dictionary) -> Dictionary:
	var path := Paths.normalize(str(args.get("path", "")))
	if path == "" or Paths.is_protected(path):
		return _err("INVALID_PATH", "invalid scene path")
	if not ResourceLoader.exists(path):
		return _err("SCENE_NOT_FOUND", "scene not found: %s" % path)
	var ps = ResourceLoader.load(path, "PackedScene", ResourceLoader.CACHE_MODE_REPLACE)
	if not (ps is PackedScene):
		return _err("SCENE_LOAD_FAILED", "cannot load scene: %s" % path)
	var root: Node = ps.instantiate(PackedScene.GEN_EDIT_STATE_MAIN)
	if root == null:
		return _err("SCENE_LOAD_FAILED", "cannot instantiate scene: %s" % path)
	Value.warnings = []
	var changes: Array = []
	var ops: Array = args.get("ops", [])
	for op in ops:
		var r := _apply_op(root, op, changes)
		if r.has("__error"):
			root.free()
			return r
	_resolve_connections(root)
	var res := _save(root, path)
	root.free()
	if res.has("__error"):
		return res
	res["changes"] = changes
	res["warnings"] = warnings + Value.warnings
	return res


func _find(root: Node, p: String) -> Node:
	if p == "." or p == "" or p == str(root.name):
		return root
	return root.get_node_or_null(NodePath(p))


func _apply_op(root: Node, op: Dictionary, changes: Array) -> Dictionary:
	match str(op.get("op", "")):
		"add":
			var parent := _find(root, str(op.get("parent", ".")))
			if parent == null:
				return _err("NODE_NOT_FOUND", "parent not found: %s" % str(op.get("parent")))
			var spec: Dictionary = op.get("node", {})
			var existing := parent.get_node_or_null(NodePath(str(spec.get("name", ""))))
			if existing != null:
				match str(op.get("if_exists", "skip")):
					"skip":
						changes.append({"op": "add", "node": str(spec.get("name")), "result": "already_present"})
						return {}
					"replace":
						parent.remove_child(existing)
						existing.free()
					_:
						return _err("NODE_EXISTS", "node already exists: %s" % str(spec.get("name")))
			var n := _build(spec, root, parent)
			if n == null:
				return _err("BUILD_FAILED", "could not build node %s" % str(spec.get("name")), {"warnings": warnings + Value.warnings})
			changes.append({"op": "add", "node": str(root.get_path_to(n)), "type": n.get_class(), "result": "added"})
		"set":
			var node := _find(root, str(op.get("node", ".")))
			if node == null:
				return _err("NODE_NOT_FOUND", "node not found: %s" % str(op.get("node")))
			Value.apply_props(node, op.get("props", {}), str(node.name))
			changes.append({"op": "set", "node": str(op.get("node")), "props": op.get("props", {}).keys()})
		"remove":
			var rn := _find(root, str(op.get("node", "")))
			if rn == null or rn == root:
				changes.append({"op": "remove", "node": str(op.get("node")), "result": "not_found"})
				return {}
			rn.get_parent().remove_child(rn)
			rn.free()
			changes.append({"op": "remove", "node": str(op.get("node")), "result": "removed"})
		"script":
			var sn := _find(root, str(op.get("node", ".")))
			if sn == null:
				return _err("NODE_NOT_FOUND", "node not found: %s" % str(op.get("node")))
			var sc = load(str(op.get("script", "")))
			if sc == null:
				return _err("SCRIPT_LOAD_FAILED", "cannot load script: %s" % str(op.get("script")))
			sn.set_script(sc)
			changes.append({"op": "script", "node": str(op.get("node")), "script": str(op.get("script"))})
		"groups":
			var gn := _find(root, str(op.get("node", ".")))
			if gn == null:
				return _err("NODE_NOT_FOUND", "node not found: %s" % str(op.get("node")))
			for g in op.get("groups", []):
				gn.add_to_group(str(g), true)
			changes.append({"op": "groups", "node": str(op.get("node")), "groups": op.get("groups", [])})
		_:
			return _err("BAD_OP", "unknown scene op '%s'" % str(op.get("op", "")))
	return {}


func _build(spec: Dictionary, root: Node, parent: Node) -> Node:
	var node_name := str(spec.get("name", ""))
	if node_name == "":
		warnings.append("node spec without name")
		return null
	var node: Node = null
	if spec.has("instance"):
		var ipath := Paths.normalize(str(spec["instance"]))
		var ips = load(ipath) if ipath != "" and ResourceLoader.exists(ipath) else null
		if not (ips is PackedScene):
			warnings.append("%s: cannot instance scene %s" % [node_name, str(spec["instance"])])
			return null
		node = ips.instantiate(PackedScene.GEN_EDIT_STATE_INSTANCE)
	else:
		var type := str(spec.get("type", "Node"))
		if not ClassDB.class_exists(type) or not ClassDB.can_instantiate(type) or not ClassDB.is_parent_class(type, "Node"):
			warnings.append("%s: unknown or non-instantiable node type '%s'" % [node_name, type])
			return null
		node = ClassDB.instantiate(type)
	node.name = node_name
	var eff_root := root if root != null else node
	if parent != null:
		parent.add_child(node)
		node.owner = eff_root
	if spec.has("script"):
		var spath := str(spec["script"])
		var sc = load(spath) if ResourceLoader.exists(spath) else null
		if sc == null:
			warnings.append("%s: cannot load script %s" % [node_name, spath])
		else:
			node.set_script(sc)
	Value.apply_props(node, spec.get("props", {}), node_name)
	for g in spec.get("groups", []):
		node.add_to_group(str(g), true)
	if bool(spec.get("unique", false)):
		node.unique_name_in_owner = true
	for conn in spec.get("connections", []):
		_connections.append({"from": node, "conn": conn})
	for child_spec in spec.get("children", []):
		if _build(child_spec, eff_root, node) == null:
			warnings.append("child of %s skipped" % node_name)
	return node


func _resolve_connections(root: Node) -> void:
	for item in _connections:
		var from: Node = item["from"]
		var conn: Dictionary = item["conn"]
		var target := _find(root, str(conn.get("target", ".")))
		if target == null:
			warnings.append("connection target not found: %s" % str(conn.get("target")))
			continue
		var sig := str(conn.get("signal", ""))
		var method := str(conn.get("method", ""))
		if not from.has_signal(sig):
			warnings.append("%s has no signal '%s'" % [str(from.name), sig])
			continue
		var cb := Callable(target, method)
		if not from.is_connected(sig, cb):
			from.connect(sig, cb, CONNECT_PERSIST)
	_connections.clear()


func _save(root: Node, path: String) -> Dictionary:
	var ps := PackedScene.new()
	var err := ps.pack(root)
	if err != OK:
		return _err("PACK_FAILED", "PackedScene.pack failed (err %d)" % err)
	DirAccess.make_dir_recursive_absolute(ProjectSettings.globalize_path(path.get_base_dir()))
	err = ResourceSaver.save(ps, path)
	if err != OK:
		return _err("SAVE_FAILED", "ResourceSaver.save failed (err %d) for %s" % [err, path])
	ResourceLoader.load(path, "PackedScene", ResourceLoader.CACHE_MODE_REPLACE)
	return {"path": path, "saved": true, "root": str(root.name), "node_count": _count(root)}


func _count(n: Node) -> int:
	var c := 1
	for ch in n.get_children():
		c += _count(ch)
	return c
