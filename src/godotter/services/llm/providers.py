from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import requests

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
        default_marker = '*' if provider == settings.default_brain else ''
        tasks: list[str] = []
        if provider == (settings.chat_brain or '').strip().lower():
            tasks.append('chat')
        if provider == (settings.plan_brain or '').strip().lower():
            tasks.append('plan')
        if provider == (settings.act_brain or '').strip().lower():
            tasks.append('act')
        task_tag = f' [{", ".join(tasks)}]' if tasks else ''
        model = current_model_for_provider(settings, provider)
        rows.append(f'{default_marker or " "} {provider}:{task_tag} model={model}')
    return rows


def format_provider_key_status(settings, provider: str) -> str:
    selected = normalize_provider_name(provider)
    if selected == 'stub':
        return 'provider=stub\nkey=not-required'
    key = current_key_for_provider(settings, selected)
    status = 'configured' if key else 'missing'
    return f'provider={selected}\nstatus={status}\nkey={mask_secret(key)}'


def check_provider_connectivity(settings, provider: str, timeout: int = 10) -> str:
    selected = normalize_provider_name(provider)
    if selected == 'stub':
        return 'provider=stub\nok=true\nnote=not-required'

    from godotter.llm.providers import build_provider_spec

    try:
        spec = build_provider_spec(settings, selected)
    except ValueError as exc:
        return f'provider={selected}\nok=false\nerror={exc}'

    url = f'{spec.base_url}/models'
    try:
        response = requests.get(
            url,
            headers={'Authorization': f'Bearer {spec.api_key}'},
            timeout=timeout,
        )
        response.raise_for_status()
        data: dict[str, Any] = response.json()
        models = [item.get('id') for item in data.get('data', []) if item.get('id')]
        return f'provider={selected}\nbase_url={spec.base_url}\nok=true\nmodels={len(models)}'
    except requests.HTTPError:
        body_preview = _safe_json_preview(response)
        return (
            f'provider={selected}\nbase_url={spec.base_url}\nok=false\n'
            f'status_code={response.status_code}\nerror=http_error\nbody={body_preview}'
        )
    except requests.RequestException as exc:
        return f'provider={selected}\nbase_url={spec.base_url}\nok=false\nerror={type(exc).__name__}: {exc}'


def _safe_json_preview(response: requests.Response, max_len: int = 240) -> str:
    try:
        data = response.json()
        text = json.dumps(data, ensure_ascii=False)
    except ValueError:
        text = (response.text or '').strip()
    text = ' '.join(text.split())
    if len(text) > max_len:
        return f'{text[:max_len]}...'
    return text or '(empty)'


def fetch_model_rows(settings, provider: str) -> list[str]:
    selected = normalize_provider_name(provider)
    models = list_models(settings, selected)
    current = current_model_for_provider(settings, selected)
    rows = [f'provider={selected}']
    for model in models:
        marker = '*' if model == current else ' '
        rows.append(f'{marker} {model}')
    return rows


def set_default_provider(provider: str, env_path: Path = Path('.env'), *, task: str | None = None) -> str:
    selected = normalize_provider_name(provider)
    if task:
        task_key = {'chat': 'GODOTTER_CHAT_BRAIN', 'plan': 'GODOTTER_PLAN_BRAIN', 'act': 'GODOTTER_ACT_BRAIN'}.get(task)
        if task_key is None:
            raise ValueError(f'Invalid task: {task}. Use chat, plan, or act.')
        EnvFile(env_path).set(task_key, selected)
    else:
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
