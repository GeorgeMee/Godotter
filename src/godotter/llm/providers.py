from __future__ import annotations

from dataclasses import dataclass

from godotter.config import Settings


@dataclass(frozen=True, slots=True)
class ProviderSpec:
    name: str
    region: str
    base_url: str
    model: str
    api_key: str


SUPPORTED_PROVIDERS = ('stub', 'deepseek', 'siliconflow', 'alibaba', 'moonshot')


def build_provider_spec(settings: Settings, provider: str, *, model_override: str | None = None) -> ProviderSpec:
    normalized = provider.strip().lower()
    model = model_override or getattr(settings, f'{normalized}_model', None) or ''
    if normalized == 'deepseek':
        return ProviderSpec(
            name='deepseek',
            region='china',
            base_url=settings.deepseek_base_url.rstrip('/'),
            model=model or settings.deepseek_model,
            api_key=_require_key('DEEPSEEK_API_KEY', settings.deepseek_api_key),
        )
    if normalized == 'siliconflow':
        return ProviderSpec(
            name='siliconflow',
            region='china',
            base_url=settings.siliconflow_base_url.rstrip('/'),
            model=model or settings.siliconflow_model,
            api_key=_require_key('SILICONFLOW_API_KEY', settings.siliconflow_api_key),
        )
    if normalized == 'alibaba':
        return ProviderSpec(
            name='alibaba',
            region='china',
            base_url=settings.alibaba_base_url.rstrip('/'),
            model=model or settings.alibaba_model,
            api_key=_require_key('ALIBABA_API_KEY', settings.alibaba_api_key),
        )
    if normalized == 'moonshot':
        return ProviderSpec(
            name='moonshot',
            region='china',
            base_url=settings.moonshot_base_url.rstrip('/'),
            model=model or settings.moonshot_model,
            api_key=_require_key('MOONSHOT_API_KEY', settings.moonshot_api_key),
        )
    raise ValueError(f'Unsupported provider: {provider}')


def _require_key(env_name: str, value: str | None) -> str:
    if value:
        return value
    raise ValueError(f'{env_name} is not configured')