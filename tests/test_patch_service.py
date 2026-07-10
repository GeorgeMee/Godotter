from pathlib import Path

import pytest

from godotter.services.project.patches import PatchService


def test_generate_text_replace_patch_roundtrips(tmp_path):
    target = tmp_path / 'sample.txt'
    target.write_text('alpha\nbeta\n', encoding='utf-8')

    service = PatchService(tmp_path)
    result = service.generate_text_replace_patch('sample.txt', 'beta', 'gamma')

    assert '--- a/sample.txt' in result.patch
    assert '+++ b/sample.txt' in result.patch
    assert '+gamma' in result.patch


def test_generate_file_patch_rejects_bad_patch(tmp_path, monkeypatch):
    target = tmp_path / 'sample.txt'
    target.write_text('alpha\nbeta\n', encoding='utf-8')

    service = PatchService(tmp_path)

    def fake_generate_patch(self, target_path, original, updated):
        return '--- a/sample.txt\n+++ b/sample.txt\n@@ -1 +1,2 @@\n-bad\n+bar\n'

    monkeypatch.setattr(PatchService, '_generate_patch', fake_generate_patch)

    with pytest.raises(ValueError, match='Generated patch'):
        service.generate_file_patch('sample.txt', 'alpha\nbeta\n')
