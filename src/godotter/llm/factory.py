from __future__ import annotations

from godotter.config import Settings
from godotter.llm.openai_compatible import OpenAICompatibleBrain
from godotter.llm.providers import SUPPORTED_PROVIDERS, build_provider_spec
from godotter.llm.types import Brain, StubBrain


def create_brain(settings: Settings, provider: str | None = None, *, model_override: str | None = None) -> Brain:
    selected = (provider or settings.default_brain).strip().lower()
    if selected == 'stub':
        return StubBrain()
    spec = build_provider_spec(settings, selected, model_override=model_override)
    return OpenAICompatibleBrain(provider=spec)


__all__ = ['SUPPORTED_PROVIDERS', 'create_brain']