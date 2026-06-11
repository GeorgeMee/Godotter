extends Control
class_name VirtualController

## Base class for mobile virtual controller boards.
## Subclasses override _bind_buttons() to wire visual buttons to InputMap actions.

@export var visible_on_desktop: bool = false


func _ready() -> void:
	var enabled := visible_on_desktop or OS.has_feature("mobile") or DisplayServer.is_touchscreen_available()
	visible = enabled
	if enabled:
		InputActions.ensure_default_actions()
		_bind_buttons()


func _bind_buttons() -> void:
	pass


## Connect a button node at [path] to fire [action] on press/release.
func bind_button(path: NodePath, action: StringName) -> void:
	var button := get_node_or_null(path) as BaseButton
	if button == null:
		push_warning("VirtualController: missing button %s" % String(path))
		return
	button.button_down.connect(func() -> void: Input.action_press(action))
	button.button_up.connect(func() -> void: Input.action_release(action))
