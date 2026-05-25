extends Node

class_name ItemPickupFeature

const EventBusScript = preload("res://game/core/events/event_bus.gd")
const EventTypes = preload("res://game/core/events/event_types.gd")
const GameEvent = preload("res://game/core/events/game_event.gd")

@export var event_bus_path: NodePath = ^"../Managers/EventBus"
@export var demo_item_id: StringName = &"starter_coin"
@export var publish_on_ready: bool = false

var event_bus = null


func _ready() -> void:
	_resolve_event_bus()
	if publish_on_ready:
		request_pickup(demo_item_id)


func request_pickup(item_id: StringName) -> void:
	if event_bus == null:
		push_warning("ItemPickupFeature: EventBus not configured")
		return

	var item_id_text := String(item_id)
	var corr_id := "pickup_%s_%d" % [item_id_text, Time.get_ticks_msec()]
	event_bus.publish(
		GameEvent.new(
			EventTypes.ITEM_PICKUP_REQUESTED,
			{"item_id": item_id_text},
			get_path(),
			Time.get_ticks_msec(),
			corr_id,
		)
	)


func _resolve_event_bus() -> void:
	var node := get_node_or_null(event_bus_path)
	if node is EventBusScript:
		event_bus = node
		return

	push_error("ItemPickupFeature: EventBus not found at '%s'" % String(event_bus_path))
