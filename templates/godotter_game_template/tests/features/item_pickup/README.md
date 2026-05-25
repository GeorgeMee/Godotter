# Item Pickup Feature Test

`pickup_harness.tscn` 演示最小测试装配：
- `Managers`
- `EventBus`
- `InventoryMgr`
- `ItemPickupFeature`

`test_pickup_flow.gd` 在 `_ready()` 中直接触发一次拾取请求，并断言：
- `InventoryMgr` 收到并落地状态
- 事件链路可在最小场景内运行
