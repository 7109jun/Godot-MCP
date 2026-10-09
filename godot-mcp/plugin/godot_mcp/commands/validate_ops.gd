@tool
extends RefCounted
## 엔진을 통한 실제 리소스/씬 로드 검증.

const Paths := preload("res://addons/godot_mcp/paths.gd")


static func check_resources(args: Dictionary) -> Dictionary:
	var results: Array = []
	for p in args.get("paths", []):
		var path := Paths.normalize(str(p))
		var entry := {"path": str(p), "loadable": false, "instantiable": false, "missing_deps": []}
		if path == "" or not ResourceLoader.exists(path):
			entry["error"] = "not found"
			results.append(entry)
			continue
		var deps := ResourceLoader.get_dependencies(path)
		for dep in deps:
			var dpath := str(dep)
			if dpath.contains("::"):
				var segs := dpath.split("::")
				dpath = segs[segs.size() - 1] if segs[segs.size() - 1] != "" else segs[0]
			if dpath.begins_with("res://") and not (ResourceLoader.exists(dpath) or FileAccess.file_exists(dpath)):
				(entry["missing_deps"] as Array).append(dpath)
		var res = ResourceLoader.load(path, "", ResourceLoader.CACHE_MODE_IGNORE)
		if res == null:
			entry["error"] = "load failed"
			results.append(entry)
			continue
		entry["loadable"] = true
		entry["class"] = res.get_class()
		if res is PackedScene:
			var inst = (res as PackedScene).instantiate()
			if inst != null:
				entry["instantiable"] = true
				entry["root"] = str(inst.name)
				inst.free()
			else:
				entry["error"] = "instantiate failed"
		else:
			entry["instantiable"] = true
		results.append(entry)
	return {"results": results}
