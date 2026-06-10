import json


def test_web_health_importable():
    try:
        from godotter_web.app import (
            _append_message,
            _append_plan_error,
            _append_run_event,
            _enrich_run_artifacts,
            _extract_prefixed_path,
            _create_plan_review,
            _create_run_job,
            _create_session,
            _find_active_run_for_review,
            _load_review,
            _run_process_key,
            _ensure_gitignore_excludes_godotter,
            _set_default_project,
            _set_env_key,
            _safe_git_relpath,
            _project_tree,
            _list_runs,
            _read_run_events,
            _session_detail,
            _update_review_status,
            app,
            chat_state,
            health,
            project_summary,
            projects_get,
            projects_list,
            project_session_reply_create,
        )
    except ModuleNotFoundError:
        # web extra not installed in this environment
        return

    assert health() == {'ok': True}
    state = chat_state()
    assert state['ok'] is True
    assert state['workflow']['default_mode'] == 'plan-first'
    assert _safe_git_relpath('src/app.gd') == 'src/app.gd'
    assert callable(_project_tree)
    assert callable(_ensure_gitignore_excludes_godotter)
    assert callable(_set_env_key)
    assert callable(_set_default_project)
    assert callable(_load_review)
    assert isinstance(projects_get(_FakeRequest()), str)
    projects = projects_list(_FakeRequest())
    assert projects['ok'] is True
    if projects['projects']:
        first = projects['projects'][0]['name']
        assert project_summary(first, _FakeRequest())['ok'] is True


def test_web_routes():
    try:
        from fastapi.testclient import TestClient
        from godotter_web.app import app
    except ModuleNotFoundError:
        return
    except RuntimeError as exc:
        if 'testclient' in str(exc).lower() or 'httpx' in str(exc).lower():
            return
        raise

    client = TestClient(app)
    assert client.get('/health').json() == {'ok': True}
    assert client.get('/api/chat/state').json()['workflow']['default_mode'] == 'plan-first'
    assert client.get('/config').status_code == 200
    assert client.get('/projects').status_code == 200
    assert client.get('/api/projects').status_code == 200
    env_response = client.get('/env')
    assert env_response.status_code == 200
    assert 'GODOTTER_DEFAULT_BRAIN' in env_response.text
    assert client.get('/api/projects.toml').status_code == 200
    response = client.get('/')
    assert response.status_code == 200
    assert 'text/html' in response.headers.get('content-type', '')


def test_secondary_pages_share_shell():
    try:
        from fastapi.testclient import TestClient
        from godotter_web.app import app
    except ModuleNotFoundError:
        return
    except RuntimeError as exc:
        if 'testclient' in str(exc).lower() or 'httpx' in str(exc).lower():
            return
        raise

    client = TestClient(app)
    for route, marker in (
        ('/projects', 'Current: Projects'),
        ('/config', 'Current: Config'),
        ('/env', 'Current: Env'),
    ):
        response = client.get(route)
        assert response.status_code == 200
        assert 'Workspace' in response.text
        assert 'Tools' in response.text
        assert marker in response.text
        assert 'data-drawer-toggle' in response.text
        assert 'drawer-backdrop' in response.text
        assert "drawer-open" in response.text


def test_task_status_frontend_preserves_runtime_state():
    from pathlib import Path

    script = Path('src/godotter_web/static/app.js').read_text(encoding='utf-8')
    assert 'let taskRuntimeRunId = null;' in script
    assert 'if (taskRuntimeRunId !== currentRun.run_id)' in script
    assert 'taskRuntimeStatus[taskId] = taskRuntimeStatus[taskId] ||' in script
    assert 'event.type === "command" || event.type === "stdout"' in script


