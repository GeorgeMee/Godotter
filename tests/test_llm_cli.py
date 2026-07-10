from typer.testing import CliRunner
from pathlib import Path
from types import SimpleNamespace

from godotter.interfaces.gdt_cli import app
from godotter.services.chat.session_service import ChatReplyResult


runner = CliRunner()


class _FakeSettings:
    def __init__(self, workspace_root, default_brain='stub'):
        self.workspace_root = workspace_root
        self.default_brain = default_brain

    def model_copy(self, update=None):
        updated = _FakeSettings(self.workspace_root, self.default_brain)
        if update:
            for key, value in update.items():
                setattr(updated, key, value)
        return updated


def test_model_use_validates_model_against_current_provider(monkeypatch):
    monkeypatch.setattr(
        'godotter.interfaces.commands.llm.get_settings',
        lambda: type('S', (), {'default_brain': 'deepseek'})(),
    )
    monkeypatch.setattr(
        'godotter.interfaces.commands.llm.list_models',
        lambda settings, provider: ['deepseek-v4-flash', 'deepseek-v4-pro'] if provider == 'deepseek' else [],
    )

    captured: dict[str, str] = {}

    def fake_set_model_for_provider(provider, model, env_path=None):
        captured['provider'] = provider
        captured['model'] = model
        return provider, model

    monkeypatch.setattr('godotter.interfaces.commands.llm.set_model_for_provider', fake_set_model_for_provider)

    result = runner.invoke(app, ['model', 'use', 'deepseek-v4-pro'])

    assert result.exit_code == 0
    assert captured == {'provider': 'deepseek', 'model': 'deepseek-v4-pro'}
    assert 'deepseek model set to deepseek-v4-pro' in result.stdout


def test_model_use_rejects_unknown_model(monkeypatch):
    monkeypatch.setattr(
        'godotter.interfaces.commands.llm.get_settings',
        lambda: type('S', (), {'default_brain': 'deepseek'})(),
    )
    monkeypatch.setattr(
        'godotter.interfaces.commands.llm.list_models',
        lambda settings, provider: ['deepseek-v4-flash', 'deepseek-v4-pro'] if provider == 'deepseek' else [],
    )

    called = {'set': False}

    def fake_set_model_for_provider(provider, model, env_path=None):
        called['set'] = True
        return provider, model

    monkeypatch.setattr('godotter.interfaces.commands.llm.set_model_for_provider', fake_set_model_for_provider)

    result = runner.invoke(app, ['model', 'use', 'not-a-real-model'])

    assert result.exit_code != 0
    assert called['set'] is False


def test_chat_message_option_uses_single_turn(monkeypatch):
    captured: dict[str, object] = {}

    def fake_generate_reply(self, **kwargs):
        captured.update(kwargs)
        return ChatReplyResult(
            reply_text='pong',
            conversation=[{'role': 'user', 'content': 'hello'}],
            workspace_root=Path('D:/Godots/Engines/Godotter/tmp'),
            brain_name='stub',
            mode='plan',
            user_message='hello',
            enriched_message='hello',
        )

    monkeypatch.setattr('godotter.interfaces.commands.chat.ChatSessionService.generate_reply', fake_generate_reply)
    monkeypatch.setattr(
        'godotter.interfaces.commands.chat.get_settings',
        lambda: _FakeSettings(workspace_root=Path('D:/Godots/Engines/Godotter/tmp')),
    )
    monkeypatch.setattr('godotter.interfaces.commands.chat.configure_logging', lambda settings: None)
    monkeypatch.setattr(
        'godotter.interfaces.commands.chat.resolve_runtime_target',
        lambda settings, project=None: SimpleNamespace(workspace_root=settings.workspace_root),
    )

    result = runner.invoke(app, ['chat', '--message', 'hello'])

    assert result.exit_code == 0
    assert result.stdout.strip() == 'pong'
    assert captured['messages'] == [{'role': 'user', 'content': 'hello'}]
    assert captured['mode'] == 'plan'


