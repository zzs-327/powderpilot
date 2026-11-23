/**
 * 前端流式链路离线断言（不联网、不调模型、不需要后端）
 *
 * 为什么要有它：
 *   SSE 解析最怕的不是"完全不工作"，而是**偶尔丢一句**——
 *   网络分块边界与消息边界毫无关系，一个中文被切成两半这种问题
 *   在演示时可能十次里只出现一次，事后完全没法复现。
 *   所以把"分块边界"写死成测试用例（每 7 字节切一刀），每次改完跑一遍。
 *
 * 跑法（在 ski-front 目录下）：
 *     node tests/test_stream.mjs
 *
 * ⚠️ 这里只能测纯 JS（api/chat.js、utils/text.js）。
 *    页面本身（pages/index/index.vue）必须真在浏览器里打开看一眼——
 *    编译通过 ≠ 页面能显示，这条是 2026-09-12 白屏事故换来的（docs/TROUBLESHOOTING.md #18）。
 */
import { mkdtempSync, copyFileSync, readFileSync, writeFileSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, dirname } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'

// uni-app 的源码是"无扩展名 import"（'./request'），Node 的 ESM 解析器不认，
// 所以复制到临时目录、补上扩展名再动态 import —— 测的还是同一份源码。
const here = dirname(fileURLToPath(import.meta.url))
const front = join(here, '..')
const dir = mkdtempSync(join(tmpdir(), 'ski-stream-'))
copyFileSync(join(front, 'utils', 'text.js'), join(dir, 'text.mjs'))
copyFileSync(join(front, 'api', 'request.js'), join(dir, 'request.mjs'))
writeFileSync(
  join(dir, 'chat.mjs'),
  readFileSync(join(front, 'api', 'chat.js'), 'utf8').replace("'./request'", "'./request.mjs'")
)

const { chatStream } = await import(pathToFileURL(join(dir, 'chat.mjs')).href)
const { prettify } = await import(pathToFileURL(join(dir, 'text.mjs')).href)
rmSync(dir, { recursive: true, force: true })

let pass = 0
let fail = 0
const ok = (cond, name) => {
  if (cond) {
    pass++
    console.log('  \u2713', name)
  } else {
    fail++
    console.log('  \u2717', name)
  }
}

globalThis.uni = { getStorageSync: () => 'token-abc' }

// ---------------- 1. SSE 解析：把中文切在字节中间 ----------------
console.log('\n[1] SSE 分块解析（每 7 字节切一刀，模拟真实网络分块）')
const enc = new TextEncoder()
const raw = [
  'data: {"type":"meta","thread_id":"t-1"}\n\n',
  ': ping\n\n',
  'data: {"type":"status","text":"正在查价格…"}\n\n',
  'data: {"type":"text","text":"万龙雪票平日 560 元"}\n\n',
  'data: {"type":"text","text":"，周末 780 元 [E301]。"}\n\n',
  'data: {"type":"evidences","evidences":[{"id":301,"status_key":"transcribed"}]}\n\n',
  'data: {"type":"guidance","guidance":{"suggestions":[{"id":"prepare","label":"出发准备","question":"第一次滑雪需要准备什么？"}],"scenes":[{"resort_id":"wanlong","name":"万龙"}]}}\n\n',
  'data: [DONE]\n\n'
].join('')
const all = enc.encode(raw)
const chunks = []
for (let i = 0; i < all.length; i += 7) chunks.push(all.slice(i, i + 7))

let fetchUrl = ''
let fetchBody = null
globalThis.fetch = async (url, opts) => {
  fetchUrl = url
  fetchBody = JSON.parse(opts.body)
  let i = 0
  return {
    ok: true,
    body: {
      getReader: () => ({
        read: async () => (i < chunks.length
          ? { value: chunks[i++], done: false }
          : { value: undefined, done: true })
      })
    }
  }
}

const events = []
await new Promise((resolve) => {
  chatStream('万龙雪票多少钱', '', (e) => {
    events.push(e)
    if (e.type === 'done') resolve()
  })
  setTimeout(resolve, 3000)
})

