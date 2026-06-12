from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        extra='ignore',
    )

    app_name: str = 'godotter'
    app_env: str = Field(default='development', alias='GODOTTER_ENV')
    log_level: str = Field(default='INFO', alias='GODOTTER_LOG_LEVEL')
    workspace_root: Path = Field(default_factory=Path.cwd, alias='GODOTTER_WORKSPACE_ROOT')
    memory_path: Path = Field(default=Path('.godotter/memory.md'), alias='GODOTTER_MEMORY_PATH')
    default_mode: str = Field(default='plan', alias='GODOTTER_DEFAULT_MODE')
    default_brain: str = Field(default='stub', alias='GODOTTER_DEFAULT_BRAIN')
    chat_brain: str | None = Field(default=None, alias='GODOTTER_CHAT_BRAIN')
    plan_brain: str | None = Field(default=None, alias='GODOTTER_PLAN_BRAIN')
    act_brain: str | None = Field(default=None, alias='GODOTTER_ACT_BRAIN')
    default_project_name: str | None = Field(default=None, alias='GODOTTER_DEFAULT_PROJECT')
    project_registry_path: Path = Field(default=Path('config/projects.toml'), alias='GODOTTER_PROJECT_REGISTRY_PATH')

    deepseek_api_key: str | None = Field(default=None, alias='DEEPSEEK_API_KEY')
    deepseek_base_url: str = Field(default='https://api.deepseek.com', alias='DEEPSEEK_BASE_URL')
    deepseek_model: str = Field(default='deepseek-v4-pro', alias='DEEPSEEK_MODEL')

    siliconflow_api_key: str | None = Field(default=None, alias='SILICONFLOW_API_KEY')
    siliconflow_base_url: str = Field(default='https://api.siliconflow.cn/v1', alias='SILICONFLOW_BASE_URL')
    siliconflow_model: str = Field(default='Qwen/Qwen3-32B', alias='SILICONFLOW_MODEL')

    alibaba_api_key: str | None = Field(default=None, alias='ALIBABA_API_KEY')
    alibaba_base_url: str = Field(default='https://dashscope.aliyuncs.com/compatible-mode/v1', alias='ALIBABA_BASE_URL')
    alibaba_model: str = Field(default='qwen-plus', alias='ALIBABA_MODEL')

    moonshot_api_key: str | None = Field(default=None, alias='MOONSHOT_API_KEY')
    moonshot_base_url: str = Field(default='https://api.moonshot.cn/v1', alias='MOONSHOT_BASE_URL')
    moonshot_model: str = Field(default='kimi-k2.6', alias='MOONSHOT_MODEL')

    godot_path: str | None = Field(default=None, alias='GODOT_PATH')
    gdcli_path: str | None = Field(default=None, alias='GDCLI_PATH')
    export_templates_path: str | None = Field(default=None, alias='GODOTTER_EXPORT_TEMPLATES_PATH')
    android_sdk_path: str | None = Field(default=None, alias='GODOTTER_ANDROID_SDK_PATH')
    java_home: str | None = Field(default=None, alias='GODOTTER_JAVA_HOME')
    android_keystore_path: str | None = Field(default=None, alias='GODOTTER_ANDROID_KEYSTORE_PATH')
    android_keystore_pass: str | None = Field(default=None, alias='GODOTTER_ANDROID_KEYSTORE_PASS')
    android_keystore_user: str | None = Field(default=None, alias='GODOTTER_ANDROID_KEYSTORE_USER')
    chat_model: str | None = Field(default=None, alias='GODOTTER_CHAT_MODEL')
    plan_model: str | None = Field(default=None, alias='GODOTTER_PLAN_MODEL')
    act_model: str | None = Field(default=None, alias='GODOTTER_ACT_MODEL')
    design_brain: str | None = Field(default=None, alias='GODOTTER_DESIGN_BRAIN')
    design_model: str | None = Field(default=None, alias='GODOTTER_DESIGN_MODEL')
    projects_root: str = Field(default='./tmp', alias='GODOTTER_PROJECTS_ROOT')

    @property
    def resolved_chat_brain(self) -> str:
        chat = getattr(self, 'chat_brain', None)
        return (chat or self.default_brain).strip().lower()

    @property
    def resolved_plan_brain(self) -> str:
        plan = getattr(self, 'plan_brain', None)
        return (plan or self.default_brain).strip().lower()

    @property
    def resolved_act_brain(self) -> str:
        act = getattr(self, 'act_brain', None)
        return (act or self.default_brain).strip().lower()

    @property
    def resolved_design_brain(self) -> str:
        design = getattr(self, 'design_brain', None)
        return (design or self.default_brain).strip().lower()

    @property
    def resolved_memory_path(self) -> Path:
        if self.memory_path.is_absolute():
            return self.memory_path
        return self.workspace_root / self.memory_path

    @property
    def resolved_project_registry_path(self) -> Path:
        if self.project_registry_path.is_absolute():
            return self.project_registry_path
        return Path.cwd() / self.project_registry_path


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    # Load .env for CLI usage without coupling tests (or library usage) to filesystem state.
    # Shell environment variables still take precedence.
    #
    # We search upward from the current working directory so a repo-level `.env`
    # can configure shared API keys for multiple Godot projects, while still
    # allowing a per-project `.env` to override by being closer.
    try:
        from dotenv import dotenv_values  # type: ignore
    except Exception:
        dotenv_values = None
    if dotenv_values is not None:
        cwd = Path.cwd().resolve()
        env_paths: list[Path] = []
        for candidate in [*reversed(list(cwd.parents)), cwd]:
            p = candidate / '.env'
            if p.exists():
                env_paths.append(p)

        merged: dict[str, str] = {}
        for p in env_paths:
            values = dotenv_values(p, encoding='utf-8')
            for k, v in values.items():
                if k and v is not None:
                    merged[str(k)] = str(v)

        import os

        for k, v in merged.items():
            # Do not override shell environment variables.
            os.environ[k] = v
    return Settings()
