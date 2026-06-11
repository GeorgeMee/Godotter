extends Control


func _ready() -> void:
	$BackBtn.pressed.connect(
		func() -> void: get_tree().change_scene_to_file("res://game/levels/main_level.tscn")
	)
