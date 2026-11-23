"""提示词契约守卫，不声称静态测试能证明真实模型服从。"""
import pytest

from agent.prompts import PromptBuilder


def test_chat_style_keeps_provenance_last():
    prompt = PromptBuilder.build({'level': '新手', 'board_type': '双板'})
    assert '第一段直接回答用户当前问题' in prompt
    assert '资料不足时第一段明确缺口' in prompt
    assert '本轮用户消息、历史用户消息或已记录行程状态' in prompt
    assert '用户最新明确更正优先于旧画像' in prompt
    assert '不强行追问' in prompt
    assert '两个口径' in prompt
    assert '对新手的建议' in prompt
    assert '不能擅自补齐、生成完整行程' in prompt
    assert '不编造视频或教练' in prompt
    assert '- 水平：新手' in prompt
    assert '- 单双板：双板' in prompt
    assert prompt.endswith(PromptBuilder.OUTPUT_FORMAT)
    assert '工具返回里没出现过的编号一律不许写' in prompt


def test_unset_profile_does_not_invent_conditions():
    prompt = PromptBuilder.build()
    assert '未采集' in prompt
    assert '不要把你自己猜测的条件当作用户确认' in prompt
    assert '示例数据' in prompt
    assert '价格类信息必须带上录入日期' in prompt
    assert '不计入预算小计' in prompt
    assert '不同币种不能换算后相加' in prompt


@pytest.mark.local_data('resorts/beidahu.yaml')
def test_recording_conditions_does_not_start_trip_collection():
    prompt = PromptBuilder.build()
    assert '记录已有条件不等于用户请求规划' in prompt
    assert '先不安排出行时' in prompt
    from agent.tools import set_trip_info
    assert '不要追问缺失行程项' in set_trip_info.description
    from agent.tools import level_notes
    beginner = next(text for text, _date in level_notes('beidahu') if '对新手的建议' in text)
    assert '星光大道' in beginner
    assert '不要改推' in beginner
