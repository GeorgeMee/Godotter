extends Control

## Floating trigger button — draggable, stays on top, activates cheat overlay.

signal pressed

var _is_dragging := false
var _is_mouse_drag := false
var _drag_start_pos := Vector2.ZERO
var _drag_offset := Vector2.ZERO
var _tap_threshold := 10.0


func _ready() -> void:
	position = get_viewport().get_visible_rect().size - Vector2(60, 140)
	modulate.a = 0.5
	set_process(false)


func _process(_delta: float) -> void:
	if not Input.is_mouse_button_pressed(MOUSE_BUTTON_LEFT):
		set_process(false)
		_is_dragging = false
		_is_mouse_drag = false
		modulate.a = 0.5
		return

	var gmp := get_global_mouse_position()
	if not _is_dragging:
		_is_dragging = gmp.distance_to(_drag_start_pos) > _tap_threshold
	if _is_dragging:
		position = gmp - _drag_offset


func _gui_input(event: InputEvent) -> void:
	if event is InputEventMouseButton:
		if event.button_index == MOUSE_BUTTON_LEFT:
			if event.pressed:
				_is_dragging = false
				_is_mouse_drag = true
				_drag_start_pos = get_global_mouse_position()
				_drag_offset = event.position
				modulate.a = 0.9
				set_process(true)
			elif not _is_dragging and _is_mouse_drag:
				pressed.emit()
				set_process(false)

	if event is InputEventScreenTouch:
		if event.pressed:
			_is_dragging = false
			_drag_start_pos = get_global_mouse_position()
			_drag_offset = event.position
			modulate.a = 0.9
		elif not _is_dragging:
			pressed.emit()
			modulate.a = 0.5

	if event is InputEventScreenDrag:
		if not _is_dragging:
			_is_dragging = (event.position - _drag_offset).length() > _tap_threshold
		if _is_dragging:
			position = event.position - _drag_offset + _drag_start_pos
