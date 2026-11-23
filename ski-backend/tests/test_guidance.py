"""情境交互与收尾协议离线测试，不依赖模型、MySQL 或 checkpoint。"""
import asyncio

import pytest

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langgraph.errors import GraphRecursionError

from agent.middleware import DEGRADE_REPLY, RETRY_MARK
from service.agent_service import AgentService
from service.guidance_service import build_guidance


pytestmark = pytest.mark.usefixtures("guidance_catalog")


def test_context_reused_without_ai_text_matching():
    state = {'messages': [HumanMessage(content='我是双板新手，从北京出发'),
                          AIMessage(content='推荐万龙'), HumanMessage(content='继续')],
             'resort': '北大湖'}
    guidance = build_guidance(state)
    assert guidance['scenes'][0]['resort_id'] == 'beidahu'
    assert len(guidance['suggestions']) == 3
    assert '双板' in guidance['suggestions'][0]['question']
    assert '新手' in guidance['suggestions'][0]['question']
    assert guidance['learning']['resources'] == []
    assert '尚无已核验' in guidance['learning']['notice']


def test_no_binding_from_ai_or_ambiguous_user():
    assert not build_guidance({'messages': [HumanMessage(content='你好'),
                                          AIMessage(content='万龙')]})['scenes']
    scenes = build_guidance({'resort': '万龙', 'messages': [
        HumanMessage(content='万龙和太舞比较一下')]})['scenes']
    assert {scene['resort_id'] for scene in scenes} == {'wanlong', 'taiwu'}


def test_current_resort_overrides_state_and_profile_fills_board():
    guidance = build_guidance({'resort': '万龙', 'messages': [HumanMessage(content='北大湖怎么样')]},
                              {'board_type': '单板', 'level': '新手'})
    assert guidance['scenes'][0]['resort_id'] == 'beidahu'
    assert '单板' in guidance['suggestions'][0]['question']


def test_location_and_missing_board_are_actionable():
    guidance = build_guidance({'messages': [HumanMessage(content='我在北京，是新手')]})
    assert len(guidance['suggestions']) == 2
    assert '北京' in guidance['suggestions'][0]['question']
    assert guidance['suggestions'][1]['id'] == 'choose-board'


class FakeAgent:
    def __init__(self, state, error=None):
        self.state = state
        self.error = error

    async def ainvoke(self, *args, **kwargs):
        if self.error:
            raise self.error
        return self.state

    async def astream(self, *args, **kwargs):
        yield 'messages', (AIMessage(content='正常回答'), {})
        if self.error:
            raise self.error
        yield 'values', self.state


def install(monkeypatch, state, error=None):
    async def get_agent(_):
        return FakeAgent(state, error)
    async def no_card(*args):
        return None
    monkeypatch.setattr(AgentService, 'aget_agent', get_agent)
    monkeypatch.setattr(AgentService, '_resolve_card', no_card)
    monkeypatch.setattr(AgentService, '_record_turn_metrics', lambda *args: None)


async def collect():
    return [event async for event in AgentService.achat_stream(None, 'test', '继续')]


def test_non_stream_and_stream_guidance_identical(monkeypatch):
    state = {'resort': '北大湖', 'messages': [HumanMessage(content='我是双板新手'),
                                           AIMessage(content='正常回答')]}
    install(monkeypatch, state)
    result = asyncio.run(AgentService.achat(None, 'test', '继续'))
    events = asyncio.run(collect())
    assert [event['guidance'] for event in events if event['type'] == 'guidance'] == [result['guidance']]
    assert events[-1]['type'] == 'guidance'


def test_degrade_has_no_card_or_guidance(monkeypatch):
    state = {'resort': '北大湖', 'messages': [HumanMessage(content='双板新手'),
                                           AIMessage(content=DEGRADE_REPLY)]}
    install(monkeypatch, state)
    async def unexpected_card(*args):
        raise AssertionError('降级不应生成卡')
    monkeypatch.setattr(AgentService, '_resolve_card', unexpected_card)
    assert asyncio.run(AgentService.achat(None, 'test', '继续'))['guidance'] is None
    assert not any(event['type'] in ('card', 'guidance') for event in asyncio.run(collect()))