def test_task_page_mentions_runstate_and_verify_report():
    from pathlib import Path

    html = Path('src/godotter_web/static/index.html').read_text(encoding='utf-8')
    script = Path('src/godotter_web/static/app.js').read_text(encoding='utf-8')
    styles = Path('src/godotter_web/static/styles.css').read_text(encoding='utf-8')
    assert 'id="task-summary"' in html
    assert 'class="global-topbar"' in html
    assert 'gtab-view' in html
    assert '<button class="gtab gtab-view" data-view="task">Task</button>' in html
    assert 'function normalizeView(view)' in script
    assert '.global-topbar' in styles
    assert 'history.pushState(null, "", `#${activeView}`)' in script
    assert '.drawer-backdrop' in styles
    assert '@media (max-width: 1279px), (pointer: coarse)' in styles
    assert '@media (max-width: 560px)' in styles
    assert 'function renderTaskSummary(review)' in script
    assert 'function applyPlanStateToRuntime(review)' in script
    assert 'plan_state?.task_status' in script
    assert 'function summaryHint(review, run, verify)' in script
    assert 'function runtimeDetailsHtml(runtime)' in script
    assert 'runtime.runstate' in script
    assert 'runtime.verify_report' in script
    assert 'flow-status' not in html


def test_build_download_frontend_and_api(tmp_path):
    from pathlib import Path

    html = Path('src/godotter_web/static/index.html').read_text(encoding='utf-8')
    script = Path('src/godotter_web/static/app.js').read_text(encoding='utf-8')
    assert 'data-view="build"' in html
    assert 'id="build-list"' in html
    assert 'id="build-form"' in html
    assert 'id="build-submit"' in html
    assert 'id="build-doctor"' in html
    assert 'async function loadBuilds()' in script
    assert 'async function submitBuild(event)' in script
    assert 'async function runBuildDoctor()' in script
    assert 'document.getElementById("build-form").addEventListener("submit", submitBuild)' in script
    assert '/builds/${encodeURIComponent(build.build_id)}/download/' in script
    assert '/builds/doctor' in script


def test_git_frontend_controls_are_present():
    from pathlib import Path

    html = Path('src/godotter_web/static/index.html').read_text(encoding='utf-8')
    script = Path('src/godotter_web/static/app.js').read_text(encoding='utf-8')
    assert 'data-view="git"' in html
    assert 'id="git-refresh"' in html
    assert 'id="git-init"' in html
    assert 'id="git-fetch"' in html
    assert 'id="git-pull"' in html
    assert 'id="git-push"' in html
    assert 'id="git-branch-select"' in html
    assert 'id="git-checkout"' in html
    assert 'id="git-branches"' in html
    assert 'id="git-commit-form"' in html
    assert 'async function loadGitStatus()' in script
    assert 'function renderGitBranches(git)' in script
    assert 'function renderGitCommits(commits)' in script
    assert 'async function checkoutGitBranch()' in script
    assert 'async function initGitRepo()' in script
    assert 'async function submitGitCommit(event)' in script
    assert '/git/status' in script
    assert '/git/init' in script
    assert '/git/checkout' in script
    assert '/git/commit' in script


def test_git_path_validation_rejects_parent_paths():
    try:
        from fastapi import HTTPException
        from godotter_web.app import _safe_git_relpath
    except ModuleNotFoundError:
        return

    try:
        _safe_git_relpath('../secret.txt')
    except HTTPException as exc:
        assert exc.status_code == 400
    else:
        raise AssertionError('expected invalid git path to be rejected')


def test_git_status_summary_handles_non_repo(tmp_path):
    try:
        from godotter_web.app import _git_status_summary
    except ModuleNotFoundError:
        return

    summary = _git_status_summary(tmp_path)
    assert summary['is_repo'] is False
    assert summary['files'] == []
    assert summary['branches'] == []
    assert summary['commits'] == []


def test_gitignore_helper_excludes_godotter(tmp_path):
    try:
        from godotter_web.app import _ensure_gitignore_excludes_godotter
    except ModuleNotFoundError:
        return

    (tmp_path / '.gitignore').write_text('export/\n', encoding='utf-8')
    _ensure_gitignore_excludes_godotter(tmp_path)
    content = (tmp_path / '.gitignore').read_text(encoding='utf-8')
    assert 'export/' in content
    assert '.godotter/' in content


