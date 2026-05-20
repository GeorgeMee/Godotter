from pathlib import Path


def test_setup_script_is_location_independent():
    content = Path('setup_godotter.sh').read_text(encoding='utf-8')
    assert '/home/Godots/Godotter' not in content
    assert 'SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"' in content
    assert 'exec uv run godotter "\\$@"' in content


def test_remove_script_is_location_independent():
    content = Path('remove_godotter.sh').read_text(encoding='utf-8')
    assert '/home/Godots/Godotter' not in content
    assert 'SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"' in content


def test_generated_wrapper_is_ignored_locally():
    content = Path('.gitignore').read_text(encoding='utf-8')
    assert 'godotter.sh' in content
