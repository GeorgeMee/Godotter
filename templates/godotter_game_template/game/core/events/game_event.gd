extends RefCounted

class_name GameEvent

var type: StringName
var data: Dictionary
var source: NodePath
var ts_ms: int
var corr_id: String

func _init(
	event_type: StringName,
	event_data: Dictionary = {},
	event_source: NodePath = NodePath(),
	event_ts_ms: int = 0,
	event_corr_id: String = ""
) -> void:
	type = event_type
	data = event_data
	source = event_source
	ts_ms = event_ts_ms
	corr_id = event_corr_id