def test_set_default_project_updates_registry_and_env(tmp_path, monkeypatch):
    try:
        from godotter_web import app as web_app
        from godotter_web.app import _set_default_project
    except ModuleNotFoundError:
        return

    env_path = tmp_path / '.env'
    projects_path = tmp_path / 'config' / 'projects.toml'
    snake_root = tmp_path / 'Snake'
    tetris_root = tmp_path / 'tetris'
    snake_root.mkdir()
    tetris_root.mkdir()
    projects_path.parent.mkdir()
    projects_path.write_text(
        '\n'.join(
            [
                'default_project = "tetris"',
                '',
                '[projects.Snake]',
                f'workspace_root = "{snake_root.as_posix()}"',
                '',
                '[projects.tetris]',
                f'workspace_root = "{tetris_root.as_posix()}"',
                '',
            ]
        ),
        encoding='utf-8',
        newline='\n',
    )
    env_path.write_text('GODOTTER_DEFAULT_PROJECT=tetris\nOTHER_KEY=keep\n', encoding='utf-8')
    monkeypatch.setattr(web_app, '_env_path', lambda: env_path)
    monkeypatch.setattr(web_app, '_projects_path', lambda: projects_path)

    result = _set_default_project('Snake')

    assert result['default_project'] == 'Snake'
    assert 'default_project = "Snake"' in projects_path.read_text(encoding='utf-8')
    env_text = env_path.read_text(encoding='utf-8')
    assert 'GODOTTER_DEFAULT_PROJECT=Snake\n' in env_text
    assert 'OTHER_KEY=keep\n' in env_text


def test_load_review_includes_plan_state(tmp_path):
    try:
        from godotter_web.app import _load_review
    except ModuleNotFoundError:
        return

    plan_path = tmp_path / '.godotter' / 'plans' / 'plan_demo.json'
    state_path = tmp_path / '.godotter' / 'plans' / 'plan_demo.state.json'
    review_path = tmp_path / '.godotter' / 'sessions' / 'cs_demo' / 'reviews' / 'pr_demo.json'
    plan_path.parent.mkdir(parents=True)
    review_path.parent.mkdir(parents=True)
    plan_path.write_text('{"plan_id":"pp_demo","tasks":[]}', encoding='utf-8')
    state_path.write_text('{"plan_id":"pp_demo","updated_at":"now","task_status":{"t1":"pass"}}', encoding='utf-8')
    review_path.write_text(
        json.dumps(
            {
                'review_id': 'pr_demo',
                'session_id': 'cs_demo',
                'planpack_path': plan_path.as_posix(),
                'items': [{'item_id': 't1', 'status': 'approved'}],
            }
        ),
        encoding='utf-8',
    )

    review = _load_review(tmp_path, 'cs_demo', 'pr_demo')

    assert review['plan_state']['task_status']['t1'] == 'pass'


def test_git_status_summary_includes_branches_and_commits(tmp_path):
    import subprocess

    try:
        from godotter_web.app import _git_status_summary
    except ModuleNotFoundError:
        return

    subprocess.run(['git', 'init'], cwd=tmp_path, check=True, capture_output=True, text=True)
    subprocess.run(['git', 'config', 'user.name', 'Godotter Test'], cwd=tmp_path, check=True, capture_output=True, text=True)
    subprocess.run(['git', 'config', 'user.email', 'godotter@example.com'], cwd=tmp_path, check=True, capture_output=True, text=True)
    (tmp_path / 'README.md').write_text('# Demo\n', encoding='utf-8')
    subprocess.run(['git', 'add', 'README.md'], cwd=tmp_path, check=True, capture_output=True, text=True)
    subprocess.run(['git', 'commit', '-m', 'Initial commit'], cwd=tmp_path, check=True, capture_output=True, text=True)
    subprocess.run(['git', 'switch', '-c', 'feature/snake'], cwd=tmp_path, check=True, capture_output=True, text=True)

    summary = _git_status_summary(tmp_path)

    assert summary['is_repo'] is True
    assert summary['branch'] == 'feature/snake'
    assert any(branch['name'] == 'feature/snake' and branch['current'] for branch in summary['branches'])
    assert summary['commits'][0]['subject'] == 'Initial commit'


