extends VirtualController

## Compact layout: D-pad (left) + 4 face buttons ABXY (right) + Pause (top-right).


func _bind_buttons() -> void:
	bind_button(^"DPad/Left", InputActions.LEFT)
	bind_button(^"DPad/Right", InputActions.RIGHT)
	bind_button(^"DPad/Up", InputActions.UP)
	bind_button(^"DPad/Down", InputActions.DOWN)
	bind_button(^"Actions/Primary", InputActions.PRIMARY)
	bind_button(^"Actions/Secondary", InputActions.SECONDARY)
	bind_button(^"Actions/Tertiary", InputActions.TERTIARY)
	bind_button(^"Actions/Quaternary", InputActions.QUATERNARY)
	bind_button(^"Pause", InputActions.PAUSE)
