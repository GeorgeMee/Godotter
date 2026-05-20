# Godotter 开发模式（草案）：项目结构 + Managers + 结构化事件

> 目的：为 **无头（云服务器）+ agent 协作开发** 的 Godot 项目提供一套可复制的项目结构与运行时约束，提升单元开发/单元测试的隔离性，减少跨模块耦合与“隐式依赖”带来的维护成本。
>
> 状态：草案（可作为后续 Godotter 的 CLI/Skill 固化规范）

---

## 设计目标

1. **单元开发（Unit of Work）明确**：以“能力/feature”为所有权边界，便于 agent 专注在单一功能范围内工作。
2. **单元测试优先**：每个 feature 能在最小场景/最少依赖下独立验证，无需依赖完整关卡或真实外部服务。
3. **无头友好**：可以在 CI/服务器环境中运行校验与测试；尽量在启动早期失败（fail fast）。
4. **组合层与能力层分离**：关卡是“组合”，feature 是“能力实现”，content 是“复用内容”，避免互相渗透导致引用乱套。

---

## 核心约定

### 1) `Managers` 作用域（Scene Scope）

- **每个 Level 根节点必须包含一个 `Managers` 节点**。
- **每个关卡内，同一种类的 `Mgr` 只能存在一个实例**（唯一性约束）。
- `Managers` 是该关卡的 **composition root（装配点）**：
  - 负责挂载/初始化本关卡需要的各类 `Mgr`（或者它们的替身实现）。
  - 负责校验“必需服务是否存在、group 是否正确、是否重复”等。

### 2) 结构化事件（EventBus）

- 每个关卡的 `Managers` 下挂一个 **场景级 `EventBus`**（非 Autoload）。
- `Mgr` 间默认使用 **结构化事件** 通信（解耦、可测试）：
  - 事件对象 `GameEvent`（建议字段）：
    - `type: StringName`（事件类型，集中定义常量，避免拼写错误）
    - `data: Dictionary`（payload）
    - `source: NodePath`（可选）
    - `ts_ms: int`（可选）
    - `corr_id: String`（可选，链路追踪）
- **事件适用场景**：通知/状态变化/广播（不需要返回值）。
- **显式调用适用场景**：必须返回值或强一致（例如 `SaveMgr.save()`、`InventoryMgr.has_item()`）。

### 3) 防止“引用乱套”的硬规则（建议写入校验）

1. **禁止业务脚本到处 `get_first_node_in_group("mgr:*")`**  
   - group 查找只允许在 `Managers` 的装配阶段发生一次，并将引用/依赖注入到需要的对象中。
2. **禁止 `Mgr` 互相随意持有引用形成依赖网**  
   - 跨 feature 协作优先走 `EventBus`，或提取 `core/contracts` 里的抽象接口再由 `Managers` 注入实现。
3. **Fail fast**  
   - 缺少必需 `Mgr`、重复 `Mgr`、group 不匹配等，应在关卡启动初期（`Managers._ready()`）明确报错。

---

## 推荐目录结构（Core / Systems / Features / Content / Levels）

> 关键点：`level` 与 `prefab` 往往会同时涉及多个 feature，因此 **不强制 level/prefab 归属于单一 feature**。

```
res://
  game/
    core/
      events/              # EventBus + GameEvent + EventTypes（结构化事件）
      contracts/           # 跨模块抽象接口/数据结构（建议依赖“契约”而非具体实现）
      foundation/          # 日志、工具、通用组件

    systems/
      <system_name>/
        scripts/           # 可复用“系统能力”（偏 ECS/system 语义）：inventory/save/time/audio 等
        README.md          # system 边界、contracts、事件（建议）

    features/
      <feature_name>/
        scripts/           # 玩法域能力：combat/quest/character 等（尽量不依赖具体关卡/内容）
        README.md          # feature 边界、事件清单、contracts 依赖、如何测试（强烈建议）

    content/
      prefabs/             # 可复用内容（实体/组件/触发器等），允许被多个 systems/features/levels 复用
      fx/ audio/ anim/ ... # 资源类目录（按需）

    levels/                # 关卡入口场景（组合层）；每个关卡根节点含 Managers + EventBus

  ui/
    core/
    systems/               # 可选：UI 侧系统能力（输入路由、UI 状态等）
    features/
    content/               # 可选：UI 复用控件（prefabs）
    levels/                # UI flow/容器场景（组合层）

  tests/
    core/                  # FakeEventBus、TestHarness、通用 fixtures
    systems/
      <system_name>/       # system 单元测试（最小场景 + 测试脚本）
    features/
      <feature_name>/      # feature 单元测试（最小场景 + 测试脚本）
    levels/                # 可选：关卡级集成测试/回归用例
```

### 分层含义（简单记忆）

- `game/core/*`：**基础设施**（事件、contracts、工具）
- `game/systems/*`：**系统能力**（可复用、被多个 feature 依赖的“服务型系统”）
- `game/features/*`：**玩法能力**（面向玩家体验/玩法流程的业务域）
- `game/content/*`：**内容**（可复用实体与资源，尽量“哑”，不绑定某 feature）
- `game/levels/*`：**组合**（关卡装配多个 feature + 多个 content）

---

## 依赖规则（建议强约束）

> 目标：允许合理引用，同时避免环依赖与跨目录“隐式耦合”。

- `features -> systems`：允许（常态）。
- `systems -> features`：不建议/默认禁止（避免基础系统被玩法绑死）。
- `features <-> features`：默认通过 `EventBus` 协作；需要返回值时先抽取 contract 到 `core/contracts`，再由 `Managers` 注入实现。
- `systems <-> systems`：允许但要克制；优先 contracts + events，避免形成环。

---

## 单元测试策略（建议）

### 1) Feature 单元测试（推荐）

- 在 `tests/features/<feature_name>/` 下维护：
  - `TestHarness.tscn`（最小场景）：`Managers + EventBus + 被测 Mgr/Prefab + fake 依赖`
  - 测试脚本：通过 `FakeEventBus` 记录与断言事件序列/数据。

### 2) 关卡级回归测试（可选）

- 在 `tests/levels/` 下加载真实关卡场景，跑启动校验与关键路径 smoke test。

---

## Godotter 固化形态（建议）

### Skill（优先）

- `godotter project new`：生成上述目录结构 + `Managers`/`EventBus`/`GameEvent` 模板。
- `godotter feature new <name>`：生成 `features/<name>/scripts` + `README.md` + 对应 `tests/features/<name>` 模板。

### CLI（配套）

- `godotter runtime validate-structure`：
  - 校验目录结构是否齐全（core/features/content/levels/tests）。
- `godotter runtime validate-managers`：
  - 扫描关卡：是否存在 `Managers`、是否有 `EventBus`、每类 `Mgr` 是否唯一、group 命名是否正确。

---

## 待定问题（需要进一步收敛）

1. `GameEvent.data` 是否做 schema 校验（严格字段 vs 宽松约定）。
2. `content/prefabs` 是否允许包含轻量脚本逻辑（以及逻辑边界）。
3. 跨 feature 的共享逻辑如何上移：放 `core` 还是单独 `shared`（目前建议统一进 `core`）。

---

## 模板项目（Template Project）建议

当规范较多时，推荐维护一个 **模板项目目录**（template），用于新项目直接复制并改名，避免：
- 手工搭目录结构时漏掉 tests/core、EventBus 等关键骨架
- 规范文档分散在外部仓库，导致新项目里缺少“就地说明”

Godotter 仓库可维护模板：
- `templates/godotter_game_template/`

并让 `godotter project new` 优先从该模板复制生成（模板不存在时再回退到最小脚手架）。
