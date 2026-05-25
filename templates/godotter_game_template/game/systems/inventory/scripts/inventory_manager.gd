extends Node

class_name InventoryManager

const EventBusScript = preload("res://game/core/events/event_bus.gd")
const EventTypes = preload("res://game/core/events/event_types.gd")
const GameEvent = preload("res://game/core/events/game_event.gd")

signal item_added(item_id: StringName)

var event_bus = null
var _items: Array[StringName] = []


func configure(bus) -> void:
	if bus != null and not (bus is EventBusScript):
		push_warning("InventoryManager.configure expected EventBus instance")
		return
	if event_bus == bus:
		return
	if event_bus != null:
		event_bus.unsubscribe(EventTypes.ITEM_PICKUP_REQUESTED, _on_item_pickup_requested)
	event_bus = bus
	if event_bus != null:
		event_bus.subscribe(EventTypes.ITEM_PICKUP_REQUESTED, _on_item_pickup_requested)


func has_item(item_id: StringName) -> bool:
	return _items.has(item_id)


func all_items() -> Array[StringName]:
	return _items.duplicate()


func _exit_tree() -> void:
	if event_bus != null:
		event_bus.unsubscribe(EventTypes.ITEM_PICKUP_REQUESTED, _on_item_pickup_requested)


func _on_item_pickup_requested(event) -> void:
	if not (event is GameEvent):
		return
	var item_id_text := String(event.data.get("item_id", "")).strip_edges()
	if item_id_text.is_empty():
		return

	var item_id := StringName(item_id_text)
	if _items.has(item_id):
		return

	_items.append(item_id)
	item_added.emit(item_id)

	if event_bus != null:
		event_bus.publish(
			GameEvent.new(
				EventTypes.ITEM_ADDED,
				{
					"item_id": item_id_text,
					"count": _items.size(),
				},
				get_path(),
				Time.get_ticks_msec(),
				event.corr_id,
			)
		)
