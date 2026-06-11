extends Control


func _ready() -> void:
	if has_node("BackBtn"):
		$BackBtn.pressed.connect(_on_back_pressed)


func _on_back_pressed() -> void:
	get_tree().change_scene_to_file("res://game/levels/main_level.tscn")
