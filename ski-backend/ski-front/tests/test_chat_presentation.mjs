import test from 'node:test'
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
const source = await readFile(new URL('../utils/chat-presentation.js', import.meta.url), 'utf8')
const ui = await import('data:text/javascript;base64,' + Buffer.from(source).toString('base64'))
test('欢迎入口是任务而不是固定雪场演示题', () => {
  const demos = ui.CHAT_STARTERS.filter(x => x.detail.startsWith('演示'))
  assert.equal(ui.CHAT_STARTERS.length - demos.length, 3)
  assert.ok(demos.length <= 1)
  assert.equal(new Set(ui.CHAT_STARTERS.map(x => x.id)).size, ui.CHAT_STARTERS.length)
  assert.ok(ui.CHAT_STARTERS.every(x => x.title && x.detail && x.question))
})
test('回答分段保留来源、安全提醒与顺序，不推断结论', () => {
  assert.deepEqual(ui.replyParagraphs('先不要上高级道 [E12]\n\n注意：这是样例价格。\n请现场确认。'), ['先不要上高级道 [E12]', '注意：这是样例价格。\n请现场确认。'])
  assert.deepEqual(ui.replyParagraphs(null), [])
})
test('重试只使用失败或停止轮次之前的用户问题', () => {
  const messages = [{role:'user',content:'第一次滑雪'}, {role:'agent',failed:true}, {role:'user',content:'预算多少'}, {role:'agent',stopped:true}]
  assert.equal(ui.retryQuestion(messages,1),'第一次滑雪')
  assert.equal(ui.retryQuestion(messages,3),'预算多少')
  assert.equal(ui.retryQuestion([{role:'agent',failed:true}],0),'')
  assert.equal(ui.retryQuestion([{role:'user',content:'a'},{role:'agent'}],1),'')
})
