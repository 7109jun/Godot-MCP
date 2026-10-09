@tool
extends RefCounted
## 길이 프리픽스(u32 big-endian) + JSON 프레임 기반 TCP 서버.
## - 127.0.0.1 전용, 첫 메시지(hello)에서 토큰 인증 필수
## - 요청 id / 프로토콜 버전 검증, 클라이언트당 요청은 순차 처리(동시 요청 보호)
## GDScript에는 AF_UNIX 소켓이 없어 loopback TCP + 토큰 인증을 사용한다.

const PROTOCOL_VERSION := 1
const MAX_MESSAGE := 32 * 1024 * 1024
const AUTH_TIMEOUT_MS := 5000

var role := "editor"

var _server := TCPServer.new()
var _clients: Array = []
var _token := ""
var _handler: Callable
var last_activity_ms := Time.get_ticks_msec()


func start(port: int, token: String, handler: Callable) -> Error:
	_token = token
	_handler = handler
	return _server.listen(port, "127.0.0.1")


func stop() -> void:
	for c in _clients:
		(c["peer"] as StreamPeerTCP).disconnect_from_host()
	_clients.clear()
	_server.stop()


func is_listening() -> bool:
	return _server.is_listening()


func poll() -> void:
	if not _server.is_listening():
		return
	while _server.is_connection_available():
		var p := _server.take_connection()
		p.set_no_delay(true)
		_clients.append({
			"peer": p, "buf": PackedByteArray(), "authed": false,
			"busy": false, "close": false, "t": Time.get_ticks_msec(),
		})
	var i := 0
	while i < _clients.size():
		var c: Dictionary = _clients[i]
		var peer: StreamPeerTCP = c["peer"]
		peer.poll()
		if peer.get_status() != StreamPeerTCP.STATUS_CONNECTED:
			_clients.remove_at(i)
			continue
		var n := peer.get_available_bytes()
		if n > 0:
			var r := peer.get_data(n)
			if r[0] == OK:
				var b: PackedByteArray = c["buf"]
				b.append_array(r[1])
				c["buf"] = b
		if not c["authed"] and Time.get_ticks_msec() - int(c["t"]) > AUTH_TIMEOUT_MS:
			c["close"] = true
		if not c["busy"] and not c["close"]:
			_process_client(c)
		if c["close"] and not c["busy"]:
			peer.disconnect_from_host()
			_clients.remove_at(i)
			continue
		i += 1


func _process_client(c: Dictionary) -> void:
	while not c["close"]:
		var buf: PackedByteArray = c["buf"]
		if buf.size() < 4:
			return
		var length: int = (buf[0] << 24) | (buf[1] << 16) | (buf[2] << 8) | buf[3]
		if length <= 0 or length > MAX_MESSAGE:
			c["close"] = true
			return
		if buf.size() < 4 + length:
			return
		var body := buf.slice(4, 4 + length)
		c["buf"] = buf.slice(4 + length)
		c["busy"] = true
		await _handle_message(c, body.get_string_from_utf8())
		c["busy"] = false


func _handle_message(c: Dictionary, text: String) -> void:
	var msg = JSON.parse_string(text)
	if typeof(msg) != TYPE_DICTIONARY:
		_send(c, _error(null, "BAD_REQUEST", "malformed JSON request"))
		c["close"] = true
		return
	var id = msg.get("id", null)
	if int(msg.get("v", 0)) != PROTOCOL_VERSION:
		_send(c, _error(id, "VERSION_MISMATCH", "protocol version %d required" % PROTOCOL_VERSION))
		c["close"] = true
		return
	var cmd := str(msg.get("cmd", ""))
	last_activity_ms = Time.get_ticks_msec()
	if not c["authed"]:
		if cmd != "hello" or not _token_equal(str(msg.get("auth", "")), _token):
			_send(c, _error(id, "UNAUTHORIZED", "invalid or missing token"))
			c["close"] = true
			return
		c["authed"] = true
		_send(c, {"v": PROTOCOL_VERSION, "id": id, "ok": true, "result": {
			"role": role,
			"godot": Engine.get_version_info().get("string", ""),
			"pid": OS.get_process_id(),
			"protocol": PROTOCOL_VERSION,
		}})
		return
	if cmd == "":
		_send(c, _error(id, "BAD_REQUEST", "missing cmd"))
		return
	var args = msg.get("args", {})
	if typeof(args) != TYPE_DICTIONARY:
		_send(c, _error(id, "BAD_REQUEST", "args must be an object"))
		return
	var result = await _handler.call(cmd, args)
	if typeof(result) == TYPE_DICTIONARY and result.has("__error"):
		var e: Dictionary = result["__error"]
		_send(c, {"v": PROTOCOL_VERSION, "id": id, "ok": false, "error": e})
	else:
		_send(c, {"v": PROTOCOL_VERSION, "id": id, "ok": true, "result": result})


func _error(id, code: String, message: String) -> Dictionary:
	return {"v": PROTOCOL_VERSION, "id": id, "ok": false, "error": {"code": code, "message": message}}


func _token_equal(a: String, b: String) -> bool:
	var x := a.to_utf8_buffer()
	var y := b.to_utf8_buffer()
	var diff := x.size() ^ y.size()
	for i in range(min(x.size(), y.size())):
		diff |= x[i] ^ y[i]
	return diff == 0 and b.length() > 0


func _send(c: Dictionary, payload: Dictionary) -> void:
	var peer: StreamPeerTCP = c["peer"]
	if peer.get_status() != StreamPeerTCP.STATUS_CONNECTED:
		return
	var body := JSON.stringify(payload).to_utf8_buffer()
	peer.big_endian = true
	peer.put_u32(body.size())
	peer.put_data(body)
