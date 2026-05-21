"""
Feature/System 拆分实验脚本（OpenAI-compatible Chat Completions）

用途
  - 你把“玩法描述”粘贴进去或在命令行输入，多轮对话迭代拆分结果
  - 模型必须输出严格 JSON（方便你做 diff / review / 后处理）
  - 不绑定 Godotter：只是一个本地实验工具

运行
  uv run python experiments/feature_system_splitter_chat.py
  # 或者
  python experiments/feature_system_splitter_chat.py

切换提供商/模型
  - 编辑 CONFIG 区域：通过注释/取消注释选择 base_url / model / api_key_env
  - 建议把 key 放到环境变量，不要写死在文件里
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import requests


# Try to load .env automatically (project root) for convenience.
# Environment variables set in the shell still take precedence.
def _load_dotenv_default() -> None:
    try:
        from dotenv import load_dotenv  # type: ignore
    except Exception:
        return

    repo_root = Path(__file__).resolve().parents[1]
    env_path = repo_root / ".env"
    if env_path.exists():
        load_dotenv(dotenv_path=env_path, override=False)


# =========================
# CONFIG（按需注释切换）
# =========================

@dataclass(frozen=True, slots=True)
class ProviderConfig:
    name: str
    base_url: str
    model: str
    api_key_env: str


# 1) DeepSeek
CONFIG = ProviderConfig(
    name="deepseek",
    base_url="https://api.deepseek.com/v1",
    model="deepseek-v4-pro",
    api_key_env="DEEPSEEK_API_KEY",
)

# 2) Moonshot（Kimi）
# CONFIG = ProviderConfig(
#     name="moonshot",
#     base_url="https://api.moonshot.cn/v1",
#     model="kimi-k2.6",
#     api_key_env="MOONSHOT_API_KEY",
# )

# 3) SiliconFlow
# CONFIG = ProviderConfig(
#     name="siliconflow",
#     base_url="https://api.siliconflow.cn/v1",
#     model="Qwen/Qwen3-32B",
#     api_key_env="SILICONFLOW_API_KEY",
# )

# 4) Alibaba DashScope compatible-mode
# CONFIG = ProviderConfig(
#     name="alibaba",
#     base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
#     model="qwen-plus",
#     api_key_env="ALIBABA_API_KEY",
# )

# 5) OpenAI（如需）
# CONFIG = ProviderConfig(
#     name="openai",
#     base_url="https://api.openai.com/v1",
#     model="gpt-4.1-mini",
#     api_key_env="OPENAI_API_KEY",
# )


# =========================
# Prompt（可迭代版本化）
# =========================

SYSTEM_PROMPT = r"""
你是游戏项目的架构拆分助手。输入是一段玩法/需求描述。你必须输出严格 JSON，不要输出任何多余文字。

拆分目标（面向无头/agent 开发与单元测试）：
- systems：可复用系统能力（跨 feature 依赖常态，API 稳定，尽量不依赖 features）
- features：只包含**游戏核心逻辑/玩法域脚本**（流程/规则/交互），不包含 UI/渲染/音效等表现层内容
- content：prefabs/resources（跨系统/feature 复用的内容，尽量“哑”）
- 本次不要输出 levels（关卡/模式在架构拆分阶段往往是噪声）。`levels` 字段必须存在但固定输出空数组 `[]`。

拆分约束：
- 默认禁止 feature -> feature 直接实现依赖；跨 feature 协作优先用结构化事件（EventBus）
- 需要返回值/强一致的能力用 contracts + Managers 注入（系统接口优先）
- 每关卡一个 Managers，同类 Mgr 唯一；业务脚本禁止到处 get-from-group
- 事件避免“每帧/高频事件风暴”（如 soft_drop 每 tick 事件）；优先发布聚合事件（例如 drop_performed、lock_resolved、clear_resolved）
- 明确“唯一权威”（single source of truth）：例如锁定/消行/胜负判定由一个系统权威负责，避免多个系统同时修改同一状态

必须包含的系统（若玩法描述适用）：
- game_flow_system（或同等职责）：状态机/流程编排，管理 playing/paused/game_over，以及 spawn->fall->lock->resolve->next 的时序
- 对战/多人时：将 network 作为 adapter（net_session_adapter），业务规则在 garbage_system 等系统内，不要把业务塞进网络层

输出 JSON schema：
{
  "summary": "...一句话总结玩法",
  "assumptions": ["..."],
  "systems": [
    {
      "name": "inventory",
      "responsibilities": ["..."],
      "public_contracts": ["IInventory", "..."],
      "events_published": [{"type": "item_added", "data": {"item_id":"string","count":"int"}}],
      "events_subscribed": [{"type": "...", "reason": "..."}],
      "test_plan": ["...最小单测思路"]
    }
  ],
  "features": [
    {
      "name": "character_progression",
      "responsibilities": ["..."],
      "depends_on_systems": ["inventory", "save"],
      "events_published": [...],
      "events_subscribed": [...],
      "content_assets": ["prefab:res://game/content/prefabs/..."],
      "test_plan": ["..."]
    }
  ],
  "ui": [],
  "content": [{"kind":"prefab|resource", "name":"...", "notes":"..."}],
  "levels": [],
  "risk_checks": ["可能出现的环依赖点", "命名冲突点", "哪些地方容易做成隐式依赖"]
}

