extends RefCounted

class_name InputActions

const LEFT: StringName = &"left"
const RIGHT: StringName = &"right"
const UP: StringName = &"up"
const DOWN: StringName = &"down"
const PRIMARY: StringName = &"primary"
const SECONDARY: StringName = &"secondary"
const TERTIARY: StringName = &"tertiary"
const QUATERNARY: StringName = &"quaternary"
const CONFIRM: StringName = &"confirm"
const CANCEL: StringName = &"cancel"
const PAUSE: StringName = &"pause"

const BASE_ACTIONS: Array[StringName] = [
	LEFT,
	RIGHT,
	UP,
	DOWN,
	PRIMARY,
	SECONDARY,
	TERTIARY,
	QUATERNARY,
	CONFIRM,
	CANCEL,
	PAUSE,
]


static func ensure_default_actions() -> void:
	for action in BASE_ACTIONS:
		if not InputMap.has_action(action):
			InputMap.add_action(action, 0.5)

	_add_key(LEFT, KEY_A)
	_add_key(LEFT, KEY_LEFT)
	_add_key(RIGHT, KEY_D)
	_add_key(RIGHT, KEY_RIGHT)
	_add_key(UP, KEY_W)
	_add_key(UP, KEY_UP)
	_add_key(DOWN, KEY_S)
	_add_key(DOWN, KEY_DOWN)
	_add_key(PRIMARY, KEY_SPACE)
	_add_key(SECONDARY, KEY_SHIFT)
	_add_key(TERTIARY, KEY_Q)
	_add_key(TERTIARY, KEY_CTRL)
	_add_key(QUATERNARY, KEY_E)
	_add_key(CONFIRM, KEY_ENTER)
	_add_key(CANCEL, KEY_ESCAPE)
	_add_key(PAUSE, KEY_P)


static func _add_key(action: StringName, keycode: Key) -> void:
	var event := InputEventKey.new()
	event.keycode = keycode
	for existing in InputMap.action_get_events(action):
		if existing is InputEventKey and existing.keycode == keycode:
			return
	InputMap.action_add_event(action, event)
