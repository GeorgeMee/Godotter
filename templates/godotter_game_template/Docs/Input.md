# 输入规范

模板使用 Godot 原生 `InputMap`，不依赖插件。玩法代码不要直接绑定键盘、触摸或按钮节点，而是依赖一组通用“虚拟手柄”动作：

- `left`
- `right`
- `up`
- `down`
- `primary`
- `secondary`
- `tertiary`
- `confirm`
- `cancel`
- `pause`

`game/core/input/input_actions.gd` 会在运行时确保这些动作存在，并注册 PC 默认按键。这样模板不需要频繁改 `project.godot`，移动端 UI 也能继续复用同一套动作。

## 玩法语义映射

如果某个游戏需要 `move_left`、`shoot`、`rotate_piece` 这类玩法语义，不要新增底层输入动作；优先通过 `game/core/input/input_mapper.gd` 做别名映射。

示例：

```gdscript
var mapper := get_node("Managers/InputMapper") as InputMapper
mapper.bind_alias(&"rotate_piece", &"up")

if mapper.is_just_pressed(&"rotate_piece"):
	_rotate_piece()
```

也就是说：

- 底层动作保持稳定：`left/right/up/down/primary/...`
- 玩法语义可以按项目变化：`move_left/shoot/jump/rotate_piece/...`
- PC、移动端、测试脚本都只需要触发底层动作

## 移动端控制

模板包含 `ui/views/mobile_controls.tscn`：

- 左下 D-pad：`left/right/up/down`
- 右下按钮：`primary/secondary/tertiary`
- 右上按钮：`pause`

默认只在移动端或触摸设备显示。PC 调试时如果需要预览，可以把 `visible_on_desktop` 打开。
