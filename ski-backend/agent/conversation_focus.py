"""只识别明确的入门/暂停意图，不把模糊问题强行分类。"""
import re
from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import SystemMessage

from agent.conversation_context import known_location


def focus_hint(messages) -> str:
    humans = [str(m.content) for m in messages if getattr(m, 'type', '') == 'human']
    for text in reversed(humans):
        if re.search(r'先不安排|暂不(?:安排|排行程)|只确认.*条件|不(?:安排|规划).*行程', text):
            return '当前任务是确认条件或暂停规划：回应已明确的条件，不询问日期、天数、人数、预算，不推荐雪场。不把记录条件当作启动行程。'
        if re.search(r'帮我.*(?:安排|规划)|安排一天|生成.*行程|排.*行程|多少钱|票价|雪道|天气|怎么去|来源|推荐.*雪场', text):
            return ''
        if re.search(r'先了解.*(?:准备|入门)|第一次滑雪.*(?:准备|先问)|先了解入门', text):
            return '当前任务是了解入门准备，不是排行程。这轮只做任务澄清：只询问尚未说明的单双板意向；已知道单双板时只回应该条件并询问想先了解课程还是装备，不主动列装备清单、教学步骤或推荐雪场。不要询问日期、天数、人数或预算。具体教学或装备结论仍必须取证，缺资料如实说明。'
    return ''


class ConversationFocusMiddleware(AgentMiddleware):
    def _request(self, request):
        messages = request.state.get('messages', [])
        hint = focus_hint(messages)
        location = known_location(messages)
        if location:
            hint += '\n用户已明确提供地点：' + location + '。本轮沿用此地点作为推荐参考，不要再次问从哪里出发；若当前所在地和本次实际出发地确有冲突，只确认该冲突。用户的新更正优先。地点是用户上下文，不是雪场事实证据。'
        if not hint:
            return request
        original = request.system_message.content if request.system_message else ''
        return request.override(system_message=SystemMessage(content=str(original) + '\n## 当前对话任务\n' + hint))

    def wrap_model_call(self, request, handler):
        return handler(self._request(request))

    async def awrap_model_call(self, request, handler):
        return await handler(self._request(request))
