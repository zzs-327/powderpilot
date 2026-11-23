"""端到端评测的纯离线契约：无模型、数据库、向量库或 .env 访问。"""
import asyncio
import importlib.util
import logging
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest
import yaml

BACKEND = Path(__file__).resolve().parents[1]


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def evaluator(monkeypatch, tmp_path):
    # fixture 生命周期内隔离配置与logger；退出后恢复已有模块，不污染其他测试。
    settings = SimpleNamespace(DATA_DIR=tmp_path, CITATION_LOOKBACK_TURNS=1,
                               LLM_PROVIDER='offline', LLM_MODEL='fake', CARD_MODE=False)
    config = ModuleType('config.config')
    config.settings = settings
    log = ModuleType('utils.log')
    log.LogUtil = SimpleNamespace(get_logger=logging.getLogger)
    with monkeypatch.context() as isolated:
        isolated.setitem(sys.modules, 'config.config', config)
        isolated.setitem(sys.modules, 'utils.log', log)
        middleware = _load('_challenge_middleware', BACKEND / 'agent/middleware.py')
        isolated.setitem(sys.modules, 'agent.middleware', middleware)
        module = _load('_challenge_eval', BACKEND / 'scripts/eval_e2e.py')
        yield module


def msg(kind, text):
    return SimpleNamespace(type=kind, content=text)


def run(reply, messages=None):
    return dict(reply=reply, messages=messages or [], seconds=1.0,
                input_tokens=10, output_tokens=5)


@pytest.mark.parametrize('lookback,valid', [(0, False), (1, False), (2, True)])
def test_old_evidence_cannot_cross_lookback(evaluator, lookback, valid):
    evaluator.settings.CITATION_LOOKBACK_TURNS = lookback
    messages = [msg('human', '旧问题'), msg('tool', '[E101] 高级道19条'),
                msg('ai', '19条[E101]'), msg('human', '上一轮'),
                msg('tool', '[E201] 平日雪票560元'), msg('ai', '560元[E201]'),
                msg('human', '当前问题'), msg('tool', '[E301] 夜场票268元')]
    verdict = evaluator.judge({}, run('高级道19条[E101]', messages))
    assert verdict['citation_valid'] is valid
    assert verdict['grounded'] is valid


@pytest.mark.parametrize('current', [[], [msg('tool', '本库暂未收录')]])
def test_current_turn_without_evidence_cannot_borrow_history(evaluator, current):
    messages = [msg('human', '旧问题'), msg('tool', '[E101] 雪票560元'),
                msg('ai', '560元[E101]'), msg('human', '未收录雪场票价')] + current
    verdict = evaluator.judge({}, run('雪票560元[E101]', messages))
    assert not verdict['citation_valid']
    assert not verdict['grounded']


@pytest.mark.parametrize('reply,hit', [('高级道19条', False), ('雪票560元', False),
                                     ('高级道十九条，560元', True)])
def test_mixed_question_requires_every_group(evaluator, reply, hit):
    item = {'expect_groups': [['19', '十九'], ['560', '五百六十']]}
    assert evaluator.judge(item, run(reply))['hit'] is hit


def test_legacy_expectations_keep_any_match_and_number_precedence(evaluator):
    assert evaluator.judge({'expect_numbers': [19, 560]}, run('19条'))['hit']
    assert evaluator.judge({'expect_keywords': ['暂无', '未收录']}, run('暂无资料'))['hit']
    assert evaluator.judge({'expect_numbers': [19], 'expect_keywords': ['暂无']},
                           run('暂无资料'))['hit'] is False
    assert evaluator.judge({}, run('您好'))['hit'] is None
    assert evaluator.judge({'expect_numbers': [19], 'expect_groups': [['560']]},
                           run('19条'))['hit'] is False


@pytest.mark.parametrize('reply', ['', '   '])
def test_empty_answer_never_passes(evaluator, reply):
    verdict = evaluator.judge({}, run(reply))
    assert not verdict['answered']
    assert not verdict['grounded']
    assert not verdict['citation_valid']
    assert verdict['hit'] is False