def test_reset_recovered_only_emits_final_guidance(monkeypatch):
    state = {'resort': '北大湖', 'messages': [HumanMessage(content='双板新手'),
        SystemMessage(content=RETRY_MARK), AIMessage(content='重写完成')]}
    install(monkeypatch, state)
    events = asyncio.run(collect())
    assert len([event for event in events if event['type'] == 'reset']) == 1
    assert len([event for event in events if event['type'] == 'guidance']) == 1
    assert events[-1]['type'] == 'guidance'


@pytest.mark.parametrize('error', [RuntimeError('network'), asyncio.CancelledError(), GraphRecursionError('limit')])
def test_interrupted_stream_never_emits_guidance(monkeypatch, error):
    install(monkeypatch, {}, error)
    seen = []
    async def run():
        with pytest.raises(type(error)):
            async for event in AgentService.achat_stream(None, 'test', '继续'):
                seen.append(event)
    asyncio.run(run())
    assert not any(event['type'] in ('card', 'guidance') for event in seen)


def test_corrected_level_and_profile_location():
    guidance = build_guidance({'messages': [HumanMessage(content='我是新手'),
        HumanMessage(content='我不是新手，双板中级')]}, {'home_region': '上海'})
    assert '上海' in guidance['suggestions'][0]['question']
    assert '新手' not in guidance['suggestions'][0]['question']


def test_internal_message_does_not_override_degrade(monkeypatch):
    state = {'resort': '北大湖', 'messages': [HumanMessage(content='双板新手'),
        AIMessage(content=DEGRADE_REPLY), AIMessage(content='Returning structured response: invalid')]}
    install(monkeypatch, state)
    async def unexpected_card(*args):
        raise AssertionError('内部消息不能绕过降级')
    monkeypatch.setattr(AgentService, '_resolve_card', unexpected_card)
    assert not asyncio.run(AgentService.achat(None, 'test', '继续'))['guidance']
    assert not any(event['type'] in ('card', 'guidance') for event in asyncio.run(collect()))


def test_denied_resort_not_reintroduced_from_state_or_history():
    state = {'resort': '万龙', 'messages': [HumanMessage(content='想去万龙'),
        HumanMessage(content='不去万龙'), HumanMessage(content='还有什么建议')]}
    assert not build_guidance(state)['scenes']
    state['messages'].append(HumanMessage(content='改主意了，还是去万龙'))
    assert build_guidance(state)['scenes'][0]['resort_id'] == 'wanlong'


def test_learning_empty_unless_relevant_and_empty_guidance_valid():
    from schemas.guidance import Guidance
    assert Guidance.model_validate({}).learning.resources == []
    assert build_guidance({'messages': [HumanMessage(content='北京到雪场怎么走')]})['learning']['notice'] == ''
    learning = build_guidance({'messages': [HumanMessage(content='有教学视频吗')]})['learning']
    assert not learning['resources'] and '尚无已核验' in learning['notice']


def test_unknown_and_switch_request_clear_old_binding():
    for latest in ('二世谷有哪些雪道？', '从北京出发推荐其他雪场'):
        state = {'resort': '万龙', 'messages': [HumanMessage(content='去万龙'),
                                               HumanMessage(content=latest)]}
        assert not build_guidance(state)['scenes']
        state['messages'].append(HumanMessage(content='继续'))
        assert not build_guidance(state)['scenes']


def test_location_activity_suffix_and_current_location():
    for text in ('我现在在上海', '我在上海附近', '我在上海滑雪'):
        guidance = build_guidance({'messages': [HumanMessage(content='我在北京'),
                                                HumanMessage(content=text)]})
        question = guidance['suggestions'][0]['question']
        assert '从上海出发' in question
        assert '北京' not in question and '上海附近' not in question and '上海滑雪' not in question


