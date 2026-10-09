"""게임 템플릿 GDScript 소스. (모두 Godot 실제 파서로 검증된다.)"""

GAME_STATE = '''extends Node
## 전역 게임 상태 (autoload). 이벤트는 MCP game_state() 의 recent_events 로 관찰된다.

signal event(event_name: String, data: Variant)
signal coins_changed(total: int)

var coins: int = 0
var score: int = 0


func add_coin(value: int = 1) -> void:
	coins += value
	score += value * 10
	coins_changed.emit(coins)
	emit_game_event("coin_collected", {"coins": coins})


func emit_game_event(event_name: String, data: Variant = null) -> void:
	event.emit(event_name, data)


func reset() -> void:
	coins = 0
	score = 0
	coins_changed.emit(coins)
'''

MAIN = '''extends Node2D
## 메인 씬 루트. 게임 시작 시 전역 상태를 초기화한다.


func _ready() -> void:
	GameState.reset()
'''

MAIN_3D = MAIN.replace("extends Node2D", "extends Node3D")

PLAYER_PLATFORMER = '''extends CharacterBody2D
## 플레이어 이동 (platformer). 메카닉 컴포넌트(DoubleJump, Dash, Health ...)는 자식 노드로 붙는다.

signal jumped

@export var speed: float = 260.0
@export var acceleration: float = 1800.0
@export var friction: float = 2200.0
@export var jump_velocity: float = -520.0
@export var gravity_scale: float = 1.0

var facing: int = 1
var input_locked: bool = false
var gravity_enabled: bool = true
var last_jump_frame: int = -1

var _gravity: float = ProjectSettings.get_setting("physics/2d/default_gravity", 980.0)


func _physics_process(delta: float) -> void:
	if gravity_enabled and not is_on_floor():
		velocity.y += _gravity * gravity_scale * delta
	if not input_locked:
		var dir := Input.get_axis("move_left", "move_right")
		if dir != 0.0:
			velocity.x = move_toward(velocity.x, dir * speed, acceleration * delta)
			facing = 1 if dir > 0.0 else -1
		else:
			velocity.x = move_toward(velocity.x, 0.0, friction * delta)
		if Input.is_action_just_pressed("jump") and is_on_floor():
			velocity.y = jump_velocity
			last_jump_frame = Engine.get_physics_frames()
			jumped.emit()
	move_and_slide()
'''

PLAYER_TOPDOWN = '''extends CharacterBody2D
## 플레이어 이동 (top-down, 8방향). 메카닉 컴포넌트는 자식 노드로 붙는다.

@export var speed: float = 220.0
@export var acceleration: float = 1800.0
@export var friction: float = 2200.0

var facing: Vector2 = Vector2.DOWN
var input_locked: bool = false


func _physics_process(delta: float) -> void:
	if not input_locked:
		var dir := Input.get_vector("move_left", "move_right", "move_up", "move_down")
		if dir != Vector2.ZERO:
			velocity = velocity.move_toward(dir * speed, acceleration * delta)
			facing = dir.normalized()
		else:
			velocity = velocity.move_toward(Vector2.ZERO, friction * delta)
	move_and_slide()
'''

PLAYER_3D = '''extends CharacterBody3D
## 플레이어 이동 (3D, WASD + 점프). 카메라는 자식 Camera3D.

@export var speed: float = 6.0
@export var acceleration: float = 30.0
@export var jump_velocity: float = 6.5

var input_locked: bool = false
var _gravity: float = ProjectSettings.get_setting("physics/3d/default_gravity", 9.8)


func _physics_process(delta: float) -> void:
	if not is_on_floor():
		velocity.y -= _gravity * delta
	if not input_locked:
		var dir := Input.get_vector("move_left", "move_right", "move_up", "move_down")
		var target := Vector3(dir.x, 0.0, dir.y) * speed
		velocity.x = move_toward(velocity.x, target.x, acceleration * delta)
		velocity.z = move_toward(velocity.z, target.z, acceleration * delta)
		if Input.is_action_just_pressed("jump") and is_on_floor():
			velocity.y = jump_velocity
	move_and_slide()
'''