def test_chat_defaults_to_interactive_and_supports_mode_switch(monkeypatch):
    prompts = iter(['hello', '/mode act', 'second', '/q'])
    captured: list[dict[str, object]] = []

    def fake_input(prompt):
        return next(prompts)

    def fake_generate_reply(self, **kwargs):
        captured.append(kwargs)
        last_message = kwargs['messages'][-1]['content']
        return ChatReplyResult(
            reply_text=f'reply:{last_message}',
            conversation=list(kwargs['messages']) + [{'role': 'assistant', 'content': f'reply:{last_message}'}],
            workspace_root=Path('D:/Godots/Engines/Godotter/tmp'),
            brain_name='stub',
            mode=kwargs['mode'],
            user_message=last_message,
            enriched_message=last_message,
        )

    monkeypatch.setattr('builtins.input', fake_input)
    monkeypatch.setattr('godotter.interfaces.commands.chat.ChatSessionService.generate_reply', fake_generate_reply)
    monkeypatch.setattr(
        'godotter.interfaces.commands.chat.get_settings',
        lambda: _FakeSettings(workspace_root=Path('D:/Godots/Engines/Godotter/tmp')),
    )
    monkeypatch.setattr('godotter.interfaces.commands.chat.configure_logging', lambda settings: None)
    monkeypatch.setattr(
        'godotter.interfaces.commands.chat.resolve_runtime_target',
        lambda settings, project=None: SimpleNamespace(workspace_root=settings.workspace_root),
    )

    result = runner.invoke(app, ['chat'])

    assert result.exit_code == 0
    assert 'Interactive chat.' in result.stdout
    assert 'reply:hello' in result.stdout
    assert 'mode=act' in result.stdout
    assert 'reply:second' in result.stdout
    assert len(captured) == 2
    assert captured[0]['mode'] == 'plan'
    assert captured[1]['mode'] == 'act'


def test_chat_supports_rollback_command(monkeypatch):
    prompts = iter(['hello', '/rollback', '/q'])
    captured: list[dict[str, object]] = []
    rollback_calls: list[object] = []

    def fake_input(prompt):
        return next(prompts)

    def fake_generate_reply(self, **kwargs):
        captured.append(kwargs)
        last_message = kwargs['messages'][-1]['content']
        return ChatReplyResult(
            reply_text=f'reply:{last_message}',
            conversation=list(kwargs['messages']) + [{'role': 'assistant', 'content': f'reply:{last_message}'}],
            workspace_root=Path('D:/Godots/Engines/Godotter/tmp'),
            brain_name='stub',
            mode=kwargs['mode'],
            user_message=last_message,
            enriched_message=last_message,
        )

    def fake_rollback(self, session):
        rollback_calls.append(session)
        return {
            'tool_name': 'rollback_operation',
            'args': {'target_tool_name': 'replace_text'},
            'affected_paths': ['sample.txt'],
        }

    monkeypatch.setattr('builtins.input', fake_input)
    monkeypatch.setattr('godotter.interfaces.commands.chat.ChatSessionService.generate_reply', fake_generate_reply)
    monkeypatch.setattr('godotter.interfaces.commands.chat.ChatSessionService.rollback_last_operation', fake_rollback)
    monkeypatch.setattr(
        'godotter.interfaces.commands.chat.get_settings',
        lambda: _FakeSettings(workspace_root=Path('D:/Godots/Engines/Godotter/tmp')),
    )
    monkeypatch.setattr('godotter.interfaces.commands.chat.configure_logging', lambda settings: None)
    monkeypatch.setattr(
        'godotter.interfaces.commands.chat.resolve_runtime_target',
        lambda settings, project=None: SimpleNamespace(workspace_root=settings.workspace_root),
    )

    result = runner.invoke(app, ['chat'])

    assert result.exit_code == 0
    assert 'rollback=rollback_operation target=replace_text paths=sample.txt' in result.stdout
    assert len(captured) == 1
    assert len(rollback_calls) == 1
