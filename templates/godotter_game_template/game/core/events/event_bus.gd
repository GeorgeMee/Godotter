extends Node

class_name EventBus

const GameEventScript = preload("res://game/core/events/game_event.gd")

signal event_emitted(event)

var _handlers: Dictionary = {} # StringName -> Array[Callable]

func subscribe(event_type: StringName, handler: Callable) -> void:
	if not _handlers.has(event_type):
		_handlers[event_type] = []
	_handlers[event_type].append(handler)

func unsubscribe(event_type: StringName, handler: Callable) -> void:
	if not _handlers.has(event_type):
		return
	var arr: Array = _handlers[event_type]
	arr.erase(handler)

func publish(event) -> void:
	if not (event is GameEventScript):
		push_warning("EventBus.publish expected GameEvent instance")
		return
	event_emitted.emit(event)
	if _handlers.has(event.type):
		for handler in _handlers[event.type]:
			handler.call(event)

