# Godotter

## Vision

Godotter 是一个面向 Godot 项目的、Workflow-first、CLI-first、Headless-friendly 的 AI Assisted Development System。

它并不追求成为一个“通用聊天 Agent”，而是：

- 面向 Godot 游戏开发
- 面向 Linux 云端运行
- 面向自动化工作流
- 面向结构化工具调用
- 面向稳定可恢复的软件工程系统

Godotter 的核心思想是：

> 使用 LLM 协调可控工作流，而不是让 LLM 直接控制整个系统。

---

# Recommended Repository Structure

```text
Godotter/
├── Docs/
│   ├── Product_Positioning.md
│   ├── Product_Functions.md
│   ├── Architecture.md
│   ├── Development_Plan.md
│   ├── Workflow_Design.md
│   ├── Tooling_and_Runtime.md
│   ├── Context_Engineering.md
│   ├── Safety_and_Policies.md
│   └── Future_Roadmap.md
│
├── References/
│   ├── aider/
│   ├── openhands/
│   ├── swe-agent/
│   ├── gdcli/
│   ├── tree-sitter/
│   └── textual/
│
├── src/
│   └── godotter/
│       ├── agent/
│       ├── workflows/
│       ├── runtime/
│       ├── tools/
│       ├── context/
│       ├── policies/
│       ├── git/
│       ├── llm/
│       ├── interfaces/
│       └── utils/
│
├── tests/
├── pyproject.toml
├── uv.lock
├── README.md
└── .env
```

---

# Product Positioning

## Godotter Is

- AI-assisted Godot workflow runtime
- Structured coding system
- Headless automation platform
- Patch-driven development assistant
- Workflow orchestration layer

---

## Godotter Is NOT

- Chatbot
- Autonomous AGI
- Generic Linux agent
- Browser automation framework
- Shell wrapper

---

## Core Philosophy

### Workflow-first

Godotter 更关注：

```text
workflow
state
validation
recovery
```

而不是：

```text
prompt magic
```

---

### Tool-driven

LLM 不直接自由操作 Linux。

而是：

```text
LLM
 ↓
Structured Tools
 ↓
Godot Runtime
```

---

### Patch-driven Editing

Godotter 采用：

- unified diff
- partial patch
- git-aware editing
- rollback support

而不是 rewrite whole file。

---

# Initial Product Features

## Repository Understanding

- repo map
- symbol extraction
- scene discovery
- dependency indexing

---

## Patch Editing

- generate patch
- apply patch
- rollback patch
- summarize diff

---

## Godot Workflow Runtime

支持：

- validate project
- inspect scene
- headless run
- resource scan
- export workflow
- lint gdscript

---

## Feishu Integration

支持：

- workflow trigger
- validation summary
- patch summary
- build notification

---

# High-level Architecture

```text
Feishu / CLI
      ↓
Godotter Orchestrator
      ↓
Workflow Engine
      ↓
Context Engine
      ↓
LLM Runtime
      ↓
Tool Layer
      ↓
Godot Runtime
```

---

# Workflow Design

## Example Workflow

```text
User Request
 ↓
Search Repository
 ↓
Inspect Scene
 ↓
Read Related Files
 ↓
Generate Patch
 ↓
Apply Patch
 ↓
Validate
 ↓
Run Headless Checks
 ↓
Return Summary
```

---

## Workflow States

```text
PENDING
RUNNING
FAILED
VALIDATING
COMPLETED
ROLLED_BACK
```

---

# Context Engineering

## Core Idea

对于大型 Godot 项目：

```text
Context selection
比
Prompt wording
更重要
```

---

## Future Techniques

- tree-sitter indexing
- embedding retrieval
- repo graph
- scene graph indexing
- semantic chunking

---

# Tooling and Runtime

## Programming Language

```text
Python 3.12+
```

---

## Environment Management

使用：

```text
uv
```

初始化：

```bash
uv init
uv venv
uv sync
```

---

## Recommended Dependencies

### CLI

- typer
- rich
- textual

---

### LLM

- litellm
- openai
- anthropic

---

### Git

- GitPython
- unidiff

---

### Runtime

- pexpect
- asyncio

---

### Code Analysis

- tree-sitter
- tree-sitter-gdscript

---

### Logging

- structlog

---

# Development Plan

## Phase 0 - Foundation

目标：

建立基础项目。

---

### Tasks

- uv setup
- git setup
- CLI setup
- config system
- logging system

---

## Phase 1 - Minimal Workflow Agent

目标：

实现最小可用版本。

---

### Features

- read file
- search code
- apply patch
- validate project
- headless check

---

### Non-goals

- MCP
- multi-agent
- browser automation
- autonomous shell

---

## Phase 2 - Context Engine

实现：

- repo indexing
- symbol extraction
- dependency graph
- scene understanding

---

## Phase 3 - Workflow Runtime

实现：

- retry loop
- rollback
- queue system
- async jobs

---

# Safety Policies

## Principle

Godotter 不允许：

- unrestricted shell access
- destructive operations
- uncontrolled filesystem editing

---

## Recovery Strategy

支持：

- git checkpoint
- rollback
- patch revert
- task restart

---

# Future Vision

Godotter 最终目标：

```text
AI-assisted Godot Development Runtime
```

而不是：

```text
chatbot
```

---

# Recommended Local Development Flow

```text
Windows Local Development
        ↓
Git Commit
        ↓
Push to GitHub
        ↓
Linux Pull
        ↓
uv sync
        ↓
Run Godotter
```

---

# Recommended Linux Runtime

```text
Ubuntu 24.04 LTS
Python 3.12+
uv
tmux
Docker (future)
Redis (future)
```

---

# Initial CLI Design

```bash
godotter patch
godotter validate
godotter inspect
godotter workflow
godotter scene
godotter build
godotter test
```

---

# Engineering Philosophy

## Godotter prioritizes:

- deterministic workflows
- structured tools
- recoverable runtime
- validation-first execution
- patch-based editing
- workflow orchestration

instead of:

- free-form autonomous behavior
- prompt tricks
- unrestricted shell execution