硬性要求：
- 输出必须是一个 JSON 对象（顶层是 {}）
- 不允许 Markdown，不允许代码块标记
- 字段缺失时也要给空数组/空字符串，不要省略键
- `ui` 字段固定输出空数组（表现层设计不在本次范围）
""".strip()


def _now_stamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _chat_completions(
    *,
    base_url: str,
    api_key: str,
    model: str,
    messages: list[dict[str, str]],
    stream: bool = False,
    timeout_s: int = 90,
) -> str:
    url = base_url.rstrip("/") + "/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": 0.2,
        "response_format": {"type": "json_object"},
    }
    if not stream:
        resp = requests.post(url, headers=headers, json=payload, timeout=timeout_s)
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"]

    payload["stream"] = True
    resp = requests.post(url, headers=headers, json=payload, timeout=timeout_s, stream=True)
    resp.raise_for_status()
    return _read_sse_stream(resp)


def _read_sse_stream(resp: requests.Response) -> str:
    """
    Read OpenAI-compatible SSE stream and return full assistant content.
    Prints partial content to stdout as it arrives.
    """
    chunks: list[str] = []
    for line in resp.iter_lines(decode_unicode=True):
        if not line:
            continue
        if line.startswith("data: "):
            data_str = line[6:].strip()
        else:
            continue
        if data_str == "[DONE]":
            break
        try:
            event = json.loads(data_str)
        except json.JSONDecodeError:
            continue

        delta = (
            event.get("choices", [{}])[0]
            .get("delta", {})
            .get("content")
        )
        if not delta:
            continue
        chunks.append(delta)
        sys.stdout.write(delta)
        sys.stdout.flush()
    sys.stdout.write("\n")
    sys.stdout.flush()
    return "".join(chunks)


def _ensure_json_object(text: str) -> dict[str, Any]:
    # 一些提供商偶尔会返回多余空白；这里做最小容错
    text = text.strip()
    obj = json.loads(text)
    if not isinstance(obj, dict):
        raise ValueError("Model output is not a JSON object")
    return obj


def main() -> int:
    _load_dotenv_default()
    api_key = os.getenv(CONFIG.api_key_env, "").strip()
    if not api_key:
        print(
            f"Missing API key env var: {CONFIG.api_key_env} (you can set it in shell env or in .env at repo root)",
            file=sys.stderr,
        )
        return 2

    stream = True
    if "--no-stream" in sys.argv:
        stream = False
    if "--stream" in sys.argv:
        stream = True

    out_dir = Path(".godotter") / "workpacks"
    out_dir.mkdir(parents=True, exist_ok=True)
    transcript_path = out_dir / f"feature_split_{CONFIG.name}_{_now_stamp()}.jsonl"

    messages: list[dict[str, str]] = [{"role": "system", "content": SYSTEM_PROMPT}]
    print(f"provider={CONFIG.name} model={CONFIG.model}")
    print(f"stream={str(stream).lower()}")
    print("输入玩法描述（支持多行）。空行+空行结束输入：")

    # First user message (multi-line)
    buf: list[str] = []
    empty_count = 0
    while True:
        line = sys.stdin.readline()
        if not line:
            break
        line = line.rstrip("\n")
        if line.strip() == "":
            empty_count += 1
            if empty_count >= 2:
                break
        else:
            empty_count = 0
        buf.append(line)

    user_text = "\n".join(buf).strip()
    if not user_text:
        print("No input provided.", file=sys.stderr)
        return 2

    messages.append({"role": "user", "content": user_text})

    round_idx = 1
    while True:
        try:
            raw = _chat_completions(
                base_url=CONFIG.base_url,
                api_key=api_key,
                model=CONFIG.model,
                messages=messages,
                stream=stream,
            )
            obj = _ensure_json_object(raw)
        except Exception as exc:
            print(f"ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
            return 1

        if not stream:
            print(json.dumps(obj, ensure_ascii=False, indent=2))

        with transcript_path.open("a", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps({"round": round_idx, "role": "assistant", "content": obj}, ensure_ascii=False) + "\n")

        # Ask for next instruction
        print("\n继续迭代？输入补充/修改要求（空行直接退出）：")
        follow = sys.stdin.readline()
        if not follow or not follow.strip():
            print(f"saved_transcript={transcript_path.as_posix()}")
            return 0

        messages.append({"role": "assistant", "content": json.dumps(obj, ensure_ascii=False)})
        messages.append({"role": "user", "content": follow.strip()})
        round_idx += 1


if __name__ == "__main__":
    raise SystemExit(main())
