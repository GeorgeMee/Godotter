extends Node

## Autoload — last in load order. Activates cheat tools only in debug builds
## or when application/config/dev_mode is true.

var _overlay: CanvasLayer = null
var _trigger: Control = null
var _trigger_layer: CanvasLayer = null


func _ready() -> void:
	if not OS.is_debug_build():
		if not ProjectSettings.get_setting("application/config/dev_mode", false):
			return
	await get_tree().process_frame
	_spawn()


func _spawn() -> void:
	_trigger_layer = CanvasLayer.new()
	_trigger_layer.layer = 100
	get_tree().root.add_child(_trigger_layer)

	_trigger = preload("res://cheat/overlay/cheat_trigger.tscn").instantiate()
	_trigger.pressed.connect(_toggle_overlay)
	_trigger_layer.add_child(_trigger)

	_overlay = preload("res://cheat/overlay/cheat_overlay.tscn").instantiate()
	_overlay.hide()
	get_tree().root.add_child(_overlay)


func _toggle_overlay() -> void:
	_overlay.visible = not _overlay.visible
