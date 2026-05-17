from pathlib import Path

from godotter.utils import EnvFile


def test_envfile_set_adds_and_updates_values(tmp_path: Path):
    path = tmp_path / '.env'
    envfile = EnvFile(path)
    envfile.set('GODOTTER_DEFAULT_BRAIN', 'moonshot')
    envfile.set('MOONSHOT_MODEL', 'kimi-k2.6')
    envfile.set('GODOTTER_DEFAULT_BRAIN', 'deepseek')
    content = path.read_text(encoding='utf-8')
    assert 'GODOTTER_DEFAULT_BRAIN=deepseek' in content
    assert 'MOONSHOT_MODEL=kimi-k2.6' in content


def test_envfile_can_store_provider_key(tmp_path: Path):
    path = tmp_path / '.env'
    envfile = EnvFile(path)
    envfile.set('MOONSHOT_API_KEY', 'sk-test-secret')
    content = path.read_text(encoding='utf-8')
    assert 'MOONSHOT_API_KEY=sk-test-secret' in content


def test_envfile_replaces_indented_and_duplicate_keys(tmp_path: Path):
    path = tmp_path / '.env'
    path.write_text('  MOONSHOT_API_KEY=old\nMOONSHOT_API_KEY=older\n', encoding='utf-8')
    envfile = EnvFile(path)
    envfile.set('MOONSHOT_API_KEY', 'new-secret')
    content = path.read_text(encoding='utf-8').splitlines()
    assert content == ['MOONSHOT_API_KEY=new-secret']