def test_failure_does_not_reuse_previous_output(evaluator, monkeypatch, capsys):
    async def fake_ask(thread, question):
        if question == '失败':
            raise RuntimeError('api_key=private-secret')
        return run('高级道19条[E101]', [msg('human', question),
                                      msg('tool', '[E101] 高级道19条')])
    monkeypatch.setattr(evaluator, 'ask', fake_ask)
    questions = [{'id': 'ok', 'kind': 'mixed', 'question': '成功'},
                 {'id': 'bad', 'kind': 'mixed', 'question': '失败'}]
    rows = asyncio.run(evaluator.run_all(questions, progress=True))
    assert rows[0]['reply'] == '高级道19条[E101]'
    assert rows[1]['reply'] == ''
    assert rows[1]['error'] == 'RuntimeError'
    assert rows[1]['hit'] is False
    assert '引用0' in capsys.readouterr().out.splitlines()[-1]
    assert 'private-secret' not in str(rows)


def test_failed_followup_does_not_reuse_first_answer(evaluator, monkeypatch):
    async def fake_ask(thread, question):
        if question == '追问':
            raise ValueError('private-secret')
        return run('首问输出[E101]')
    monkeypatch.setattr(evaluator, 'ask', fake_ask)
    rows = asyncio.run(evaluator.run_all([
        {'id': 'followup', 'kind': 'mixed', 'question': '首问', 'followup': '追问'}], False))
    assert rows[0]['reply'] == ''
    assert rows[0]['error'] == 'ValueError'


def test_report_records_answer_and_exception_without_raw_state(evaluator, tmp_path):
    rows = [dict(id='bad', kind='mixed', question='题目', reply='联系 user@example.com',
                 error='RuntimeError', answered=True, cited=False, citation_valid=False,
                 grounded=False, unsupported=[], hit=False, stale_notice=False,
                 refused=False, degraded=True, price_question=False,
                 seconds=0, input_tokens=0, output_tokens=0)]
    output = tmp_path / 'report.md'
    evaluator.write_report(rows, 0, 0, output, '人工集.yaml')
    text = output.read_text(encoding='utf-8')
    assert '人工集.yaml' in text and 'RuntimeError' in text
    assert '[EMAIL]' in text and 'user@example.com' not in text
    assert '期望未命中' in text and '完整语义忠实度' in text


def test_cases_cli_does_not_overwrite_baseline(evaluator, monkeypatch, tmp_path):
    cases = tmp_path / 'challenge.yaml'
    cases.write_text('questions:\n  - {id: one, kind: refusal, question: 未收录}\n', encoding='utf-8')
    baseline = tmp_path / 'eval/last_e2e.md'
    baseline.parent.mkdir()
    baseline.write_text('original', encoding='utf-8')
    async def fake_ask(thread, question):
        return run('暂无资料')
    monkeypatch.setattr(evaluator, 'ask', fake_ask)
    monkeypatch.setattr(sys, 'argv', ['eval', '--cases', str(cases), '--quiet'])
    evaluator.main()
    assert cases.with_suffix('.md').exists()
    assert baseline.read_text(encoding='utf-8') == 'original'
    explicit = tmp_path / 'other/result.md'
    monkeypatch.setattr(sys, 'argv', ['eval', '--cases', str(cases), '--output', str(explicit)])
    evaluator.main()
    assert explicit.exists()
    monkeypatch.setattr(sys, 'argv', ['eval', '--cases', str(cases), '--output', str(baseline)])
    with pytest.raises(SystemExit, match='不能覆盖'):
        evaluator.main()
    assert baseline.read_text(encoding='utf-8') == 'original'


