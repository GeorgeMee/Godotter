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

拆分目标：
- systems：可复用系统能力（跨 feature 依赖常态，API 稳定，尽量不依赖 features）
- features：面向玩家体验的玩法域（流程/规则/交互），可依赖 systems
- content：prefabs/resources（跨系统/feature 复用的内容，尽量“哑”）
- levels：关卡是组合层，会启用多个 systems/features，不属于单一 feature

拆分约束：
- 默认禁止 feature -> feature 直接实现依赖；跨 feature 协作优先用结构化事件（EventBus）
- 需要返回值/强一致的能力用 contracts + Managers 注入（系统接口优先）
- 每关卡一个 Managers，同类 Mgr 唯一；业务脚本禁止到处 get-from-group

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
  "content": [{"kind":"prefab|resource", "name":"...", "notes":"..."}],
  "levels": [{"name":"...", "uses_systems":[...], "uses_features":[...]}],
  "risk_checks": ["可能出现的环依赖点", "命名冲突点", "哪些地方容易做成隐式依赖"]
}

硬性要求：
- 输出必须是一个 JSON 对象（顶层是 {}）
- 不允许 Markdown，不允许代码块标记
- 字段缺失时也要给空数组/空字符串，不要省略键
""".strip()


def _now_stamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _chat_completions(
    *,
    base_url: str,
    api_key: str,
    model: str,
    messages: list[dict[str, str]],
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
    resp = requests.post(url, headers=headers, json=payload, timeout=timeout_s)
    resp.raise_for_status()
    data = resp.json()
    return data["choices"][0]["message"]["content"]


def _ensure_json_object(text: str) -> dict[str, Any]:
    # 一些提供商偶尔会返回多余空白；这里做最小容错
    text = text.strip()
    obj = json.loads(text)
    if not isinstance(obj, dict):
        raise ValueError("Model output is not a JSON object")
    return obj


def main() -> int:
    api_key = os.getenv(CONFIG.api_key_env, "").strip()
    if not api_key:
        print(f"Missing API key env var: {CONFIG.api_key_env}", file=sys.stderr)
        return 2

    out_dir = Path(".godotter") / "workpacks"
    out_dir.mkdir(parents=True, exist_ok=True)
    transcript_path = out_dir / f"feature_split_{CONFIG.name}_{_now_stamp()}.jsonl"

    messages: list[dict[str, str]] = [{"role": "system", "content": SYSTEM_PROMPT}]
    print(f"provider={CONFIG.name} model={CONFIG.model}")
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
            )
            obj = _ensure_json_object(raw)
        except Exception as exc:
            print(f"ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
            return 1

        print(json.dumps(obj, ensure_ascii=False, indent=2))

        transcript_path.write_text("", encoding="utf-8") if not transcript_path.exists() else None
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