const types = events.map((e) => e.type)
ok(fetchUrl.endsWith('/api/v1/chat/stream'), 'POST 打到 /chat/stream')
ok(fetchBody.message === '万龙雪票多少钱' && fetchBody.thread_id === null, '请求体带 message / thread_id')
ok(types[0] === 'meta' && events[0].thread_id === 't-1', 'meta 事件带回会话 id')
ok(events.find((e) => e.type === 'status')?.text === '正在查价格…', 'status 进度事件')
ok(
  events.filter((e) => e.type === 'text').map((e) => e.text).join('')
    === '万龙雪票平日 560 元，周末 780 元 [E301]。',
  '中文被切碎后仍能完整还原（UTF-8 跨块解码）'
)
ok(events.find((e) => e.type === 'evidences')?.evidences[0].id === 301, 'evidences 事件')
ok(types[types.length - 1] === 'done' && types.filter((t) => t === 'done').length === 1, 'done 只发一次且收尾')
ok(events.find((e) => e.type === 'guidance')?.guidance.suggestions[0].question === '第一次滑雪需要准备什么？', 'guidance 中文跨块完整还原')
ok(types.indexOf('guidance') < types.indexOf('done'), 'guidance 在 done 前到达')
ok(!types.includes('error'), '没有 error 事件')

// ---------------- 2. 流式 markdown 清洗 ----------------
console.log('\n[2] 流式 markdown 清洗（半截内容不该闪出来）')
const partial = '| 票种 | 价格 |\n|---|---|\n| 平日全天 | 560 元/人 |\n| 周末全天 | 780'
const final = '| 票种 | 价格 |\n|---|---|\n| 平日全天 | 560 元/人 |\n| 周末全天 | 780 元/人 |'
const partialText = prettify(partial, { streaming: true })
ok(partialText.indexOf('周末') < 0, '流式中：半截表格行先不显示（避免文字跳变）')
ok(partialText.indexOf('平日全天：560 元/人') >= 0, '流式中：前面完整的行照常显示')
ok(prettify(final, { streaming: false }).indexOf('周末全天：780 元/人') >= 0, '收尾后：完整表格行正常显示')
ok(prettify('价格是 **560 元', { streaming: true }) === '价格是 560 元', '流式中：去掉未闭合的加粗标记')
ok(prettify('见 [E30', { streaming: true }) === '见', '流式中：去掉写了一半的引用编号')
ok(prettify('见 [E301]。', { streaming: false }) === '见 [E301]。', '收尾后：完整引用编号保留')

// ---------------- 3. 兜底：流式通道不通时退回一次性接口 ----------------
console.log('\n[3] 流式通道不通时退回一次性接口（不让用户白屏）')
globalThis.fetch = async () => {
  throw new Error('CORS blocked')
}
const fallbackEvents = []
await new Promise((resolve) => {
  // request.js 走 uni.request，这里给个最小桩：直接返回一次性接口的返回体
  globalThis.uni.request = (cfg) => {
    cfg.success({
      statusCode: 200,
      data: {
        code: 200,
        data: {
          reply: '万龙平日 560 元 [E301]',
          thread_id: 't-2',
          structured: null,
          trip: { resort: '万龙', ready: false },
          evidences: [{ id: 301 }],
          guidance: { suggestions: [{ id: 'prepare', label: '出发准备', question: '第一次滑雪需要准备什么？' }], scenes: [{ resort_id: 'wanlong', name: '万龙' }] }
        }
      }
    })
  }
  chatStream('万龙雪票多少钱', '', (e) => {
    fallbackEvents.push(e)
    if (e.type === 'done') resolve()
  })
  setTimeout(resolve, 3000)
})
const fbTypes = fallbackEvents.map((e) => e.type)
ok(fbTypes.includes('text') && fbTypes.includes('evidences'), '退回一次性接口后仍产出同样的事件序列')
ok(fallbackEvents.find((e) => e.type === 'meta')?.thread_id === 't-2', '兜底路径也带回会话 id')
ok(fallbackEvents.find((e) => e.type === 'guidance')?.guidance.scenes[0].resort_id === 'wanlong', '非流式兜底透传 guidance')
ok(fbTypes.indexOf('guidance') < fbTypes.indexOf('done'), '兜底引导先于 done')
ok(!fbTypes.includes('error'), '兜底成功时不该报错')

// 两条通道都失败时也必须结束等待，允许用户再次发送。
const errorEvents = []
globalThis.uni.request = cfg => { cfg.fail({ errMsg: 'network unavailable' }) }
await new Promise(resolve => {
  chatStream('失败重试测试', 't-err', event => {
    errorEvents.push(event)
    if (event.type === 'done') resolve()
  })
  setTimeout(resolve, 1000)
})
ok(errorEvents.some(event => event.type === 'error'), '双通道失败有明确错误')
ok(errorEvents.filter(event => event.type === 'done').length === 1, '双通道失败也只结束一次，不锁住输入框')

console.log(`\n结果：${pass} 项通过 / ${fail} 项失败`)
process.exit(fail ? 1 : 0)
