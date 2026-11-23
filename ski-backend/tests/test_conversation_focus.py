from langchain_core.messages import HumanMessage, AIMessage
from agent.conversation_focus import focus_hint


def test_intro_persists_for_condition_followup_and_explicit_plan_resets():
    messages = [HumanMessage(content='第一次滑雪，先了解怎么准备'),
                HumanMessage(content='我从北京出发，想滑双板')]
    assert '不要询问日期' in focus_hint(messages)
    assert focus_hint(messages + [HumanMessage(content='帮我安排一天滑雪')]) == ''


def test_pause_and_ai_text_do_not_create_planning_intent():
    assert '暂停规划' in focus_hint([HumanMessage(content='先不安排出行，只确认我的条件')])
    assert focus_hint([HumanMessage(content='雪票多少钱'), AIMessage(content='第一次滑雪准备')]) == ''


def test_fact_question_leaves_intro_focus():
    assert focus_hint([HumanMessage(content='先了解入门'), HumanMessage(content='万龙票价多少钱')]) == ''
