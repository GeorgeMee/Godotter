extends Node

const TIMEOUT_SECONDS := 2.0

var _started_ms := 0


func _ready() -> void:
	_started_ms = Time.get_ticks_msec()
	var scene := load("res://game/levels/main.tscn") as PackedScene
	if scene == null:
		_fail("main scene did not load")
		return
	var instance := scene.instantiate()
	add_child(instance)
	await get_tree().process_frame
	if instance.get_node_or_null("Managers/EventBus") == null:
		_fail("Managers/EventBus missing")
		return
	if instance.get_node_or_null("Managers/InputMapper") == null:
		_fail("Managers/InputMapper missing")
		return
	_pass("main smoke")


func _process(_delta: float) -> void:
	if Time.get_ticks_msec() - _started_ms > int(TIMEOUT_SECONDS * 1000.0):
		_fail("timeout")


func _pass(label: String) -> void:
	print("PASS: %s" % label)
	get_tree().quit(0)


func _fail(message: String) -> void:
	printerr("FAIL: %s" % message)
	get_tree().quit(1)
