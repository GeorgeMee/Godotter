from __future__ import annotations

from pathlib import Path

from godotter.llm import SUPPORTED_PROVIDERS
from godotter.llm.catalog import (
    current_key_for_provider,
    current_model_for_provider,
    key_env_name,
    list_models,
    model_env_name,
)
from godotter.utils.envfile import EnvFile


def normalize_provider_name(name: str) -> str:
    normalized = name.strip().lower()
    if normalized not in SUPPORTED_PROVIDERS:
        raise ValueError(f'Unsupported provider: {name}')
    return normalized


def format_provider_rows(settings) -> list[str]:
    rows: list[str] = []
    for provider in SUPPORTED_PROVIDERS:
        marker = '*' if provider == settings.default_brain else ' '
        model = current_model_for_provider(settings, provider)
        rows.append(f'{marker} {provider}: model={model}')
    return rows


def format_provider_key_status(settings, provider: str) -> str:
    selected = normalize_provider_name(provider)
    if selected == 'stub':
        return 'provider=stub\nkey=not-required'
    key = current_key_for_provider(settings, selected)
    status = 'configured' if key else 'missing'
    return f'provider={selected}\nstatus={status}\nkey={mask_secret(key)}'


def fetch_model_rows(settings, provider: str) -> list[str]:
    selected = normalize_provider_name(provider)
    models = list_models(settings, selected)
    current = current_model_for_provider(settings, selected)
    rows = [f'provider={selected}']
    for model in models:
        marker = '*' if model == current else ' '
        rows.append(f'{marker} {model}')
    return rows


def set_default_provider(provider: str, env_path: Path = Path('.env')) -> str:
    selected = normalize_provider_name(provider)
    EnvFile(env_path).set('GODOTTER_DEFAULT_BRAIN', selected)
    return selected


def set_provider_key(provider: str, value: str, env_path: Path = Path('.env')) -> tuple[str, str]:
    selected = normalize_provider_name(provider)
    env_name = key_env_name(selected)
    EnvFile(env_path).set(env_name, value)
    return selected, mask_secret(value)


def set_model_for_provider(provider: str, model: str, env_path: Path = Path('.env')) -> tuple[str, str]:
    selected = normalize_provider_name(provider)
    env_name = model_env_name(selected)
    EnvFile(env_path).set(env_name, model)
    return selected, model


def mask_secret(value: str | None) -> str:
    if not value:
        return '(not set)'
    if len(value) <= 8:
        return '*' * len(value)
    return f'{value[:4]}...{value[-4:]}'
