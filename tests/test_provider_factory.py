import requests

from godotter.config import Settings
from godotter.llm import SUPPORTED_PROVIDERS, StubBrain, create_brain
from godotter.llm.catalog import current_key_for_provider, current_model_for_provider, key_env_name, model_env_name
from godotter.llm.openai_compatible import OpenAICompatibleBrain
from godotter.llm.providers import ProviderSpec


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


def test_openai_compatible_brain_surfaces_http_error_body(monkeypatch):
    brain = OpenAICompatibleBrain(
        provider=ProviderSpec(
            name='moonshot',
            region='china',
            base_url='https://api.moonshot.cn/v1',
            model='kimi-k2.6',
            api_key='test-key',
        )
    )

    response = requests.Response()
    response.status_code = 400
    response._content = b'{"error":{"message":"tool message malformed"}}'
    response.url = 'https://api.moonshot.cn/v1/chat/completions'

    def fake_post(*args, **kwargs):
        return response

    monkeypatch.setattr('godotter.llm.openai_compatible.requests.post', fake_post)

    try:
        brain.think([{'role': 'user', 'content': 'hello'}])
    except RuntimeError as exc:
        message = str(exc)
        assert 'provider=moonshot' in message
        assert 'status_code=400' in message
        assert 'tool message malformed' in message
    else:
        raise AssertionError('Expected RuntimeError for HTTP 400 response')


def test_openai_compatible_brain_roundtrips_reasoning_content_in_assistant_messages(monkeypatch):
    brain = OpenAICompatibleBrain(
        provider=ProviderSpec(
            name='deepseek',
            region='china',
            base_url='https://api.deepseek.com',
            model='deepseek-v4-pro',
            api_key='test-key',
        )
    )

    captured = {}

    class FakeResponse:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {
                'choices': [
                    {
                        'message': {
                            'content': 'done',
                            'tool_calls': [],
                        }
                    }
                ],
                'usage': {'prompt_tokens': 12},
            }

    def fake_post(*args, **kwargs):
        captured['json'] = kwargs['json']
        return FakeResponse()

    monkeypatch.setattr('godotter.llm.openai_compatible.requests.post', fake_post)

    conversation = [
        {'role': 'user', 'content': 'inspect project'},
        {
            'role': 'assistant',
            'content': 'planning',
            'reasoning_content': 'trace-1',
            'tool_calls': [{'id': 'tool-1', 'name': 'git_status', 'args': {}}],
        },
        {'role': 'tool', 'tool_call_id': 'tool-1', 'content': '## main'},
    ]

    brain.think(conversation)

    assistant_entry = next(item for item in captured['json']['messages'] if item['role'] == 'assistant')
    assert assistant_entry['role'] == 'assistant'
    assert assistant_entry['reasoning_content'] == 'trace-1'
