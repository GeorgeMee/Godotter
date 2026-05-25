# Inventory System

职责：
- 维护已收集物品的最小状态。
- 订阅 `EventTypes.ITEM_PICKUP_REQUESTED`。
- 成功处理后发布 `EventTypes.ITEM_ADDED`。

依赖：
- `core/events/EventBus`
- `core/events/GameEvent`
- `core/events/EventTypes`

约束：
- 作为 `Managers` 的子节点挂载。
- 节点应属于唯一分组 `mgr:inventory`。
- 不主动查找其他 manager；跨模块协作优先通过事件完成。
