@tool
extends RefCounted
## IPC 명령 라우터. (이 명령들은 서버 내부용이며 AI에게는 노출되지 않는다.)

const SceneOps := preload("res://addons/godot_mcp/commands/scene_ops.gd")
const ProjectOps := preload("res://addons/godot_mcp/commands/project_ops.gd")
const InspectOps := preload("res://addons/godot_mcp/commands/inspect_ops.gd")
const ValidateOps := preload("res://addons/godot_mcp/commands/validate_ops.gd")
const EditorOps := preload("res://addons/godot_mcp/commands/editor_ops.gd")

var _plugin: EditorPlugin


func _init(plugin: EditorPlugin) -> void:
	_plugin = plugin


func handle(cmd: String, args: Dictionary) -> Dictionary:
	match cmd:
		"ping":
			return {"pong": true, "time_ms": Time.get_ticks_msec()}
		"info":
			return EditorOps.info()
		"scan":
			return await EditorOps.scan(_plugin)
		"save_all":
			return EditorOps.save_all()
		"editor_screenshot":
			return await EditorOps.screenshot(args, _plugin)
		"quit":
			_plugin.get_tree().call_deferred("quit")
			return {"quitting": true}
		"create_scene":
			return SceneOps.new().create_scene(args)
		"patch_scene":
			return SceneOps.new().patch_scene(args)
		"inspect_scene":
			return InspectOps.inspect_scene(args)
		"class_info":
			return InspectOps.class_info(args)
		"set_settings":
			return ProjectOps.set_settings(args)
		"get_settings":
			return ProjectOps.get_settings(args)
		"add_input_actions":
			return ProjectOps.add_input_actions(args)
		"list_input_actions":
			return ProjectOps.list_input_actions()
		"set_autoloads":
			return await ProjectOps.set_autoloads(args, _plugin)
		"check_resources":
			return ValidateOps.check_resources(args)
	return {"__error": {"code": "UNKNOWN_COMMAND", "message": "unknown command '%s'" % cmd}}
