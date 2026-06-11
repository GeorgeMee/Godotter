extends Control

## Floating trigger button — draggable, stays on top, activates cheat overlay.

signal pressed

var _dragging := false
var _drag_start := Vector2.ZERO
var _drag_offset := Vector2.ZERO
var _tap_threshold := 10.0


func _ready() -> void:
	position = get_viewport().get_visible_rect().size - Vector2(60, 140)
	modulate.a = 0.5


func _gui_input(event: InputEvent) -> void:
	if event is InputEventMouseButton:
		if event.button_index == MOUSE_BUTTON_LEFT:
			if event.pressed:
				_drag_start = position
				_drag_offset = event.position
				modulate.a = 0.9
			else:
				modulate.a = 0.5
				if not _dragging:
					pressed.emit()
				_dragging = false

	if event is InputEventMouseMotion:
		if Input.is_mouse_button_pressed(MOUSE_BUTTON_LEFT):
			if not _dragging:
				_dragging = position.distance_to(_drag_start) > _tap_threshold
			if _dragging:
				position = get_global_mouse_position() - _drag_offset

	if event is InputEventScreenTouch:
		if event.pressed:
			_drag_start = position
			_drag_offset = event.position
			modulate.a = 0.9
		else:
			modulate.a = 0.5
			if not _dragging:
				pressed.emit()
			_dragging = false

	if event is InputEventScreenDrag:
		if not _dragging:
			_dragging = position.distance_to(_drag_start) > _tap_threshold
		if _dragging:
			position = event.position - _drag_offset + _drag_start