def test_negative_board_and_instruction_level_are_not_self_description():
    guidance = build_guidance({'board': '单板', 'messages': [HumanMessage(content='我不滑单板')]})
    assert any(item['id'] == 'choose-board' for item in guidance['suggestions'])
    guidance = build_guidance({'messages': [HumanMessage(content='不要推荐高级道，我是新手')]})
    assert '新手' in guidance['suggestions'][0]['question']
    guidance = build_guidance({'messages': [HumanMessage(content='单板教学有哪些？')]})
    assert any(item['id'] == 'choose-board' for item in guidance['suggestions'])


def test_non_stream_recursion_returns_no_new_guidance(monkeypatch):
    install(monkeypatch, {}, GraphRecursionError('limit'))
    result = asyncio.run(AgentService.achat(None, 'test', '继续'))
    assert not result.get('guidance')
    assert result['structured'] is None


def test_intended_board_is_reused_without_choose_board():
    guidance = build_guidance({'messages': [HumanMessage(content='我是第一次滑雪'),
                                          HumanMessage(content='我从北京出发，想滑双板，还没选雪场')]})
    assert not any(item['id'] == 'choose-board' for item in guidance['suggestions'])
    assert '双板' in guidance['suggestions'][0]['question']


def test_latest_intended_board_overrides_previous_board():
    guidance = build_guidance({'board': '双板', 'messages': [
        HumanMessage(content='我想滑双板'), HumanMessage(content='我准备学单板')]})
    assert '单板' in guidance['suggestions'][0]['question']


@pytest.mark.parametrize('latest', [
    '我是第一次滑雪的新手，先了解需要准备什么',
    '先不选雪场，想了解注意事项',
    '暂时不去滑雪，先学安全知识',
    '先别推荐雪场，暂停行程安排',
])
def test_preparation_and_pause_do_not_promote_trip(latest):
    guidance = build_guidance({'resort': '万龙', 'messages': [
        HumanMessage(content='我在北京，想滑双板，去万龙'),
        HumanMessage(content=latest)]})
    expected_scenes = [] if '选雪场' in latest or '推荐雪场' in latest else ['wanlong']
    assert [scene['resort_id'] for scene in guidance['scenes']] == expected_scenes
    assert 2 <= len(guidance['suggestions']) <= 3
    assert guidance['suggestions'][0]['id'] in ('beginner-preparation', 'learning-safety')
    assert all(item['id'] not in ('choose-resort', 'resort-runs', 'resort-cost')
               for item in guidance['suggestions'])
    assert all(item['label'] and item['question'] for item in guidance['suggestions'])


def test_learning_intent_comes_before_existing_resort():
    guidance = build_guidance({'resort': '北大湖', 'messages': [
        HumanMessage(content='我是双板新手'), HumanMessage(content='有教学视频吗')]})
    assert guidance['scenes'][0]['resort_id'] == 'beidahu'
    assert guidance['suggestions'][0]['id'] == 'learning-safety'
    assert not guidance['learning']['resources']


@pytest.mark.parametrize(('latest', 'first_id', 'excluded_id'), [
    ('万龙雪票多少钱？', 'cost-inclusions', 'resort-cost'),
    ('万龙的海拔是多少？', 'fact-conditions', 'choose-resort'),
    ('万龙有哪些雪道？', 'runs-safety', 'resort-runs'),
    ('万龙怎么走？', 'arrival-preparation', 'cost-transport'),
])
def test_answered_topic_has_related_followups(latest, first_id, excluded_id):
    guidance = build_guidance({'messages': [HumanMessage(content=latest)]})
    assert guidance['suggestions'][0]['id'] == first_id
    assert all(item['id'] != excluded_id for item in guidance['suggestions'])
    assert len({item['question'] for item in guidance['suggestions']}) == 3


