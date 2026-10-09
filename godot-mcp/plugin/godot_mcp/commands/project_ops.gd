@tool
extends RefCounted
## ProjectSettings / InputMap / Autoload 조작. project.godot 쓰기는 항상 이 경로로만 수행한다.

const Value := preload("res://addons/godot_mcp/value_codec.gd")


static func _err(code: String, msg: String) -> Dictionary:
	return {"__error": {"code": code, "message": msg}}


static func set_settings(args: Dictionary) -> Dictionary:
	var applied := {}
	var settings: Dictionary = args.get("settings", {})
	for key in settings:
		var k := str(key)
		if k.begins_with("input/") or k.begins_with("autoload/") or k.begins_with("editor_plugins/"):
			return _err("RESTRICTED_SETTING", "use dedicated commands for '%s'" % k)
		var v = settings[key]
		if ProjectSettings.has_setting(k):
			var cur = ProjectSettings.get_setting(k)
			v = Value.coerce(v, {"type": typeof(cur)})
		else:
			v = Value.decode(v)
		ProjectSettings.set_setting(k, v)
		applied[k] = Value.encode(v)
	var err := ProjectSettings.save()
	if err != OK:
		return _err("SAVE_FAILED", "ProjectSettings.save failed (err %d)" % err)
	return {"applied": applied}


static func get_settings(args: Dictionary) -> Dictionary:
	var out := {}
	for key in args.get("keys", []):
		var k := str(key)
		out[k] = Value.encode(ProjectSettings.get_setting(k)) if ProjectSettings.has_setting(k) else null
	return {"settings": out}


static func _make_event(e: Dictionary) -> InputEvent:
	match str(e.get("type", "key")):
		"key":
			var ev := InputEventKey.new()
			var code := OS.find_keycode_from_string(str(e.get("key", "")))
			if code == KEY_NONE:
				return null
			ev.physical_keycode = code
			return ev
		"mouse_button":
			var mb := InputEventMouseButton.new()
			mb.button_index = int(e.get("index", 1))
			return mb
		"joypad_button":
			var jb := InputEventJoypadButton.new()
			jb.button_index = int(e.get("index", 0))
			return jb
		"joypad_axis":
			var ja := InputEventJoypadMotion.new()
			ja.axis = int(e.get("axis", 0))
			ja.axis_value = float(e.get("value", 1.0))
			return ja
	return null


static func add_input_actions(args: Dictionary) -> Dictionary:
	var added: Array = []
	var existing: Array = []
	var skipped: Array = []
	var actions: Dictionary = args.get("actions", {})
	for name in actions:
		var key := "input/" + str(name)
		var def: Dictionary = actions[name]
		if ProjectSettings.has_setting(key) and not bool(args.get("replace", false)):
			existing.append(str(name))
			continue
		var events: Array = []
		for e in def.get("events", []):
			var ev := _make_event(e)
			if ev == null:
				skipped.append("%s: invalid event %s" % [str(name), str(e)])
				continue
			events.append(ev)
		ProjectSettings.set_setting(key, {"deadzone": float(def.get("deadzone", 0.5)), "events": events})
		if InputMap.has_action(str(name)):
			InputMap.erase_action(str(name))
		InputMap.add_action(str(name), float(def.get("deadzone", 0.5)))
		for ev in events:
			InputMap.action_add_event(str(name), ev)
		added.append(str(name))
	var err := ProjectSettings.save()
	if err != OK:
		return _err("SAVE_FAILED", "ProjectSettings.save failed (err %d)" % err)
	return {"added": added, "existing": existing, "skipped": skipped}


static func list_input_actions() -> Dictionary:
	var out := {}
	for p in ProjectSettings.get_property_list():
		var pname: String = p["name"]
		if pname.begins_with("input/"):
			var def = ProjectSettings.get_setting(pname)
			var evs: Array = []
			if typeof(def) == TYPE_DICTIONARY:
				for ev in def.get("events", []):
					evs.append(ev.as_text())
			out[pname.substr(6)] = evs
	return {"actions": out}


static func set_autoloads(args: Dictionary, plugin: EditorPlugin) -> Dictionary:
	var changed: Array = []
	var autoloads: Dictionary = args.get("autoloads", {})
	for name in autoloads:
		var key := "autoload/" + str(name)
		var path = autoloads[name]
		if path == null or str(path) == "":
			if ProjectSettings.has_setting(key):
				ProjectSettings.clear(key)
				changed.append("-" + str(name))
		else:
			var val := "*" + str(path)
			if not ProjectSettings.has_setting(key) or str(ProjectSettings.get_setting(key)) != val:
				ProjectSettings.set_setting(key, val)
				changed.append("+" + str(name))
	var err := ProjectSettings.save()
	if err != OK:
		return _err("SAVE_FAILED", "ProjectSettings.save failed (err %d)" % err)
	# 에디터가 autoload 싱글톤을 GDScript 전역으로 등록하는 것은 다음 프레임들에 지연 처리된다.
	for i in range(6):
		await plugin.get_tree().process_frame
	return {"changed": changed}
