# Item Pickup Feature

职责：
- 提供“请求拾取物品”的玩法入口。
- 不直接修改 Inventory 状态，而是发布结构化事件。

事件流：
1. `ItemPickupFeature.request_pickup(item_id)` 发布 `EventTypes.ITEM_PICKUP_REQUESTED`
2. `InventoryManager` 处理该事件并更新内部状态
3. `InventoryManager` 发布 `EventTypes.ITEM_ADDED`

这样 feature 只表达意图，system 负责状态落地，便于替换与测试。