def test_contextual_cost_keeps_location_board_corrections_and_scenes():
    guidance = build_guidance({'board': '单板', 'resort': '北大湖', 'messages': [
        HumanMessage(content='我在北京，我想滑单板'),
        HumanMessage(content='我现在在上海，改滑双板，万龙和太舞雪票多少钱？')]})
    assert {scene['resort_id'] for scene in guidance['scenes']} == {'wanlong', 'taiwu'}
    assert '从上海出发' in guidance['suggestions'][1]['question']
    assert '北京' not in guidance['suggestions'][1]['question']
    assert '万龙' in guidance['suggestions'][0]['question']
    assert '太舞' in guidance['suggestions'][0]['question']
    assert all(item['id'] != 'choose-board' for item in guidance['suggestions'])


def test_continue_preserves_preparation_but_new_question_switches_intent():
    state = {'messages': [HumanMessage(content='我是新手，先不选雪场，准备什么？'),
                          HumanMessage(content='继续')]}
    assert build_guidance(state)['suggestions'][0]['id'] == 'beginner-preparation'
    state['messages'].append(HumanMessage(content='万龙雪票多少钱？'))
    assert build_guidance(state)['suggestions'][0]['id'] == 'cost-inclusions'


@pytest.mark.local_data('resorts/beidahu.yaml', 'graph/graph.json')
def test_beidahu_next_question_follows_the_topic():
    def ask(text):
        guidance = build_guidance({'messages': [HumanMessage(content=text)]})
        questions = ' '.join(item['question'] for item in guidance['suggestions'])
        labels = [item['label'] for item in guidance['suggestions']]
        return guidance, questions, labels

    guidance, questions, labels = ask('北大湖哪里看雾凇')
    assert guidance['suggestions'][0]['id'] != 'fact-conditions'
    assert '雾凇' not in questions
    assert '北大湖' in questions
    assert '夜场' in questions and '雪道' in questions
    assert all(item['id'] != 'choose-board' for item in guidance['suggestions'])

    guidance, questions, labels = ask('第一次去北大湖，新手滑哪条雪道')
    assert guidance['suggestions'][0]['id'] == 'next-rime'
    assert labels[0] == '哪里看雾凇'
    assert '夜场' in questions and '雪票' in questions
    assert '单双板' not in questions

    guidance, questions, labels = ask('北大湖能用Ikon Pass吗')
    assert 'Ikon' not in questions and '通行证' not in questions
    assert '雪票' in questions and '雪道' in questions

    guidance, questions, labels = ask('北大湖岗梁道怎么样')
    assert '岗梁' not in questions
    assert '雾凇' in questions and '夜场' in questions

    guidance, questions, _ = ask('北大湖有什么推荐的滑雪视频')
    assert guidance['suggestions'][0]['id'] != 'learning-safety'
    assert '视频' not in ' '.join(item['question'] for item in guidance['suggestions'])
    assert guidance['learning']['notice'] == ''

    guidance, _, _ = ask('北大湖雪票多少钱')
    assert guidance['suggestions'][0]['id'] == 'cost-inclusions'
    assert any('Ikon' in item['question'] for item in guidance['suggestions'][1:])
    assert all(item['id'] != 'choose-board' for item in guidance['suggestions'])


@pytest.mark.local_data('resorts/beidahu.yaml', 'graph/graph.json')
def test_run_brief_is_archive_not_model_prose():
    def brief(text):
        return build_guidance({'messages': [HumanMessage(content=text)]}).get('run_brief')

    assert brief('北大湖哪里看雾凇') is None
    assert brief('北大湖雪票多少钱') is None
    beginner = brief('第一次去北大湖，新手滑哪条雪道')
    assert beginner['title'] == '新手先看这几条'
    assert beginner['runs'][0]['name'] == '星光大道'
    assert beginner['runs'][0]['difficulty'] == '初级道'
    assert beginner['runs'][0]['length_m'] == 2279
    assert [item['name'] for item in beginner['runs']] == ['星光大道', 'H16慕雪道', 'D15逍遥大道']
    gangliang = brief('北大湖岗梁道怎么样')
    assert gangliang['runs'][0]['name'] == 'H6岗梁道'
    assert gangliang['runs'][0]['difficulty'] == '中级道'
    assert gangliang['runs'][0]['length_m'] == 2269
    assert gangliang['runs'][0]['slope_deg'] == 13.8
    assert '74' in gangliang['caveat'] and '33' in gangliang['caveat']
    assert '软粉雪' in gangliang['runs'][0]['note']
    night = brief('北大湖夜场能滑哪些雪道')
    assert night['title'] == '攻略里的夜场雪道'
    assert night['runs'][0]['name'] == 'D9'
    assert len(night['runs']) == 6
    counts = brief('北大湖有几条雪道')
    assert counts['title'] == '雪道条数有两个口径'
    assert counts['runs'] == []