def test_project_tree_helper_filters_noise_files(tmp_path):
    try:
        from godotter_web.app import _project_tree
    except ModuleNotFoundError:
        return

    (tmp_path / 'game' / 'levels').mkdir(parents=True)
    (tmp_path / 'game' / 'levels' / 'main.tscn').write_text('[gd_scene]', encoding='utf-8')
    (tmp_path / 'game' / 'levels' / 'main.tscn.uid').write_text('uid://demo', encoding='utf-8')
    (tmp_path / 'game' / 'levels' / '.gitkeep').write_text('', encoding='utf-8')
    (tmp_path / '.godotter').mkdir()
    (tmp_path / '.godotter' / 'hidden.json').write_text('{}', encoding='utf-8')

    result = _project_tree(tmp_path, max_depth=3)
    payload = str(result['tree'])
    assert 'main.tscn' in payload
    assert 'main.tscn.uid' not in payload
    assert '.gitkeep' not in payload
    assert '.godotter' not in payload


def test_project_tree_frontend_controls_are_present():
    from pathlib import Path

    html = Path('src/godotter_web/static/index.html').read_text(encoding='utf-8')
    script = Path('src/godotter_web/static/app.js').read_text(encoding='utf-8')
    assert 'data-view="files"' in html
    assert 'id="tree-list"' in html
    assert 'id="tree-refresh"' in html
    assert 'async function loadProjectTree' in script
    assert 'function treeNodeHtml(node)' in script
    assert '/tree?path=' in script


def test_web_build_list_and_download_helpers(tmp_path):
    try:
        from godotter_web.app import _safe_project_file
    except ModuleNotFoundError:
        return

    artifact = tmp_path / '.godotter' / 'builds' / 'build_demo' / 'game.zip'
    artifact.parent.mkdir(parents=True)
    artifact.write_bytes(b'zip')
    resolved = _safe_project_file(tmp_path, '.godotter/builds/build_demo/game.zip')
    assert resolved == artifact.resolve()


def test_web_run_artifact_enrichment(tmp_path):
    try:
        from godotter_web.app import _enrich_run_artifacts, _extract_prefixed_path
    except ModuleNotFoundError:
        return

    runstate_path = tmp_path / '.godotter' / 'runs' / 'run_demo.json'
    verify_path = tmp_path / '.godotter' / 'reports' / 'verify' / 'vr_demo.json'
    runstate_path.parent.mkdir(parents=True)
    verify_path.parent.mkdir(parents=True)
    runstate_path.write_text('{"run_id":"run_demo","status":"pass","attempts":[{"index":1,"status":"pass"}]}', encoding='utf-8')
    verify_path.write_text('{"report_id":"vr_demo","result":"pass","summary":{"total":1,"passed":1}}', encoding='utf-8')

    stdout = f'runstate={runstate_path.as_posix()}\ntask_run_verify_report={verify_path.as_posix()}\n'
    assert _extract_prefixed_path(stdout, 'runstate=') == runstate_path.as_posix()
    enriched = _enrich_run_artifacts(
        tmp_path,
        {
            'run_id': 'rj_demo',
            'commands': [
                {
                    'task_id': 't1',
                    'exit_code': 0,
                    'runstate_path': runstate_path.as_posix(),
                    'verify_report_path': verify_path.as_posix(),
                }
            ],
        },
    )
    command = enriched['commands'][0]
    assert command['runstate']['run_id'] == 'run_demo'
    assert command['verify_report']['report_id'] == 'vr_demo'


