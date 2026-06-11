extends CanvasLayer

## Overlay panel with scene jumper and reload.


func _ready() -> void:
	$Panel/VBoxContainer/TopBar/CloseBtn.pressed.connect(hide)
	$Panel/VBoxContainer/ReloadBtn.pressed.connect(_on_reload_pressed)
	_populate_scenes()


func _populate_scenes() -> void:
	var list := $Panel/VBoxContainer/SceneList/SceneListVBox
	var scene_root := "res://game/levels/"
	var dir := DirAccess.open(scene_root)
	if dir == null:
		return
	dir.list_dir_begin()
	var file_name := dir.get_next()
	var scenes: Array[String] = []
	while file_name != "":
		if file_name.ends_with(".tscn"):
			scenes.append(scene_root + file_name)
		file_name = dir.get_next()
	dir.list_dir_end()
	scenes.sort()
	for scene_path in scenes:
		var btn := Button.new()
		btn.text = scene_path.trim_prefix(scene_root)
		btn.alignment = HORIZONTAL_ALIGNMENT_LEFT
		btn.pressed.connect(_on_jump.bind(scene_path))
		list.add_child(btn)


func _on_jump(scene_path: String) -> void:
	hide()
	get_tree().change_scene_to_file(scene_path)


func _on_reload_pressed() -> void:
	hide()
	get_tree().reload_current_scene()
