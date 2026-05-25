extends Node

const EventTypes = preload("res://game/core/events/event_types.gd")
const GameEvent = preload("res://game/core/events/game_event.gd")

var _received_item_added := false

func _ready() -> void:
	var feature := get_node("ItemPickupFeature") as ItemPickupFeature
	var inventory := get_node("Managers/InventoryMgr") as InventoryManager
	var event_bus := get_node("Managers/EventBus") as EventBus

	event_bus.subscribe(EventTypes.ITEM_ADDED, _on_item_added)
	feature.request_pickup(&"starter_coin")

	assert(inventory.has_item(&"starter_coin"))
	assert(_received_item_added)

	get_tree().quit()


func _on_item_added(event) -> void:
	if not (event is GameEvent):
		return
	if String(event.data.get("item_id", "")) == "starter_coin":
		_received_item_added = true