def test_run_ids_are_explained_in_frontend():
    from pathlib import Path

    html = Path('src/godotter_web/static/index.html').read_text(encoding='utf-8')
    script = Path('src/godotter_web/static/app.js').read_text(encoding='utf-8')
    assert '执行批次（rj_*）是一轮运行' in html
    assert 'function runDisplayName(run)' in script
    assert 'function runTaskSummary(run)' in script
    assert '执行批次 ${run.run_id' in script


def test_chat_and_plan_are_separate_frontend_actions():
    from pathlib import Path

    html = Path('src/godotter_web/static/index.html').read_text(encoding='utf-8')
    script = Path('src/godotter_web/static/app.js').read_text(encoding='utf-8')
    assert 'id="plan-goal-form"' in html
    assert 'class="send-btn"' in html
    assert 'async function sendChatMessage()' in script
    assert 'async function generatePlanFromGoal()' in script
    assert '/reply' in script
    assert '/plan' in script


def test_project_local_session_storage(tmp_path):
    try:
        from godotter.tasks.planpack import PlanPack, PlanTask
        from godotter_web.app import (
            _append_message,
            _append_plan_error,
            _append_run_event,
            _create_plan_review,
            _create_run_job,
            _create_session,
            _find_active_run_for_review,
            _run_process_key,
            _list_runs,
            _read_run_events,
            _session_detail,
            _update_review_status,
        )
    except ModuleNotFoundError:
        return

    session = _create_session(tmp_path, 'demo', title='Test chat')
    message = _append_message(
        tmp_path,
        'demo',
        str(session['session_id']),
        role='user',
        content='Make a small game',
    )
    detail = _session_detail(tmp_path, str(session['session_id']))
    assert detail['session']['project_name'] == 'demo'
    error = _append_plan_error(
        tmp_path,
        'demo',
        str(session['session_id']),
        goal='Make a small game',
        brain='deepseek',
        error=RuntimeError('planner failed'),
    )
    assert error['detail'] == 'planner failed'
    assert (tmp_path / '.godotter' / 'sessions' / str(session['session_id']) / 'plan_errors.jsonl').exists()
    assert detail['messages'][0]['message_id'] == message['message_id']
    assert (tmp_path / '.godotter' / 'sessions' / f'{session["session_id"]}.json').exists()

    pack = PlanPack(
        plan_id='pp_test',
        created_at='2026-06-02T12:00:00',
        workspace_root=tmp_path.as_posix(),
        goal='Make a small game',
        tasks=[PlanTask(id='t1', title='Add scene', goal='Create a main scene')],
    )
    plan_path = tmp_path / '.godotter' / 'plans' / 'test_plan.json'
    plan_path.parent.mkdir(parents=True, exist_ok=True)
    plan_path.write_text(pack.to_json(), encoding='utf-8')
    review = _create_plan_review(tmp_path, str(session['session_id']), pack, plan_path)
    assert review['items'][0]['status'] == 'needs_review'
    review['items'][0]['status'] = 'approved'
    _update_review_status(review)
    assert review['status'] == 'approved'
    run = _create_run_job(tmp_path, 'demo', str(session['session_id']), review)
    assert run['task_ids'] == ['t1']
    assert (tmp_path / '.godotter' / 'sessions' / str(session['session_id']) / 'runs' / f'{run["run_id"]}.json').exists()
    assert _list_runs(tmp_path, str(session['session_id']))[0]['run_id'] == run['run_id']
    assert _find_active_run_for_review(tmp_path, str(session['session_id']), str(review['review_id']))['run_id'] == run['run_id']
    assert _run_process_key(str(session['session_id']), str(run['run_id'])) == f'{session["session_id"]}:{run["run_id"]}'
    _append_run_event(tmp_path, str(session['session_id']), str(run['run_id']), {'type': 'status', 'message': 'queued'})
    events = _read_run_events(tmp_path, str(session['session_id']), str(run['run_id']))
    assert events[0]['message'] == 'run_queued'
    assert events[-1]['message'] == 'queued'
    assert detail['session']['project_name'] == 'demo'


