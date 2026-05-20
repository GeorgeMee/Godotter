extends Node

class_name EventBus

signal event_emitted(event: GameEvent)

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

func publish(event: GameEvent) -> void:
	event_emitted.emit(event)
	if _handlers.has(event.type):
		for handler in _handlers[event.type]:
			handler.call(event)

