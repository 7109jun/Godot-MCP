@tool
extends RefCounted
## 에디터 수준 작업: 파일시스템 스캔, 저장, 에디터 스크린샷, 정보.


static func info() -> Dictionary:
	return {
		"godot": Engine.get_version_info().get("string", ""),
		"project_name": str(ProjectSettings.get_setting("application/config/name", "")),
		"main_scene": str(ProjectSettings.get_setting("application/run/main_scene", "")),
		"display_driver": DisplayServer.get_name(),
		"project_path": ProjectSettings.globalize_path("res://"),
		"pid": OS.get_process_id(),
	}


static func scan(plugin: EditorPlugin) -> Dictionary:
	var fs := EditorInterface.get_resource_filesystem()
	var tree := plugin.get_tree()
	var done := [false]
	var on_changed := func(): done[0] = true
	fs.filesystem_changed.connect(on_changed, CONNECT_ONE_SHOT)
	if not fs.is_scanning():
		fs.scan()
	var guard := 0
	# 변경이 없으면 filesystem_changed 가 발생하지 않으므로: 신호가 오거나, 스캔이 끝난 뒤 충분히 기다렸으면 종료
	while not done[0] and guard < 1800:
		await tree.process_frame
		guard += 1
		if guard > 40 and not fs.is_scanning():
			break
	if fs.filesystem_changed.is_connected(on_changed):
		fs.filesystem_changed.disconnect(on_changed)
	# update_scripts_classes 등 후속 작업이 끝나도록 대기
	for i in range(10):
		await tree.process_frame
	return {"scanned": done[0], "frames": guard}


static func save_all() -> Dictionary:
	EditorInterface.save_all_scenes()
	return {"saved": true}


static func screenshot(args: Dictionary, plugin: EditorPlugin) -> Dictionary:
	if DisplayServer.get_name() == "headless":
		return {"__error": {"code": "UNAVAILABLE", "message": "editor runs headless; no editor screenshot possible"}}
	var tree := plugin.get_tree()
	var fs := EditorInterface.get_resource_filesystem()
	var guard := 0
	while fs.is_scanning() and guard < 900:
		await tree.process_frame
		guard += 1
	var scene := str(args.get("scene", ""))
	if scene == "":
		scene = str(ProjectSettings.get_setting("application/run/main_scene", ""))
	if scene != "" and ResourceLoader.exists(scene):
		EditorInterface.open_scene_from_path(scene)
	for i in range(60):  # 로딩 다이얼로그/뷰포트가 안정되도록 대기
		await tree.process_frame
	var img := EditorInterface.get_base_control().get_viewport().get_texture().get_image()
	if img == null or img.is_empty():
		return {"__error": {"code": "UNAVAILABLE", "message": "editor viewport has no image"}}
	var max_w := int(args.get("max_width", 1280))
	if img.get_width() > max_w:
		img.resize(max_w, int(img.get_height() * float(max_w) / img.get_width()))
	return {"width": img.get_width(), "height": img.get_height(), "scene": scene, "png_base64": Marshalls.raw_to_base64(img.save_png_to_buffer())}
