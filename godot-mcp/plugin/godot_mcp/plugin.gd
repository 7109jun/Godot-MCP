@tool
extends EditorPlugin
## Godot MCP 에디터 플러그인: 세션 파일을 읽고 로컬 IPC 서버를 연다.

const IpcServer := preload("res://addons/godot_mcp/ipc_server.gd")
const Router := preload("res://addons/godot_mcp/commands/router.gd")

var _ipc
var _router
var _idle_quit_s := 300


func _enter_tree() -> void:
	var session := _load_session()
	if session.is_empty():
		push_warning("[godot_mcp] session file not found; plugin idle")
		return
	_router = Router.new(self)
	_ipc = IpcServer.new()
	_ipc.role = "editor"
	var err: Error = _ipc.start(int(session["editor_port"]), str(session["token"]), Callable(_router, "handle"))
	if err != OK:
		push_error("[godot_mcp] cannot listen on port %s (err %d)" % [str(session["editor_port"]), err])
		_ipc = null
		return
	_idle_quit_s = int(session.get("editor_idle_s", 300))
	set_process(true)
	print("[godot_mcp] editor IPC listening on 127.0.0.1:%d" % int(session["editor_port"]))


func _process(_delta: float) -> void:
	if _ipc != null:
		_ipc.poll()
		# 방치된 에디터가 메모리/CPU 를 계속 점유하지 않도록 유휴 시 스스로 종료한다 (서버가 필요하면 다시 띄운다)
		if _idle_quit_s > 0 and Time.get_ticks_msec() - _ipc.last_activity_ms > _idle_quit_s * 1000:
			print("[godot_mcp] idle for %ds; quitting editor" % _idle_quit_s)
			_ipc.stop()
			_ipc = null
			get_tree().quit()


func _exit_tree() -> void:
	if _ipc != null:
		_ipc.stop()
		_ipc = null


func _load_session() -> Dictionary:
	var path := OS.get_environment("GODOT_MCP_SESSION")
	if path == "":
		path = ProjectSettings.globalize_path("res://.godot_mcp/session.json")
	if not FileAccess.file_exists(path):
		return {}
	var parsed = JSON.parse_string(FileAccess.get_file_as_string(path))
	if typeof(parsed) != TYPE_DICTIONARY or not parsed.has("editor_port") or not parsed.has("token"):
		return {}
	return parsed
