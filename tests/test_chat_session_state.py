from godotter.services.chat.session_repository import ChatSessionRepository
from godotter.services.chat.session_service import SessionService


def test_session_records_operations_and_checkpoint(tmp_path):
    repo = ChatSessionRepository(tmp_path)
    service = SessionService(type('S', (), {'workspace_root': tmp_path, 'resolved_chat_brain': 'stub'})(), repository=repo)
    session = repo.create_session('demo')

    record = service.record_operation(
        session,
        tool_name='replace_text',
        args={'path': 'game/a.gd'},
        before_hash={'game/a.gd': 'before'},
        after_hash={'game/a.gd': 'after'},
        forward_patch='--- a',
        inverse_patch='--- b',
        affected_paths=['game/a.gd'],
    )
    assert record['tool_name'] == 'replace_text'
    assert session.operation_history[0]['after_hash']['game/a.gd'] == 'after'

    updated = service.update_checkpoint(session, checkpoint_id='cp_1', base_commit='abc123', summary_state='summary')
    assert updated.checkpoint_id == 'cp_1'
    assert updated.base_commit == 'abc123'
    assert updated.summary_state == 'summary'
    reloaded = repo.load_session(session.session_id)
    assert reloaded.checkpoint_id == 'cp_1'
    assert reloaded.base_commit == 'abc123'


def test_session_can_rollback_last_operation(tmp_path):
    repo = ChatSessionRepository(tmp_path)
    service = SessionService(type('S', (), {'workspace_root': tmp_path, 'resolved_chat_brain': 'stub'})(), repository=repo)
    session = repo.create_session('demo')

    target = tmp_path / 'game' / 'sample.txt'
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text('alpha\ngamma\n', encoding='utf-8')

    service.record_operation(
        session,
        tool_name='replace_text',
        args={'path': 'game/sample.txt', 'old_text': 'beta', 'new_text': 'gamma'},
        before_hash={'game/sample.txt': 'before'},
        after_hash={'game/sample.txt': 'after'},
        forward_patch='--- a/game/sample.txt\n+++ b/game/sample.txt\n@@ -1,2 +1,2 @@\n alpha\n-beta\n+gamma\n',
        inverse_patch='--- a/game/sample.txt\n+++ b/game/sample.txt\n@@ -1,2 +1,2 @@\n alpha\n-gamma\n+beta\n',
        affected_paths=['game/sample.txt'],
    )

    rollback = service.rollback_last_operation(session)

    assert target.read_text(encoding='utf-8') == 'alpha\nbeta\n'
    assert rollback['tool_name'] == 'rollback_operation'
    assert session.operation_history[-2]['status'] == 'reverted'
    assert session.operation_history[-1]['tool_name'] == 'rollback_operation'
    reloaded = repo.load_session(session.session_id)
    assert reloaded.operation_history[-2]['status'] == 'reverted'
