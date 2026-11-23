"""复用用户明确提供的地点，不从助手回答推断。"""
import re

_LOCATION = re.compile(r'(?:我(?:现在)?在|我住在|从)([^，。！？；\s]{2,16}?)(?:附近|滑雪|出发|去|想|[，。！？；]|$)')
_RESET = re.compile(r'(?:出发(?:地|位置)|地点).{0,4}(?:还没定|未定|不确定)|不知道从哪.{0,3}出发')


def known_location(messages, fallback: str = '') -> str:
    location = fallback
    for message in messages:
        if getattr(message, 'type', '') != 'human':
            continue
        text = str(message.content)
        if _RESET.search(text):
            location = ''
        for match in _LOCATION.finditer(text):
            prefix = text[max(0, match.start() - 5):match.start()]
            if re.search(r'不是|不|如果|假如|比如|例如', prefix):
                continue
            value = match.group(1)
            if not re.search(r'不是|不在|哪里|哪儿|什么|还没|不确定', value):
                location = value
    return location