HUD = '''extends CanvasLayer
## HUD: 메카닉이 추가한 라벨(CoinLabel/HealthLabel/ItemsLabel)을 찾아 갱신한다. 없으면 무시.

@onready var _stats: VBoxContainer = $Stats


func _ready() -> void:
	GameState.coins_changed.connect(_on_coins_changed)
	_on_coins_changed(GameState.coins)
	call_deferred("_bind_player")


func _label(label_name: String) -> Label:
	return _stats.get_node_or_null(label_name) as Label


func _on_coins_changed(total: int) -> void:
	var label := _label("CoinLabel")
	if label:
		label.text = "Coins: %d" % total


func _bind_player() -> void:
	var player = get_tree().get_first_node_in_group("player")
	if player == null:
		return
	var health = player.get_node_or_null("Health")
	if health != null:
		health.health_changed.connect(_on_health_changed)
		_on_health_changed(health.current, health.max_health)
	var inv = player.get_node_or_null("Inventory")
	if inv != null:
		inv.changed.connect(_on_inventory_changed)
		_on_inventory_changed(inv.items)


func _on_health_changed(current: int, maximum: int) -> void:
	var label := _label("HealthLabel")
	if label:
		label.text = "HP: %d / %d" % [current, maximum]


func _on_inventory_changed(items: Dictionary) -> void:
	var label := _label("ItemsLabel")
	if label:
		var parts: Array = []
		for k in items:
			parts.append("%s x%d" % [k, items[k]])
		label.text = "Items: " + (", ".join(parts) if not parts.is_empty() else "-")
'''

DOUBLE_JUMP = '''extends Node
## 이중 점프 컴포넌트: Player 의 자식으로 붙는다. 공중에서 extra_jumps 만큼 추가 점프.

signal double_jumped

@export var extra_jumps: int = 1
@export var jump_velocity: float = -480.0

var _left: int = 0
@onready var _p = get_parent()


func _physics_process(_delta: float) -> void:
	if _p.is_on_floor():
		_left = extra_jumps
	elif Input.is_action_just_pressed("jump") and _left > 0 and not _p.input_locked and Engine.get_physics_frames() != _p.last_jump_frame:
		_p.velocity.y = jump_velocity
		_left -= 1
		double_jumped.emit()
		GameState.emit_game_event("double_jump", {"left": _left})
'''

DASH_PLATFORMER = '''extends Node
## 대시 컴포넌트 (platformer): 바라보는 방향으로 짧게 돌진. 쿨다운 있음.

signal dashed

@export var dash_speed: float = 720.0
@export var dash_time: float = 0.14
@export var cooldown: float = 0.7

var _time_left: float = 0.0
var _cd: float = 0.0
var _dir: float = 1.0
@onready var _p = get_parent()


func _physics_process(delta: float) -> void:
	_cd = maxf(0.0, _cd - delta)
	if _time_left > 0.0:
		_time_left -= delta
		_p.velocity.x = _dir * dash_speed
		_p.velocity.y = 0.0
		if _time_left <= 0.0:
			_p.input_locked = false
			_p.gravity_enabled = true
		return
	if Input.is_action_just_pressed("dash") and _cd <= 0.0 and not _p.input_locked:
		_dir = float(_p.facing)
		_time_left = dash_time
		_cd = cooldown
		_p.input_locked = true
		_p.gravity_enabled = false
		dashed.emit()
		GameState.emit_game_event("dash", {"dir": _dir})
'''