def test_present_reply_adds_the_missing_total_and_unwatched_limit():
    from service.run_brief import present_reply
    evidence = '档案来源口径总数 74 条雪道；分级统计合计 33 条。未观看内容，不能写成已验证的推荐。'
    corrected = present_reply('岗梁道长 2269 米，总共有 74 条雪道，其中包括初级道。', evidence)
    assert '2269' in corrected and '总共有 74' not in corrected
    assert '33' in corrected and '两个口径' in corrected
    assert present_reply('一个来源称 74 条，分级合计 33 条。', evidence) == '一个来源称 74 条，分级合计 33 条。'
    assert '未观看' in present_reply('以下是滑雪视频推荐。', evidence)
    assert present_reply('星光大道适合新手。[E101]', '长度约 2279 米') == '星光大道适合新手。[E101]'


@pytest.mark.local_data('resorts/beidahu.yaml', 'graph/graph.json')
def test_refusal_does_not_precede_archive_runs():
    from service.run_brief import present_reply
    apology = ('很抱歉，目前没有找到关于北大湖滑雪场新手适合先滑哪条雪道的具体信息。'
               '建议您查看官方渠道或等待实拍更新，以获取更多相关信息。')
    text = present_reply(apology, '', '第一次去北大湖，新手滑哪条雪道')
    assert '没有找到' not in text
    assert text.startswith('可以先看星光大道')
    assert '2279' in text and 'H16慕雪道' in text
    price = '目前无法提供北大湖雪票的具体价格，本库没有收录。'
    assert present_reply(price, '', '北大湖雪票多少钱') == price
    kept = '星光大道适合新手，长 2279 米。[E101]'
    assert present_reply(kept, '', '第一次去北大湖，新手滑哪条雪道') == kept
    # 这一句没写雪场，雪场名在道歉正文或上一轮里
    short = present_reply(apology, '', '新手先滑哪条')
    assert short.startswith('可以先看星光大道')
    followed = present_reply('没有找到具体信息。', '', '新手先滑哪条', ['我想去北大湖'])
    assert followed.startswith('可以先看星光大道')
    night = present_reply('没有找到夜场雪道。', '', '北大湖夜场能滑哪些雪道')
    assert night.startswith('攻略里写的夜场雪道有D9') and '可以先看' not in night
    gangliang = present_reply('没有找到岗梁道的资料。', '', '北大湖岗梁道怎么样')
    assert 'H6岗梁道' in gangliang and '2269' in gangliang and '没有找到' not in gangliang
    rime = present_reply('没有找到雾凇的具体信息。', '', '北大湖哪里看雾凇')
    assert rime.startswith('下面是和这个问题相关的已收录链接') and '没有找到' not in rime
    video = present_reply('没有找到推荐视频。', '', '北大湖有什么滑雪视频')
    assert video.startswith('下面是和这个问题相关的已收录链接') and '没有找到' not in video
    ikon = present_reply('没有找到通行证资料。', '', '北大湖能用Ikon Pass吗')
    assert '不可用日期' in ikon and '没有找到' not in ikon
    counts = present_reply('没有找到雪道总数。', '', '北大湖有几条雪道')
    assert '74' in counts and '33' in counts and '没有找到' not in counts