def test_challenge_expectations_match_curated_data():
    data = BACKEND / 'data'
    wanlong = yaml.safe_load((data / 'resorts/wanlong.yaml').read_text(encoding='utf-8'))
    assert wanlong['runs_summary']['advanced'] == 19
    assert sum(r['difficulty'] == '高级' for r in wanlong['runs']) == 19
    assert next(p['price'] for p in wanlong['prices']
                if p['item'] == '全天雪票·自带板·平日') == 560
    jiangjunshan = (data / 'resorts/jiangjunshan.yaml').read_text(encoding='utf-8')
    assert '61 条' in jiangjunshan and '合计 48 条' in jiangjunshan
    assert '未获得官方结构化雪道数据' in jiangjunshan
    assert not any('二世谷' in p.read_text(encoding='utf-8')
                   for p in (data / 'resorts').glob('*.yaml'))
    cases = yaml.safe_load((data / 'eval/e2e_challenge_set.yaml').read_text(encoding='utf-8'))
    assert len(cases['questions']) == 4
    assert all(q['expect_groups'] for q in cases['questions'])


def test_judge_error_keeps_only_current_sanitized_reply(evaluator, monkeypatch):
    async def fake_ask(thread, question):
        return run('当前输出 api_key=private-secret user@example.com')
    def broken_judge(item, result):
        raise RuntimeError('password=[FAKE_TEST_VALUE]')
    monkeypatch.setattr(evaluator, 'ask', fake_ask)
    monkeypatch.setattr(evaluator, 'judge', broken_judge)
    row = asyncio.run(evaluator.run_all([
        {'id': 'judge-error', 'kind': 'mixed', 'question': '当前题'}], False))[0]
    assert '当前输出' in row['reply']
    assert '[REDACTED]' in row['reply'] and '[EMAIL]' in row['reply']
    assert row['error'] == 'RuntimeError' and row['hit'] is False
    assert 'private-secret' not in str(row) and '[FAKE_TEST_VALUE]' not in str(row)


@pytest.mark.parametrize('fails', [False, True])
def test_cli_closes_checkpointer_on_success_and_failure(evaluator, monkeypatch, fails):
    closed = []
    class Connection:
        async def close(self):
            closed.append(True)
    service = ModuleType('service.agent_service')
    service._checkpointer = SimpleNamespace(conn=Connection())
    monkeypatch.setitem(sys.modules, 'service.agent_service', service)
    async def fake_run_all(questions, progress):
        if fails:
            raise RuntimeError('failed')
        return ['result']
    monkeypatch.setattr(evaluator, 'run_all', fake_run_all)
    if fails:
        with pytest.raises(RuntimeError, match='failed'):
            asyncio.run(evaluator.run_cli([], False))
    else:
        assert asyncio.run(evaluator.run_cli([], False)) == ['result']
    assert closed == [True]
    assert service._checkpointer is None


def test_cli_without_loaded_service_does_not_import_it(evaluator, monkeypatch):
    monkeypatch.delitem(sys.modules, 'service.agent_service', raising=False)
    assert asyncio.run(evaluator.run_cli([], False)) == []
    assert 'service.agent_service' not in sys.modules


@pytest.mark.parametrize('history', [False, True])
def test_ask_counts_only_current_turn_usage(evaluator, monkeypatch, history):
    old = [msg('human', '旧问题'), msg('ai', '旧回答')]
    old[1].usage_metadata = {'input_tokens': 1000, 'output_tokens': 500}
    current = [msg('human', '当前问题'), msg('ai', '工具调用'), msg('tool', '[E101]证据'),
               msg('ai', '当前回答')]
    current[1].usage_metadata = {'input_tokens': 20, 'output_tokens': 3}
    current[3].usage_metadata = {'input_tokens': 30, 'output_tokens': 7}
    service = ModuleType('service.agent_service')
    async def achat(*args):
        return {'reply': '当前回答', 'messages': (old if history else []) + current}
    service.AgentService = SimpleNamespace(achat=achat)
    monkeypatch.setitem(sys.modules, 'service.agent_service', service)
    clock = iter([10.0, 12.5])
    monkeypatch.setattr(evaluator.time, 'time', lambda: next(clock))
    result = asyncio.run(evaluator.ask('thread', '当前问题'))
    assert result['input_tokens'] == 50
    assert result['output_tokens'] == 10
    assert result['seconds'] == 2.5


