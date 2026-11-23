// 仅组织显示，不重写答案，不隐藏来源编号或安全提醒。
export const CHAT_STARTERS = [
  { id: 'beidahu-beginner', title: '北大湖 · 新手先滑哪条', detail: '档案里的长度、坡度和走法', question: '第一次去北大湖，新手滑哪条雪道' },
  { id: 'beidahu-run', title: '北大湖 · 岗梁道怎么样', detail: '一条中级道的参数，不靠印象', question: '北大湖岗梁道怎么样' },
  { id: 'beidahu-rime', title: '北大湖 · 哪里看雾凇', detail: '只讲已收录的观景点，并配链接', question: '北大湖哪里看雾凇' },
  { id: 'wanlong', title: '排万龙行程', detail: '演示：周末两天 · 出行程卡', question: '我想下周末去万龙，两个人，人均预算 2000，玩两天，我玩单板。请帮我生成行程方案，并标明价格来源与日期。' }
]

export function replyParagraphs(content) {
  if (typeof content !== 'string' || !content.trim()) return []
  return content.split(/\n\s*\n/).map(text => text.trim()).filter(Boolean)
}

export function retryQuestion(messages, index) {
  const reply = messages[index]
  if (!reply || reply.role !== 'agent' || (!reply.failed && !reply.stopped) || reply.streaming) return ''
  for (let i = index - 1; i >= 0; i--) {
    if (messages[i].role === 'user') return typeof messages[i].content === 'string' ? messages[i].content.trim() : ''
  }
  return ''
}
