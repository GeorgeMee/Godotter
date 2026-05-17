from __future__ import annotations

from typing import Any

import requests

from godotter.config import Settings
from godotter.llm.providers import ProviderSpec, build_provider_spec


PROVIDER_MODEL_ENV = {
    'deepseek': 'DEEPSEEK_MODEL',
    'siliconflow': 'SILICONFLOW_MODEL',
    'alibaba': 'ALIBABA_MODEL',
    'moonshot': 'MOONSHOT_MODEL',
}

PROVIDER_KEY_ENV = {
    'deepseek': 'DEEPSEEK_API_KEY',
    'siliconflow': 'SILICONFLOW_API_KEY',
    'alibaba': 'ALIBABA_API_KEY',
    'moonshot': 'MOONSHOT_API_KEY',
}


def current_model_for_provider(settings: Settings, provider: str) -> str:
    normalized = provider.strip().lower()
    if normalized == 'deepseek':
        return settings.deepseek_model
    if normalized == 'siliconflow':
        return settings.siliconflow_model
    if normalized == 'alibaba':
        return settings.alibaba_model
    if normalized == 'moonshot':
        return settings.moonshot_model
    if normalized == 'stub':
        return 'stub'
    raise ValueError(f'Unsupported provider: {provider}')


def current_key_for_provider(settings: Settings, provider: str) -> str | None:
    normalized = provider.strip().lower()
    if normalized == 'deepseek':
        return settings.deepseek_api_key
    if normalized == 'siliconflow':
        return settings.siliconflow_api_key
    if normalized == 'alibaba':
        return settings.alibaba_api_key
    if normalized == 'moonshot':
        return settings.moonshot_api_key
    if normalized == 'stub':
        return None
    raise ValueError(f'Unsupported provider: {provider}')


def model_env_name(provider: str) -> str:
    normalized = provider.strip().lower()
    if normalized not in PROVIDER_MODEL_ENV:
        raise ValueError(f'Provider does not have a configurable remote model: {provider}')
    return PROVIDER_MODEL_ENV[normalized]


def key_env_name(provider: str) -> str:
    normalized = provider.strip().lower()
    if normalized not in PROVIDER_KEY_ENV:
        raise ValueError(f'Provider does not have a configurable remote key: {provider}')
    return PROVIDER_KEY_ENV[normalized]


def list_models(settings: Settings, provider: str) -> list[str]:
    normalized = provider.strip().lower()
    if normalized == 'stub':
        return ['stub']
    spec = build_provider_spec(settings, normalized)
    return _list_remote_models(spec)


def _list_remote_models(spec: ProviderSpec) -> list[str]:
    response = requests.get(
        f'{spec.base_url}/models',
        headers={'Authorization': f'Bearer {spec.api_key}'},
        timeout=60,
    )
    response.raise_for_status()
    data: dict[str, Any] = response.json()
    models = [item['id'] for item in data.get('data', []) if item.get('id')]
    return sorted(models)