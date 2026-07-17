from godotter.services.chat.session_types import ChatSession
from godotter.services.chat.session_repository import ChatSessionRepository
from godotter.services.chat.reply_service import ReplyService
from godotter.services.chat.session_service import SessionService


def test_generate_reply_reuses_history_and_appends_scout_context(monkeypatch, tmp_path):
    service = ReplyService(type('S', (), {'workspace_root': tmp_path, 'resolved_chat_brain': 'stub'})())

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
        'godotter.services.chat.reply_service.build_chat_scout_context',
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
    session_service = SessionService(type('S', (), {'workspace_root': tmp_path, 'resolved_chat_brain': 'stub'})())
    service = ReplyService(
        type('S', (), {'workspace_root': tmp_path, 'resolved_chat_brain': 'stub'})(),
        session_service,
    )
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
    monkeypatch.setattr('godotter.services.chat.reply_service.build_chat_scout_context', lambda *_: None)

    result = service.generate_reply_for_session(session, no_scout=True)

    assert result.reply_text == 'reply'
    assert session.messages[-1]['role'] == 'user'
    assert session.brain_name == 'stub'


def test_session_service_create_list_archive_roundtrip(tmp_path):
    repo = ChatSessionRepository(tmp_path)
    service = SessionService(type('S', (), {'workspace_root': tmp_path, 'resolved_chat_brain': 'stub'})(), repository=repo)

    active = service.create_session('demo', title='Active chat', session_id='cs_active', brain_name='stub')
    archived = service.create_session('demo', title='Archived chat', session_id='cs_archived', brain_name='stub')
    service.archive_session(archived)

    sessions = service.list_sessions(project_name='demo', status='all')
    assert [session.session_id for session in sessions] == ['cs_archived', 'cs_active']

    active_only = service.list_sessions(project_name='demo', status='active')
    assert [session.session_id for session in active_only] == ['cs_active']

    archived_only = service.list_sessions(project_name='demo', status='archived')
    assert [session.session_id for session in archived_only] == ['cs_archived']
    assert archived_only[0].status == 'archived'
    assert service.load_session('cs_active').title == 'Active chat'


def test_session_service_detail_and_reload_keep_title(tmp_path):
    repo = ChatSessionRepository(tmp_path)
    service = SessionService(type('S', (), {'workspace_root': tmp_path, 'resolved_chat_brain': 'stub'})(), repository=repo)

    session = service.create_session('demo', title='Pinned title', session_id='cs_demo', brain_name='stub')
    service.append_message(session, role='user', content='hello')
    service.record_operation(
        session,
        tool_name='replace_text',
        args={'path': 'sample.txt'},
        affected_paths=['sample.txt'],
        before_hash={'sample.txt': 'before'},
        after_hash={'sample.txt': 'after'},
        forward_patch='--- a/sample.txt\n+++ b/sample.txt\n@@ -1 +1 @@\n-a\n+b\n',
        inverse_patch='--- a/sample.txt\n+++ b/sample.txt\n@@ -1 +1 @@\n-b\n+a\n',
    )

    detail = service.session_detail('cs_demo')
    assert detail['session'].session_id == 'cs_demo'
    assert len(detail['messages']) == 1
    assert len(detail['operations']) == 1

    loaded = service.load_or_create_session('demo', session_id='cs_demo', title='Chat', brain_name='stub')
    assert loaded.title == 'Pinned title'
