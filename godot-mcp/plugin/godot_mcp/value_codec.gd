@tool
extends RefCounted
## JSON <-> Variant 변환. 속성 타입에 맞춰 값을 강제 변환(coerce)하므로
## 서버는 [100, 200] / "#ff0000" / "res://a.png" 같은 단순 JSON만 보내면 된다.
## 태그 형식: {"_t": "Resource", "type": "RectangleShape2D", "props": {...}}

static var warnings: Array = []


static func decode(v):
	match typeof(v):
		TYPE_DICTIONARY:
			if v.has("_t"):
				return _decode_tagged(v)
			var d := {}
			for k in v:
				d[k] = decode(v[k])
			return d
		TYPE_ARRAY:
			var a := []
			for x in v:
				a.append(decode(x))
			return a
		_:
			return v


static func _decode_tagged(v: Dictionary):
	var a = v.get("v", [])
	match str(v["_t"]):
		"Vector2": return Vector2(a[0], a[1])
		"Vector2i": return Vector2i(int(a[0]), int(a[1]))
		"Vector3": return Vector3(a[0], a[1], a[2])
		"Color":
			if v.has("hex"):
				return Color.from_string(str(v["hex"]), Color.MAGENTA)
			return Color(a[0], a[1], a[2], a[3] if a.size() > 3 else 1.0)
		"Rect2": return Rect2(a[0], a[1], a[2], a[3])
		"NodePath": return NodePath(str(v.get("v", "")))
		"StringName": return StringName(str(v.get("v", "")))
		"Load":
			var path := str(v.get("path", ""))
			if not ResourceLoader.exists(path):
				warnings.append("Load: resource not found: %s" % path)
				return null
			return load(path)
		"Resource":
			var type := str(v.get("type", ""))
			if not ClassDB.class_exists(type) or not ClassDB.can_instantiate(type):
				warnings.append("Resource: cannot instantiate '%s'" % type)
				return null
			var res = ClassDB.instantiate(type)
			apply_props(res, v.get("props", {}), "resource " + type)
			return res
		"PackedVector2Array":
			var pv := PackedVector2Array()
			for i in range(0, a.size() - 1, 2):
				pv.append(Vector2(a[i], a[i + 1]))
			return pv
		"PackedStringArray":
			return PackedStringArray(a)
	warnings.append("unknown value tag '%s'" % str(v["_t"]))
	return null


static func coerce(v, info: Dictionary):
	if typeof(v) == TYPE_DICTIONARY and v.has("_t"):
		return decode(v)
	var t: int = info.get("type", TYPE_NIL)
	match t:
		TYPE_INT:
			if typeof(v) in [TYPE_FLOAT, TYPE_INT, TYPE_BOOL]:
				return int(v)
		TYPE_FLOAT:
			if typeof(v) in [TYPE_FLOAT, TYPE_INT, TYPE_BOOL]:
				return float(v)
		TYPE_BOOL:
			return bool(v)
		TYPE_STRING:
			return str(v)
		TYPE_STRING_NAME:
			return StringName(str(v))
		TYPE_NODE_PATH:
			return NodePath(str(v))
		TYPE_VECTOR2:
			if typeof(v) == TYPE_ARRAY and v.size() >= 2:
				return Vector2(v[0], v[1])
		TYPE_VECTOR2I:
			if typeof(v) == TYPE_ARRAY and v.size() >= 2:
				return Vector2i(int(v[0]), int(v[1]))
		TYPE_VECTOR3:
			if typeof(v) == TYPE_ARRAY and v.size() >= 3:
				return Vector3(v[0], v[1], v[2])
		TYPE_COLOR:
			if typeof(v) == TYPE_STRING:
				return Color.from_string(v, Color.MAGENTA)
			if typeof(v) == TYPE_ARRAY and v.size() >= 3:
				return Color(v[0], v[1], v[2], v[3] if v.size() > 3 else 1.0)
		TYPE_RECT2:
			if typeof(v) == TYPE_ARRAY and v.size() >= 4:
				return Rect2(v[0], v[1], v[2], v[3])
		TYPE_PACKED_VECTOR2_ARRAY:
			if typeof(v) == TYPE_ARRAY:
				var pv := PackedVector2Array()
				for item in v:
					if typeof(item) == TYPE_ARRAY and item.size() >= 2:
						pv.append(Vector2(item[0], item[1]))
				return pv
		TYPE_PACKED_STRING_ARRAY:
			if typeof(v) == TYPE_ARRAY:
				return PackedStringArray(v)
		TYPE_OBJECT:
			if typeof(v) == TYPE_STRING and v.begins_with("res://"):
				if not ResourceLoader.exists(v):
					warnings.append("resource not found: %s" % v)
					return null
				return load(v)
		TYPE_ARRAY:
			return decode(v)
	return v


static func property_infos(obj: Object) -> Dictionary:
	var out := {}
	for p in obj.get_property_list():
		out[p["name"]] = p
	return out


static func apply_props(obj: Object, props: Dictionary, ctx: String) -> void:
	var infos := property_infos(obj)
	for k in props:
		var key := str(k)
		if infos.has(key):
			obj.set(key, coerce(props[k], infos[key]))
		elif key.contains("/"):
			obj.set(key, decode(props[k]))
		else:
			warnings.append("%s: unknown property '%s' on %s" % [ctx, key, obj.get_class()])


static func encode(v, depth: int = 0):
	match typeof(v):
		TYPE_NIL, TYPE_BOOL, TYPE_INT, TYPE_STRING:
			return v
		TYPE_FLOAT:
			return v if is_finite(v) else null
		TYPE_VECTOR2, TYPE_VECTOR2I:
			return [encode(v.x), encode(v.y)]
		TYPE_VECTOR3, TYPE_VECTOR3I:
			return [encode(v.x), encode(v.y), encode(v.z)]
		TYPE_COLOR:
			return "#" + v.to_html(v.a < 1.0)
		TYPE_RECT2:
			return [v.position.x, v.position.y, v.size.x, v.size.y]
		TYPE_STRING_NAME, TYPE_NODE_PATH:
			return str(v)
		TYPE_OBJECT:
			if v == null:
				return null
			if v is Resource:
				if v.resource_path != "":
					return v.resource_path
				return "<%s>" % v.get_class()
			return "<%s>" % v.get_class()
		TYPE_ARRAY:
			if depth > 3:
				return "<array>"
			var a := []
			for x in v:
				a.append(encode(x, depth + 1))
			return a
		TYPE_DICTIONARY:
			if depth > 3:
				return "<dict>"
			var d := {}
			for k in v:
				d[str(k)] = encode(v[k], depth + 1)
			return d
		TYPE_PACKED_VECTOR2_ARRAY:
			var pv := []
			for x in v:
				pv.append([x.x, x.y])
			return pv
		_:
			return str(v)
