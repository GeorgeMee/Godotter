extends Node

class_name Managers

@export var require_event_bus: bool = true

func _ready() -> void:
	if require_event_bus and get_node_or_null("EventBus") == null:
		push_error("Managers: missing child node 'EventBus'")
		get_tree().quit(1)

	_validate_unique_mgr_groups()


func _validate_unique_mgr_groups() -> void:
	# Enforce: for any group that starts with "mgr:", there must be at most one node in that group.
	# Convention: each mgr node should be a child of Managers and belong to exactly one "mgr:*" group.
	var counts := {}
	for child in get_children():
		var node := child as Node
		if node == null:
			continue
		for group in node.get_groups():
			var g := StringName(group)
			if not String(g).begins_with("mgr:"):
				continue
			counts[g] = int(counts.get(g, 0)) + 1

	for g in counts.keys():
		if int(counts[g]) > 1:
			push_error("Managers: duplicate mgr group '%s' (count=%d)" % [String(g), int(counts[g])])
			get_tree().quit(1)