DASH_TOPDOWN = '''extends Node
## 대시 컴포넌트 (top-down): 바라보는 방향으로 짧게 돌진. 쿨다운 있음.

signal dashed

@export var dash_speed: float = 640.0
@export var dash_time: float = 0.14
@export var cooldown: float = 0.7

var _time_left: float = 0.0
var _cd: float = 0.0
var _dir: Vector2 = Vector2.RIGHT
@onready var _p = get_parent()


func _physics_process(delta: float) -> void:
	_cd = maxf(0.0, _cd - delta)
	if _time_left > 0.0:
		_time_left -= delta
		_p.velocity = _dir * dash_speed
		if _time_left <= 0.0:
			_p.input_locked = false
		return
	if Input.is_action_just_pressed("dash") and _cd <= 0.0 and not _p.input_locked:
		_dir = _p.facing
		_time_left = dash_time
		_cd = cooldown
		_p.input_locked = true
		dashed.emit()
		GameState.emit_game_event("dash", {"dir": [_dir.x, _dir.y]})
'''

HEALTH = '''extends Node
## 체력 컴포넌트: Player/Enemy 의 자식. damage()/heal(). 플레이어 사망 시 씬 재시작.

signal health_changed(current: int, maximum: int)
signal damaged(amount: int)
signal died

@export var max_health: int = 5
@export var invulnerable_time: float = 0.8
@export var restart_on_death: bool = true

var current: int = 0
var _inv: float = 0.0


func _ready() -> void:
	current = max_health
	health_changed.emit(current, max_health)


func _process(delta: float) -> void:
	_inv = maxf(0.0, _inv - delta)
	var item := get_parent() as CanvasItem
	if item != null:
		item.modulate.a = 0.45 if (_inv > 0.0 and int(_inv * 20.0) % 2 == 0) else 1.0


func damage(amount: int = 1) -> bool:
	if current <= 0 or _inv > 0.0:
		return false
	current = maxi(0, current - amount)
	_inv = invulnerable_time
	damaged.emit(amount)
	health_changed.emit(current, max_health)
	GameState.emit_game_event("damaged", {"target": str(get_parent().name), "health": current})
	if current <= 0:
		died.emit()
		GameState.emit_game_event("died", {"target": str(get_parent().name)})
		if restart_on_death and get_parent().is_in_group("player"):
			await get_tree().create_timer(1.2).timeout
			get_tree().reload_current_scene()
	return true


func heal(amount: int = 1) -> void:
	current = mini(max_health, current + amount)
	health_changed.emit(current, max_health)
'''

COIN = '''extends Area2D
## 코인: 플레이어가 닿으면 GameState 에 코인을 더하고(인벤토리가 있으면 아이템도) 사라진다.

@export var value: int = 1


func _ready() -> void:
	body_entered.connect(_on_body_entered)


func _on_body_entered(body: Node) -> void:
	if not body.is_in_group("player"):
		return
	GameState.add_coin(value)
	var inv = body.get_node_or_null("Inventory")
	if inv != null:
		inv.add_item("coin", value)
	queue_free()
'''

INVENTORY = '''extends Node
## 인벤토리 컴포넌트: 아이템 id -> 수량.

signal changed(items: Dictionary)

var items: Dictionary = {}


func add_item(id: String, qty: int = 1) -> void:
	items[id] = int(items.get(id, 0)) + qty
	changed.emit(items)
	GameState.emit_game_event("item_added", {"id": id, "qty": qty})


func remove_item(id: String, qty: int = 1) -> bool:
	var have := int(items.get(id, 0))
	if have < qty:
		return false
	items[id] = have - qty
	if int(items[id]) <= 0:
		items.erase(id)
	changed.emit(items)
	return true


func count(id: String) -> int:
	return int(items.get(id, 0))


func has_item(id: String, qty: int = 1) -> bool:
	return count(id) >= qty
'''