def test_planner_accepts_numbered_dependencies(tmp_path):
    try:
        from godotter_web.app import _plan_tasks_from_json
    except ModuleNotFoundError:
        return

    tasks = _plan_tasks_from_json(
        {
            'tasks': [
                {
                    'title': 'Fix first issue',
                    'goal': 'Fix first issue',
                    'scope': ['game/a.gd'],
                    'acceptance': ['Issue is fixed'],
                    'verification': ['uv run godotter runtime lint --project .'],
                },
                {
                    'title': 'Update second issue',
                    'goal': 'Update second issue',
                    'depends_on': ['1'],
                    'scope': ['game/b.gd'],
                    'acceptance': ['Second issue is fixed'],
                    'verification': ['uv run godotter runtime lint --project .'],
                },
                {
                    'id': 'custom',
                    'title': 'Add third coverage',
                    'goal': 'Add third coverage',
                    'depends_on': ['2', 'Fix first issue'],
                    'scope': ['tests/c.gd'],
                    'acceptance': ['Coverage exists'],
                    'verification': ['uv run godotter runtime test --project . --pattern "*smoke.tscn"'],
                },
            ],
        }
    )

    assert tasks[1].depends_on == ['t1']
    assert tasks[2].depends_on == ['t2', 't1']


def test_planner_rejects_non_executable_tasks():
    try:
        from fastapi import HTTPException
        from godotter_web.app import _plan_tasks_from_json
    except ModuleNotFoundError:
        return

    try:
        _plan_tasks_from_json(
            {
                'tasks': [
                    {
                        'title': 'Determine best fix',
                        'goal': 'Decide whether to add an input action',
                        'scope': ['game/features/tetris_gameplay/'],
                        'acceptance': ['Decision is made'],
                        'verification': ['grep double_down'],
                    },
                ],
            }
        )
    except HTTPException as exc:
        assert exc.status_code == 502
        assert 'planner_quality_gate_failed' in str(exc.detail)
    else:
        raise AssertionError('non-executable plan task should be rejected')


def test_planner_allows_manual_word_with_automated_verification():
    try:
        from godotter_web.app import _plan_tasks_from_json
    except ModuleNotFoundError:
        return

    tasks = _plan_tasks_from_json(
        {
            'tasks': [
                {
                    'title': 'Fix game over detection and add automated smoke coverage',
                    'goal': 'Fix the failure UI trigger; manual reproduction notes are context only.',
                    'scope': ['game/features/tetris_gameplay/', 'tests/levels/'],
                    'acceptance': ['Game over UI appears when the grid reaches the top.'],
                    'verification': ['uv run godotter runtime test --project . --pattern "*game_over*smoke.tscn"'],
                },
            ],
        }
    )
    assert tasks[0].title.startswith('Fix game over detection')


def test_planner_rejects_prose_verification_even_when_it_mentions_test():
    try:
        from fastapi import HTTPException
        from godotter_web.app import _plan_tasks_from_json
    except ModuleNotFoundError:
        return

    try:
        _plan_tasks_from_json(
            {
                'tasks': [
                    {
                        'title': 'Fix game over detection',
                        'goal': 'Fix game over detection and UI.',
                        'scope': ['game/features/tetris_gameplay/'],
                        'acceptance': ['Game over UI appears.'],
                        'verification': ['Run test_gameplay_flow.gd and confirm the game over UI is visible.'],
                    },
                ],
            }
        )
    except HTTPException as exc:
        assert exc.status_code == 502
        assert 'verification_must_contain_executable_command' in str(exc.detail)
    else:
        raise AssertionError('prose verification should be rejected')


class _FakeRequest:
    headers: dict[str, str] = {}
