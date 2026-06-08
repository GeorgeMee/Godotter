extends Control

class_name MobileControls

const InputActionsScript = preload("res://game/core/input/input_actions.gd")

@export var visible_on_desktop: bool = false


func _ready() -> void:
	InputActionsScript.ensure_default_actions()
	visible = visible_on_desktop or OS.has_feature("mobile") or DisplayServer.is_touchscreen_available()
	_bind_button(^"DPad/Left", InputActionsScript.LEFT)
	_bind_button(^"DPad/Right", InputActionsScript.RIGHT)
	_bind_button(^"DPad/Up", InputActionsScript.UP)
	_bind_button(^"DPad/Down", InputActionsScript.DOWN)
	_bind_button(^"Actions/Primary", InputActionsScript.PRIMARY)
	_bind_button(^"Actions/Secondary", InputActionsScript.SECONDARY)
	_bind_button(^"Actions/Tertiary", InputActionsScript.TERTIARY)
	_bind_button(^"Pause", InputActionsScript.PAUSE)


func _bind_button(path: NodePath, action: StringName) -> void:
	var button := get_node_or_null(path) as BaseButton
	if button == null:
		push_warning("MobileControls: missing button %s" % String(path))
		return
	button.button_down.connect(func() -> void: Input.action_press(action))
	button.button_up.connect(func() -> void: Input.action_release(action))
