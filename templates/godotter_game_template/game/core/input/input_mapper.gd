extends Node

class_name InputMapper

const InputActionsScript = preload("res://game/core/input/input_actions.gd")

@export var register_defaults_on_ready: bool = true

var aliases: Dictionary = {
	&"move_left": InputActionsScript.LEFT,
	&"move_right": InputActionsScript.RIGHT,
	&"move_up": InputActionsScript.UP,
	&"move_down": InputActionsScript.DOWN,
	&"rotate": InputActionsScript.UP,
	&"shoot": InputActionsScript.PRIMARY,
	&"interact": InputActionsScript.CONFIRM,
	&"back": InputActionsScript.CANCEL,
	&"alt_action": InputActionsScript.QUATERNARY,
}


func _ready() -> void:
	add_to_group("mgr:input")
	if register_defaults_on_ready:
		InputActionsScript.ensure_default_actions()


func bind_alias(game_action: StringName, base_action: StringName) -> void:
	aliases[game_action] = base_action


func resolve(action: StringName) -> StringName:
	return aliases.get(action, action)


func is_pressed(action: StringName) -> bool:
	return Input.is_action_pressed(resolve(action))


func is_just_pressed(action: StringName) -> bool:
	return Input.is_action_just_pressed(resolve(action))


func is_just_released(action: StringName) -> bool:
	return Input.is_action_just_released(resolve(action))


func get_vector(
	negative_x: StringName = InputActionsScript.LEFT,
	positive_x: StringName = InputActionsScript.RIGHT,
	negative_y: StringName = InputActionsScript.UP,
	positive_y: StringName = InputActionsScript.DOWN
) -> Vector2:
	return Input.get_vector(resolve(negative_x), resolve(positive_x), resolve(negative_y), resolve(positive_y))
