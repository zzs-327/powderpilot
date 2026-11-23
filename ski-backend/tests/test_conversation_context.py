import pytest


from types import SimpleNamespace
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage
from agent.conversation_context import known_location
from agent.conversation_focus import ConversationFocusMiddleware
from service.guidance_service import build_guidance


pytestmark = pytest.mark.usefixtures("guidance_catalog")


def test_location_survives_followup_and_new_user_location_wins():
    messages = [HumanMessage(content='我在北京'), AIMessage(content='你从上海出发'),
                HumanMessage(content='我是新手，想滑双板')]
    assert known_location(messages) == '北京'
    guidance = build_guidance({'messages': messages})
    assert '从北京出发' in guidance['suggestions'][0]['question']
    assert '先问我出发位置' not in str(guidance)
    messages.append(HumanMessage(content='更正，这次从上海出发'))
    assert known_location(messages) == '上海'


def test_hypothetical_negative_and_reset():
    assert known_location([HumanMessage(content='如果我在北京，会怎么样')]) == ''
    assert known_location([HumanMessage(content='我不是从上海出发')]) == ''
    assert known_location([HumanMessage(content='我在北京'), HumanMessage(content='出发地还没定')]) == ''
    assert known_location([HumanMessage(content='我在北京，不是从北京出发，是从上海出发')]) == '上海'


def test_model_request_receives_known_location_without_history_mutation():
    messages = [HumanMessage(content='我在北京'), HumanMessage(content='继续')]
    class Request:
        state = {'messages': messages}
        system_message = SystemMessage(content='来源约束')
        def override(self, **kwargs):
            return SimpleNamespace(**kwargs)
    result = ConversationFocusMiddleware()._request(Request())
    assert '用户已明确提供地点：北京' in result.system_message.content
    assert '不要再次问从哪里出发' in result.system_message.content
    assert len(messages) == 2
