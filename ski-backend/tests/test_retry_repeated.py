from scripts.experiments.eval_retry_repeated import passed, summarize


def good(**kw):
    return dict(id='q1', kind='price_mixed', answered=True, cited=True,
                citation_valid=True, grounded=True, degraded=False, hit=True,
                price_question=True, stale_notice=True, seconds=1, input_tokens=10,
                output_tokens=2, **kw)


def test_price_needs_citation_completeness_and_stale_notice():
    row = good()
    assert passed(row)
    for field in ('cited', 'hit', 'grounded', 'stale_notice'):
        assert not passed(dict(row, **{field: False}))
    assert not passed(dict(row, degraded=True))


def test_refusal_requires_explicit_refusal_not_citation():
    row = good()
    assert passed(dict(row, kind='refusal', cited=False, refused=True))
    assert not passed(dict(row, kind='refusal', cited=False, refused=False))


def test_summary_preserves_failed_repeat_not_best_only():
    row = good()
    records = [{'mode': 'off', 'rows': [row]},
               {'mode': 'on', 'rows': [row]},
               {'mode': 'on', 'rows': [dict(row, hit=False)]}]
    result = summarize(records)
    assert '| on | 1/2 |' in result
    assert '| q1 | 1/1 | 1/2 |' in result


def test_repeated_cli_failure_gate_is_opt_in_and_saves_summary(monkeypatch, tmp_path):
    import json
    from pathlib import Path
    from types import SimpleNamespace
    import sys
    import pytest
    from scripts.experiments import eval_retry_repeated as evaluator

    monkeypatch.chdir(tmp_path)
    cases = tmp_path / 'cases.yaml'
    cases.write_text('questions: []\n', encoding='utf-8')
    calls = []
    successful = False
    def fake_subprocess(command, **kwargs):
        metrics = Path(command[command.index('--metrics-output') + 1])
        metrics.write_text(json.dumps([dict(good(), hit=successful)]), encoding='utf-8')
        calls.append(kwargs['env']['CITATION_RETRY_MISSING_ONLY'])
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(evaluator.subprocess, 'run', fake_subprocess)
    for strict, ok in [(False, False), (True, False), (True, True)]:
        successful = ok
        output = tmp_path / f'result-{strict}-{ok}.md'
        argv = ['repeat', '--cases', str(cases), '--rounds', '1', '--output', str(output)]
        if strict:
            argv.append('--fail-on-error')
        monkeypatch.setattr(sys, 'argv', argv)
        if strict and not ok:
            with pytest.raises(SystemExit) as exc:
                evaluator.main()
            assert exc.value.code == 1
        else:
            assert evaluator.main() is None
        assert output.is_file() and output.with_suffix('.json').is_file()
    assert calls == ['false', 'true'] * 3
