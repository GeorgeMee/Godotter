extends Control


func _ready() -> void:
	$PlayBtn.pressed.connect(
		func() -> void: get_tree().change_scene_to_file("res://game/levels/game_level.tscn")
	)
