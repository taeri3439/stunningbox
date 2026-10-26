import json

import pytest

from engine.legacy import CheckedTranslator


class Delegate:
    def __init__(self, respond):
        self.respond, self.calls = respond, []

    def translate(self, texts, *args, **kwargs):
        self.calls.append(list(texts))
        return self.respond(texts)


def test_empty_item_retries_only_that_item_and_keeps_structure():
    delegate = Delegate(lambda texts: ['PUMP', '', 'PASS'] if len(texts) == 3 else ['MIXER'])
    checked = CheckedTranslator(delegate)
    assert checked.translate(['1) pump\n  mixer ☑ pass □\n  123\n ....']) == ['1) PUMP\n  MIXER ☑ PASS □\n  123\n ....']
    assert delegate.calls == [['pump', 'mixer', 'pass'], ['mixer']]
    assert not checked.review_texts


@pytest.mark.parametrize('malformed', [None, 'abc', {}, ['wrong'], ['', None, 42]])
def test_malformed_batches_recover_without_shifting_alignment(malformed):
    delegate = Delegate(lambda texts: malformed if len(texts) == 3 else [t.upper() for t in texts])
    checked = CheckedTranslator(delegate)
    assert checked.translate(['one', 'two', 'three']) == ['ONE', 'TWO', 'THREE']


def test_persistent_empty_keeps_whole_region_and_does_not_cache_failure(tmp_path):
    delegate = Delegate(lambda texts: ['GOOD' if t == 'good' else '' for t in texts])
    checked = CheckedTranslator(delegate, tmp_path)
    assert checked.translate(['good\n  broken', 'good']) == ['good\n  broken', 'GOOD']
    assert checked.review_texts == {'good\n  broken'}
    assert len(delegate.calls) == 5  # bounded recovery
    assert len(list(tmp_path.glob('*.json'))) == 1
    second = Delegate(lambda texts: [t.upper() for t in texts])
    retried = CheckedTranslator(second, tmp_path)
    assert retried.translate(['good\n  broken']) == ['GOOD\n  BROKEN']
    assert second.calls == [['broken']]


def test_successes_survive_connection_failure(tmp_path):
    def respond(texts):
        if len(texts) == 2:
            return ['OK', '']
        raise ConnectionError('disconnected')
    checked = CheckedTranslator(Delegate(respond), tmp_path)
    with pytest.raises(ConnectionError):
        checked.translate(['first', 'second'])
    next_delegate = Delegate(lambda texts: ['SECOND'])
    assert CheckedTranslator(next_delegate, tmp_path).translate(['first', 'second']) == ['OK', 'SECOND']
    assert next_delegate.calls == [['second']]


def test_truncated_json_is_recovered():
    def respond(texts):
        if len(texts) > 1:
            raise json.JSONDecodeError('truncated', '{', 1)
        return [texts[0].upper()]
    assert CheckedTranslator(Delegate(respond)).translate(['one', 'two']) == ['ONE', 'TWO']


def test_long_batches_are_bounded_and_progress_is_accurate():
    delegate = Delegate(lambda texts: texts)
    steps = []
    texts = [str(i) + 'a' * 1000 for i in range(45)]
    checked = CheckedTranslator(delegate, progress=lambda stage, done, total: steps.append((done, total)) if stage=='translate' else None)
    assert checked.translate(texts) == texts
    assert all(sum(map(len, call)) <= 6000 for call in delegate.calls)
    assert steps == [(i, len(delegate.calls)) for i in range(len(delegate.calls) + 1)]


def test_all_structural_text_never_calls_api():
    delegate = Delegate(lambda texts: pytest.fail('structural input sent to API'))
    text = '123\n  .... ☑ □ 456'
    assert CheckedTranslator(delegate).translate([text]) == [text]


def test_partial_result_is_reviewable_and_not_cached_as_complete(tmp_path, monkeypatch):
    from engine import legacy
    from engine.inspect import inspect_pdf
    from test_lab import make_pdf
    legacy.setup()
    from pipeline import translator
    monkeypatch.setattr(legacy, 'configuration', lambda: {'configured': True, 'provider': 'test', 'model': 'test'})
    monkeypatch.setattr(translator, 'get_translator', lambda: Delegate(lambda texts: ['' for _ in texts]))
    layout = inspect_pdf(make_pdf(tmp_path / 'source.pdf'))
    cache = tmp_path / 'cache'
    result = legacy.translate_shared(layout, 'en', 'ko', cache)
    assert result['incomplete_ids']
    assert set(result['incomplete_ids']) <= set(result['unchanged_ids'])
    assert not list(cache.glob('*.json'))
    assert result['recovery']['invalid_responses']
