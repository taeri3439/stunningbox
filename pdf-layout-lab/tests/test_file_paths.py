import json
import os
from pathlib import Path

import pytest

from engine.file_paths import io_path
from engine.legacy import CheckedTranslator
from engine.failures import file_failure


def test_long_checkpoint_path_writes_replaces_and_reuses(tmp_path):
    # Match the real layout: source digest / translations / segments / request digest / text digest.
    cache = tmp_path / ('a' * 64) / 'translations' / 'segments' / ('b' * 64)
    while len(str(cache / ('c' * 64 + '.json'))) < 287:
        cache = cache / 'long-path-component'
    class Translator:
        calls = 0
        def translate(self, texts, *args, **kwargs):
            self.calls += 1
            return ['Saved translation' for _ in texts]
    delegate = Translator()
    first = CheckedTranslator(delegate, cache)
    assert first.translate(['Source text']) == ['Saved translation']
    saved = first._saved('Source text')
    assert json.loads(saved.read_text('utf-8'))['text'] == 'Saved translation'
    second = CheckedTranslator(delegate, cache)
    assert second.translate(['Source text']) == ['Saved translation']
    assert delegate.calls == 1
    assert second.diagnostics['cached_segments'] == 1
    assert not saved.with_suffix('.tmp').exists()


@pytest.mark.skipif(os.name != 'nt', reason='Windows path semantics')
def test_windows_paths_are_absolute_idempotent_and_unc_safe():
    local = io_path(Path('relative') / 'cache')
    assert str(local).startswith('\\\\?\\')
    assert io_path(local) == local
    assert str(io_path(r'\\server\share\cache')) == r'\\?\UNC\server\share\cache'


def test_missing_file_diagnostic_does_not_leak_path():
    result = file_failure(FileNotFoundError(2, 'missing', 'private-document-name/' + 'a' * 270))
    assert result['error_code'] == 'file_not_found'
    assert result['path_length'] > 260
    assert 'private-document-name' not in str(result)
    assert file_failure(ValueError('unrelated')) is None
