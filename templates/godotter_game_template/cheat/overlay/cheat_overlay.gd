extends CanvasLayer

## Overlay panel with Jump, Inspect tabs and Reload.


func _ready() -> void:
	$Panel/VBoxContainer/TopBar/CloseBtn.pressed.connect(hide)
	$Panel/VBoxContainer/TopBar/ReloadBtn.pressed.connect(_on_reload_pressed)

	var nav_jump := $Panel/VBoxContainer/TopBar/NavJumpBtn
	var nav_inspect := $Panel/VBoxContainer/TopBar/NavInspectBtn
	var jump_content := $Panel/VBoxContainer/ContentStack/JumpContent
	var inspect_content := $Panel/VBoxContainer/ContentStack/InspectContent

	nav_jump.pressed.connect(
		func() -> void:
			jump_content.show()
			inspect_content.hide()
	)
	nav_inspect.pressed.connect(
		func() -> void:
			jump_content.hide()
			inspect_content.show()
			_populate_inspect()
	)

	_populate_scenes()
	inspect_content.hide()


func _populate_scenes() -> void:
	var list := $Panel/VBoxContainer/ContentStack/JumpContent/SceneList/SceneListVBox
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


func _populate_inspect() -> void:
	var vbox := $Panel/VBoxContainer/ContentStack/InspectContent/InspectVBox
	# Clear previous content
	for child in vbox.get_children():
		child.queue_free()

	var scene := get_tree().current_scene
	if scene == null:
		return

	_inspect_node(scene, vbox, 0)


func _inspect_node(n: Node, parent_vbox: Control, depth: int) -> void:
	if depth > 3:
		return

	var prefix := "  ".repeat(depth)
	var name_label := Label.new()
	name_label.text = prefix + n.name + " (" + n.get_class() + ")"
	parent_vbox.add_child(name_label)

	# Show exported/editor properties
	for prop in n.get_property_list():
		if prop.usage & PROPERTY_USAGE_EDITOR:
			var val = n.get(prop.name)
			if val == null:
				continue
			var row := HBoxContainer.new()
			var prop_label := Label.new()
			prop_label.text = prefix + "  " + prop.name
			var val_label := Label.new()
			val_label.text = str(val)
			row.add_child(prop_label)
			row.add_child(val_label)
			parent_vbox.add_child(row)

	# Recurse into children
	for child in n.get_children():
		if child is Node:
			_inspect_node(child, parent_vbox, depth + 1)
