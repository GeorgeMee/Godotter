extends Node

const InputActions = preload("res://game/core/input/input_actions.gd")
const InputMapperScript = preload("res://game/core/input/input_mapper.gd")


func _ready() -> void:
	InputActions.ensure_default_actions()

	var mapper := InputMapperScript.new()
	add_child(mapper)
	await get_tree().process_frame

	if not InputMap.has_action(InputActions.LEFT):
		_fail("left action missing")
		return
	if InputMap.action_get_events(InputActions.LEFT).is_empty():
		_fail("left action has no key bindings")
		return

	mapper.bind_alias(&"rotate_piece", InputActions.UP)
	Input.action_press(InputActions.UP)
	await get_tree().process_frame
	if not mapper.is_pressed(&"rotate_piece"):
		_fail("alias rotate_piece did not resolve to up")
		return
	Input.action_release(InputActions.UP)

	_pass("input mapper")


func _pass(label: String) -> void:
	print("PASS: %s" % label)
	get_tree().quit(0)


func _fail(message: String) -> void:
	printerr("FAIL: %s" % message)
	get_tree().quit(1)
