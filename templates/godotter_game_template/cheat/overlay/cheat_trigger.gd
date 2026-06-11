extends Control

## Floating trigger button — draggable, stays on top, activates cheat overlay.


signal pressed

var _dragging := false
var _drag_start := Vector2.ZERO
var _drag_offset := Vector2.ZERO
var _tap_threshold := 10.0


func _ready() -> void:
	# Position at bottom-right corner
	position = get_viewport().get_visible_rect().size - Vector2(60, 140)
	# Make self semi-transparent
	modulate.a = 0.5


func _gui_input(event: InputEvent) -> void:
	if event is InputEventMouseButton:
		if event.button_index == MOUSE_BUTTON_LEFT:
			if event.pressed:
				_start_drag(event.position)
			else:
				_end_drag()
				if not _dragging and position.distance_to(_drag_start) < _tap_threshold:
					pressed.emit()

	if event is InputEventMouseMotion and _dragging:
		position = get_global_mouse_position() - _drag_offset

	if event is InputEventScreenTouch:
		if event.pressed:
			_start_drag(event.position)
		else:
			_end_drag()
			if not _dragging and position.distance_to(_drag_start) < _tap_threshold:
				pressed.emit()

	if event is InputEventScreenDrag and _dragging:
		position = event.position - _drag_offset + _drag_start


func _start_drag(at: Vector2) -> void:
	_dragging = false
	_drag_start = position
	_drag_offset = at
	modulate.a = 0.9


func _end_drag() -> void:
	modulate.a = 0.5


func _exit_tree() -> void:
	queue_free()