ENEMY_PLATFORMER = '''extends CharacterBody2D
## 순찰 + 추적 적 AI (platformer). 접촉 시 플레이어에게 피해, 위에서 밟히면 처치된다.

signal defeated

@export var speed: float = 70.0
@export var chase_speed: float = 120.0
@export var detect_range: float = 240.0
@export var contact_damage: int = 1

var _dir: float = -1.0
var _gravity: float = ProjectSettings.get_setting("physics/2d/default_gravity", 980.0)
@onready var _hurtbox: Area2D = $Hurtbox


func _physics_process(delta: float) -> void:
	if not is_on_floor():
		velocity.y += _gravity * delta
	var target := get_tree().get_first_node_in_group("player") as Node2D
	var v := speed
	if target != null and global_position.distance_to(target.global_position) < detect_range:
		_dir = signf(target.global_position.x - global_position.x)
		if _dir == 0.0:
			_dir = 1.0
		v = chase_speed
	elif is_on_wall() or (is_on_floor() and not _ground_ahead()):
		_dir = -_dir
	velocity.x = _dir * v
	move_and_slide()
	_check_contact()


func _ground_ahead() -> bool:
	var from := global_position + Vector2(_dir * 20.0, 0.0)
	var query := PhysicsRayQueryParameters2D.create(from, from + Vector2(0.0, 36.0), 1)
	return not get_world_2d().direct_space_state.intersect_ray(query).is_empty()


func _check_contact() -> void:
	for b in _hurtbox.get_overlapping_bodies():
		var body = b
		if not body.is_in_group("player"):
			continue
		if body.velocity.y > 40.0 and body.global_position.y < global_position.y - 8.0:
			body.velocity.y = -320.0
			defeated.emit()
			GameState.emit_game_event("enemy_defeated", {"name": str(name)})
			queue_free()
			return
		var health = body.get_node_or_null("Health")
		if health != null:
			health.damage(contact_damage)
'''

ENEMY_TOPDOWN = '''extends CharacterBody2D
## 배회 + 추적 적 AI (top-down). 접촉 시 플레이어에게 피해.

signal defeated

@export var speed: float = 60.0
@export var chase_speed: float = 110.0
@export var detect_range: float = 260.0
@export var contact_damage: int = 1

var _dir: Vector2 = Vector2.RIGHT
var _t: float = 0.0
@onready var _hurtbox: Area2D = $Hurtbox


func _physics_process(delta: float) -> void:
	var target := get_tree().get_first_node_in_group("player") as Node2D
	if target != null and global_position.distance_to(target.global_position) < detect_range:
		velocity = global_position.direction_to(target.global_position) * chase_speed
	else:
		_t -= delta
		if _t <= 0.0 or is_on_wall():
			_dir = Vector2.from_angle(randf() * TAU)
			_t = randf_range(0.8, 2.0)
		velocity = _dir * speed
	move_and_slide()
	_check_contact()


func _check_contact() -> void:
	for b in _hurtbox.get_overlapping_bodies():
		var body = b
		if not body.is_in_group("player"):
			continue
		var health = body.get_node_or_null("Health")
		if health != null:
			health.damage(contact_damage)
'''

PAUSE_MENU = '''extends CanvasLayer
## 일시정지 메뉴: "pause" 액션(ESC)으로 토글. process_mode = ALWAYS.


func _ready() -> void:
	visible = false
	$Center/VBox/ResumeButton.pressed.connect(_resume)
	$Center/VBox/QuitButton.pressed.connect(_quit)


func _unhandled_input(event: InputEvent) -> void:
	if event.is_action_pressed("pause"):
		if get_tree().paused:
			_resume()
		else:
			_pause()
		get_viewport().set_input_as_handled()


func _pause() -> void:
	get_tree().paused = true
	visible = true
	GameState.emit_game_event("paused")


func _resume() -> void:
	get_tree().paused = false
	visible = false
	GameState.emit_game_event("resumed")


func _quit() -> void:
	get_tree().paused = false
	get_tree().quit()
'''

MENU = '''extends Control
## 타이틀 메뉴: Start 버튼으로 start_scene 으로 전환.

@export_file("*.tscn") var start_scene: String = "res://scenes/main.tscn"


func _ready() -> void:
	$Center/VBox/StartButton.pressed.connect(_on_start)
	$Center/VBox/QuitButton.pressed.connect(_on_quit)


func _on_start() -> void:
	get_tree().change_scene_to_file(start_scene)


func _on_quit() -> void:
	get_tree().quit()
'''
