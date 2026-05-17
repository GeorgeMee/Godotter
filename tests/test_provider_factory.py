from godotter.config import Settings
from godotter.llm import SUPPORTED_PROVIDERS, StubBrain, create_brain
from godotter.llm.catalog import current_key_for_provider, current_model_for_provider, key_env_name, model_env_name
from godotter.llm.openai_compatible import OpenAICompatibleBrain


def test_supported_providers_contains_china_targets():
    assert 'deepseek' in SUPPORTED_PROVIDERS
    assert 'siliconflow' in SUPPORTED_PROVIDERS
    assert 'alibaba' in SUPPORTED_PROVIDERS
    assert 'moonshot' in SUPPORTED_PROVIDERS


def test_create_stub_brain_returns_stub():
    settings = Settings(GODOTTER_DEFAULT_BRAIN='stub')
    brain = create_brain(settings)
    assert isinstance(brain, StubBrain)


def test_create_deepseek_brain_uses_configured_china_endpoint():
    settings = Settings(
        GODOTTER_DEFAULT_BRAIN='deepseek',
        DEEPSEEK_API_KEY='test-key',
        DEEPSEEK_BASE_URL='https://api.deepseek.com',
        DEEPSEEK_MODEL='deepseek-v4-pro',
    )
    brain = create_brain(settings)
    assert isinstance(brain, OpenAICompatibleBrain)
    assert brain.provider.region == 'china'
    assert brain.provider.base_url == 'https://api.deepseek.com'
    assert brain.provider.model == 'deepseek-v4-pro'


def test_create_moonshot_brain_uses_china_endpoint():
    settings = Settings(
        GODOTTER_DEFAULT_BRAIN='moonshot',
        MOONSHOT_API_KEY='test-key',
        MOONSHOT_BASE_URL='https://api.moonshot.cn/v1',
        MOONSHOT_MODEL='kimi-k2.6',
    )
    brain = create_brain(settings)
    assert isinstance(brain, OpenAICompatibleBrain)
    assert brain.provider.region == 'china'
    assert brain.provider.base_url == 'https://api.moonshot.cn/v1'
    assert brain.provider.model == 'kimi-k2.6'


def test_model_env_name_maps_remote_providers():
    assert model_env_name('deepseek') == 'DEEPSEEK_MODEL'
    assert model_env_name('moonshot') == 'MOONSHOT_MODEL'


def test_key_env_name_maps_remote_providers():
    assert key_env_name('deepseek') == 'DEEPSEEK_API_KEY'
    assert key_env_name('moonshot') == 'MOONSHOT_API_KEY'


def test_current_model_for_stub_is_stub():
    settings = Settings(GODOTTER_DEFAULT_BRAIN='stub')
    assert current_model_for_provider(settings, 'stub') == 'stub'


def test_current_key_for_provider_reads_configured_key():
    settings = Settings(DEEPSEEK_API_KEY='secret-key')
    assert current_key_for_provider(settings, 'deepseek') == 'secret-key'