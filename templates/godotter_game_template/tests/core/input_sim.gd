extends RefCounted

class_name InputSim

static func press_action(action_name: StringName) -> void:
	Input.action_press(String(action_name))


static func release_action(action_name: StringName) -> void:
	Input.action_release(String(action_name))


static func tap_action(tree: SceneTree, action_name: StringName, *, frames_down: int = 1) -> void:
	press_action(action_name)
	for _i in range(maxi(1, frames_down)):
		await tree.process_frame
	release_action(action_name)


static func run_frames(tree: SceneTree, frames: int) -> void:
	for _i in range(maxi(0, frames)):
		await tree.process_frame


static func quit_ok(tree: SceneTree, message: String = "") -> void:
	if message:
		print(message)
	tree.quit(0)


static func quit_fail(tree: SceneTree, message: String) -> void:
	printerr(message)
	tree.quit(1)
