from godotter.services.chat.session_repository import ChatSessionRepository


def test_chat_session_repository_roundtrip(tmp_path):
    repo = ChatSessionRepository(tmp_path)

    session = repo.create_session('demo', title='Hello')
    assert session.project_name == 'demo'
    assert session.title == 'Hello'

    loaded = repo.load_session(session.session_id)
    assert loaded.session_id == session.session_id
    assert loaded.project_name == 'demo'

    message = repo.append_message(
        session.session_id,
        project_name='demo',
        role='user',
        content='hello world',
    )
    assert message['role'] == 'user'
    assert repo.read_messages(session.session_id)[0]['content'] == 'hello world'

    record = repo.append_operation(
        session.session_id,
        operation={'tool_name': 'replace_text', 'args': {'path': 'sample.txt'}, 'status': 'applied'},
    )
    assert record['operation_id'].startswith('op_')
    assert repo.read_operations(session.session_id)[0]['tool_name'] == 'replace_text'

    repo.append_message(
        session.session_id,
        project_name='demo',
        role='assistant',
        content='done',
    )
    assert repo.read_operations(session.session_id)[0]['tool_name'] == 'replace_text'

    detail_session = repo.load_session(session.session_id)
    assert detail_session.title == 'Hello'
    assert detail_session.operation_history[0]['tool_name'] == 'replace_text'
