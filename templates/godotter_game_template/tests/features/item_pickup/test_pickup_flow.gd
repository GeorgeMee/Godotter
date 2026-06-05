extends Node

const EventTypes = preload("res://game/core/events/event_types.gd")
const GameEvent = preload("res://game/core/events/game_event.gd")
const EventBusScript = preload("res://game/core/events/event_bus.gd")
const InventoryManagerScript = preload("res://game/systems/inventory/scripts/inventory_manager.gd")
const ItemPickupFeatureScript = preload("res://game/features/item_pickup/scripts/item_pickup_feature.gd")

var _received_item_added := false

func _ready() -> void:
	var feature := get_node("ItemPickupFeature") as ItemPickupFeatureScript
	var inventory := get_node("Managers/InventoryMgr") as InventoryManagerScript
	var event_bus := get_node("Managers/EventBus") as EventBusScript

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