def test_followup_aggregates_cost_but_judges_final_answer(evaluator, monkeypatch):
    threads = []
    async def fake_ask(thread, question):
        threads.append(thread)
        if question == '首问':
            return dict(reply='首问19', messages=[], seconds=2.0,
                        input_tokens=10, output_tokens=3)
        return dict(reply='末问560', messages=[], seconds=4.0,
                    input_tokens=20, output_tokens=7)
    monkeypatch.setattr(evaluator, 'ask', fake_ask)
    row = asyncio.run(evaluator.run_all([{
        'id': 'meter', 'kind': 'mixed', 'question': '首问', 'followup': '末问',
        'expect_groups': [['560']]}], False))[0]
    assert threads[0] == threads[1]
    assert row['reply'] == '末问560' and row['hit'] is True
    assert row['seconds'] == 6.0
    assert row['input_tokens'] == 30 and row['output_tokens'] == 10


@pytest.mark.parametrize('reply,hit', [
    ('119条', False), ('19.5条', False), ('0.19条', False),
    ('暂无资料[E19]', False), ('高级道19条[E201]', True),
    ('5600元', False),
])
def test_number_expectations_do_not_match_substrings(evaluator, reply, hit):
    value = 560 if '5600' in reply else 19
    assert evaluator.judge({'expect_numbers': [value]}, run(reply))['hit'] is hit
    assert evaluator.judge({'expect_groups': [[str(value)]]}, run(reply))['hit'] is hit


@pytest.mark.parametrize('reply,hit', [
    ('太舞共有22条缆车，没有详细长度', False),
    ('太舞未收录缆车表，不能列长度', True),
    ('太舞本库暂未收录缆车，不代表现实中没有缆车。[E601]', True),
    ('没有档案，但并不意味着太舞没有缆车。[E601]', True),
    ('太舞没有缆车。', False),
])
def test_forbidden_object_pattern_overrides_keyword_hit(evaluator, reply, hit):
    item = {'expect_groups': [['没有', '未收录']], 'forbid_patterns': [r'22\s*条缆车', r'(?:^|[。！？])\s*太舞(?:滑雪小镇)?(?:现实中)?没有缆车']}
    assert evaluator.judge(item, run(reply))['hit'] is hit


@pytest.mark.parametrize('strict,successful', [(False, False), (True, False), (True, True)])
def test_cli_fail_on_error_is_opt_in_and_report_is_saved(evaluator, monkeypatch, tmp_path, strict, successful):
    output = tmp_path / 'cli-result.md'
    metrics = tmp_path / 'cli-metrics.json'
    questions = [{'id': 'one', 'kind': 'run_parameter', 'question': '离线问题'}]
    rows = [dict(id='one', kind='run_parameter', question='离线问题',
                 reply='来源可核对[E101]' if successful else '安全兜底', error='',
                 answered=True, cited=successful, citation_valid=True,
                 grounded=True, unsupported=[], hit=None, stale_notice=False,
                 refused=False, degraded=not successful, price_question=False,
                 seconds=1, input_tokens=10, output_tokens=2)]
    calls = []
    def fake_load(kinds, limit, cases):
        calls.append(('load', kinds, limit, cases))
        return questions
    async def fake_run(loaded, progress):
        assert loaded is questions
        calls.append(('run', progress))
        return rows
    monkeypatch.setattr(evaluator, 'load_questions', fake_load)
    monkeypatch.setattr(evaluator, 'run_cli', fake_run)
    argv = ['eval', '--cases', 'offline.yaml', '--output', str(output),
            '--metrics-output', str(metrics), '--quiet']
    if strict:
        argv.append('--fail-on-error')
    monkeypatch.setattr(sys, 'argv', argv)
    if strict and not successful:
        with pytest.raises(SystemExit) as exc:
            evaluator.main()
        assert exc.value.code == 1
    else:
        assert evaluator.main() is None
    assert output.is_file() and metrics.is_file()
    assert '逐题验收' in output.read_text(encoding='utf-8')
    assert calls == [('load', set(), 0, 'offline.yaml'), ('run', False)]
