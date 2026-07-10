from godotter.services.chat.session_types import ChatSession
from godotter.services.chat.session_service import ChatSessionService


def test_generate_reply_reuses_history_and_appends_scout_context(monkeypatch, tmp_path):
    service = ChatSessionService(type('S', (), {'workspace_root': tmp_path, 'resolved_chat_brain': 'stub'})())

    class FakeBrain:
        tool_choice = 'required'

    class FakeAgent:
        def __init__(self):
            self.conversation = []
            self.brain = FakeBrain()
            self.expose_tool_output = True

        def _agentic_loop(self):
            return '\n'.join(f"{item['role']}:{item['content']}" for item in self.conversation)

    fake_agent = FakeAgent()

    monkeypatch.setattr(service, 'build_agent', lambda **kwargs: (fake_agent, None, 'stub'))
    monkeypatch.setattr(
        'godotter.services.chat.session_service.build_chat_scout_context',
        lambda workspace_root, message: f'CTX::{workspace_root.name}::{message}',
    )

    result = service.generate_reply(
        workspace_root=tmp_path,
        messages=[
            {'role': 'user', 'content': 'first'},
            {'role': 'assistant', 'content': 'ack'},
            {'role': 'user', 'content': 'second'},
        ],
        no_scout=False,
    )

    assert result.brain_name == 'stub'
    assert result.user_message == 'second'
    assert 'CTX::' in result.enriched_message
    assert fake_agent.conversation[0] == {'role': 'user', 'content': 'first'}
    assert fake_agent.conversation[1] == {'role': 'assistant', 'content': 'ack'}
    assert fake_agent.conversation[2]['role'] == 'user'
    assert 'CTX::' in fake_agent.conversation[2]['content']


def test_generate_reply_for_session_updates_session_messages(monkeypatch, tmp_path):
    service = ChatSessionService(type('S', (), {'workspace_root': tmp_path, 'resolved_chat_brain': 'stub'})())
    session = ChatSession(
        session_id='cs_1',
        workspace_root=tmp_path,
        project_name='demo',
        created_at='2026-07-10T00:00:00',
        updated_at='2026-07-10T00:00:00',
        messages=[{'role': 'user', 'content': 'hello'}],
        brain_name='stub',
    )

    class FakeAgent:
        def __init__(self):
            self.conversation = []
            self.brain = type('B', (), {'tool_choice': 'required'})()
            self.expose_tool_output = True

        def _agentic_loop(self):
            return 'reply'

    fake_agent = FakeAgent()
    monkeypatch.setattr(service, 'build_agent', lambda **kwargs: (fake_agent, None, 'stub'))
    monkeypatch.setattr('godotter.services.chat.session_service.build_chat_scout_context', lambda *_: None)

    result = service.generate_reply_for_session(session, no_scout=True)

    assert result.reply_text == 'reply'
    assert session.messages[-1]['role'] == 'user'
    assert session.brain_name == 'stub'
