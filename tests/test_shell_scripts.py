from pathlib import Path


def test_install_script_is_location_independent():
    content = Path('install_godotter.sh').read_text(encoding='utf-8')
    assert '/home/Godots/Godotter' not in content
    assert 'SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"' in content
    assert 'exec uv run $command_name "\\$@"' in content
    assert 'register_command "gdt"' in content
    assert 'register_command "godotter"' in content


def test_uninstall_script_is_location_independent():
    content = Path('uninstall_godotter.sh').read_text(encoding='utf-8')
    assert '/home/Godots/Godotter' not in content
    assert 'SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"' in content
    assert 'remove_command "gdt"' in content
    assert 'remove_command "godotter"' in content


def test_generated_wrapper_is_ignored_locally():
    content = Path('.gitignore').read_text(encoding='utf-8')
    assert 'gdt.sh' in content
    assert 'godotter.sh' in content
