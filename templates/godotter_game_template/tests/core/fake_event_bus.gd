extends Node

class_name FakeEventBus

var events: Array = []

func publish(event) -> void:
	events.append(event)

