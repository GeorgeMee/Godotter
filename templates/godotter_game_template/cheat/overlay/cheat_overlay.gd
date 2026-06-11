extends CanvasLayer

## Bottom slide-up panel with scene jumper and reload.


var _animating := false


func slide_up() -> void:
	var panel := $Panel
	panel.position.y = get_viewport().get_visible_rect().size.y
	show()
	var tween := create_tween()
	tween.set_ease(Tween.EASE_OUT).set_trans(Tween.TRANS_CUBIC)
	tween.tween_property(panel, "position:y", get_viewport().get_visible_rect().size.y * 0.4, 0.25)


func slide_down() -> void:
	var panel := $Panel
	var tween := create_tween()
	tween.set_ease(Tween.EASE_IN).set_trans(Tween.TRANS_CUBIC)
	tween.tween_property(panel, "position:y", get_viewport().get_visible_rect().size.y, 0.2)
	tween.tween_callback(hide)


func _ready() -> void:
	$Panel/VBoxContainer/CloseBtn.pressed.connect(slide_down)
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
		if file_name.ends_with(".tscn") and file_name != "uid://":
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
	slide_down()
	# Use call_deferred so the tween finishes before scene change
	call_deferred("_change_scene", scene_path)


func _change_scene(scene_path: String) -> void:
	var err := get_tree().change_scene_to_file(scene_path)
	if err != OK:
		push_error("Failed to change scene to %s" % scene_path)


func _on_reload_pressed() -> void:
	slide_down()
	call_deferred("_reload_current")


func _reload_current() -> void:
	var err := get_tree().reload_current_scene()
	if err != OK:
		push_error("Failed to reload scene")
