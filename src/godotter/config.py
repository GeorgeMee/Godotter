from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file='.env',
        env_file_encoding='utf-8',
        extra='ignore',
    )

    app_name: str = 'godotter'
    app_env: str = Field(default='development', alias='GODOTTER_ENV')
    log_level: str = Field(default='INFO', alias='GODOTTER_LOG_LEVEL')
    workspace_root: Path = Field(default_factory=Path.cwd, alias='GODOTTER_WORKSPACE_ROOT')
    memory_path: Path = Field(default=Path('.godotter/memory.md'), alias='GODOTTER_MEMORY_PATH')
    default_mode: str = Field(default='plan', alias='GODOTTER_DEFAULT_MODE')
    default_brain: str = Field(default='stub', alias='GODOTTER_DEFAULT_BRAIN')

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

    @property
    def resolved_memory_path(self) -> Path:
        if self.memory_path.is_absolute():
            return self.memory_path
        return self.workspace_root / self.memory_path


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()