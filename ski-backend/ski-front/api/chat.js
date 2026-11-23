import { BASE_URL, getToken, request } from './request'

// 一次性返回：流式用不了时的兜底（老浏览器 / 小程序端拿不到分块 / 流式通道本身不通）
export function chat(message, threadId) {
  return request({
    url: '/api/v1/chat',
    method: 'POST',
    data: { message, thread_id: threadId || null }
  })
}

const STREAM_PATH = '/api/v1/chat/stream'
const CHAT_PATH = '/api/v1/chat'

// 服务端静默上限：一轮对话里最长的静默是"兜底生成行程卡"那 10~30 秒，
// 留到 3 分钟足够宽松。超过就判定连接卡死——否则用户会对着一个一直闪的光标干等。
const IDLE_TIMEOUT = 180000

/**
 * SSE 事件流客户端（打字机效果就是它）
 *
 * 为什么不用 uni.request：
 *   uni.request 会把整个响应体读完才回调，token 是"攒够了再一次性给你"，
 *   拿不到增量 —— 这就是原来只能等 30~90 秒看整段答案的原因。
 *   H5 端因此改用 fetch + ReadableStream 逐块读；小程序端没有 fetch，
 *   改用 uni.request 的 enableChunked（见 streamByRequest）。
 *
 * 事件协议由后端 service/agent_service.py 的 achat_stream 定义：
 *   meta（会话 id）/ status（进度）/ text（正文增量）/ reset（校验打回，清空重来）
 *   / trip / card / evidences / error，最后一定有一条 done（本地补）
 *
 * @param {string} message 用户这一轮说的话
 * @param {string} threadId 会话 id，空则由后端生成（第一条 meta 事件会带回来）
 * @param {(event: object) => void} onEvent 每收到一个事件回调一次
 * @returns {{abort: () => void}} 用户点"停止"时调 abort()
 */
export function chatStream(message, threadId, onEvent) {
  const body = { message, thread_id: threadId || null }
  // 这条流的公共状态：aborted 区分"用户主动停"和"真出错"，
  // got 记录有没有收到过任何事件（决定出错时要不要退回一次性接口），
  // done 保证收尾事件只发一次（服务端发了 [DONE]、本地收尾还会再补一次）
  const ctx = { aborted: false, timedOut: false, got: false, done: false, abort: null }

  const emit = (event) => {
    if (event.type === 'done') {
      if (ctx.done) return
      ctx.done = true
    } else {
      ctx.got = true
    }
    onEvent(event)
  }

  const run = async () => {
    try {
      // #ifdef H5
      await streamByFetch(body, emit, ctx)
      // #endif
      // #ifndef H5
      await streamByRequest(body, emit, ctx)
      // #endif
      emit({ type: 'done' })
    } catch (err) {
      if (ctx.aborted) {
        emit({ type: 'done' })       // 用户自己停的，不算错误
        return
      }
      // 一个字都没收到 -> 多半是"流式这条通道不通"（浏览器太老、代理缓冲、跨域），
      // 退回一次性接口，用户至少能拿到答案；已经收到过半截就只能如实报错，
      // 因为再补一份完整答案会和半截内容叠在一起。
      if (!ctx.got) {
        try {
          await streamByFallback(body, emit)
          emit({ type: 'done' })
          return
        } catch (err2) {
          emit({ type: 'error', message: describe(err2, ctx) })
          emit({ type: 'done' })
          return
        }
      }
      emit({ type: 'error', message: describe(err, ctx) })
      emit({ type: 'done' })
    }
  }

  run()

  return {
    abort() {
      ctx.aborted = true
      if (ctx.abort) ctx.abort()
    }
  }
}

/** 把异常翻译成人话（直接甩 err.message 给用户，多半是英文技术名词） */
function describe(err, ctx) {
  if (ctx && ctx.timedOut) {
    return `服务端超过 ${Math.round(IDLE_TIMEOUT / 60000)} 分钟没有响应，已断开`
  }
  const text = String((err && (err.errMsg || err.message)) || err || '')
  if (/abort/i.test(text)) return '连接已断开'
  if (/401|403/.test(text)) return '登录状态已失效，请重新登录'
  if (/timeout/i.test(text)) return '请求超时'
  return text || '网络错误'
}

// ---------------- SSE 解析（各端共用） ----------------

/**
 * 从缓冲区里切出完整的 SSE 消息并分发，返回没消费完的尾巴
 *
 * ⚠️ 必须自己维护缓冲区：网络分块与消息边界**没有任何关系**，
 *    一条 data: 消息可能被切成两半到达。少维护这个缓冲区，
 *    表现就是"偶尔丢一句、偶尔报 JSON 解析失败"。
 */
function feed(buffer, onEvent) {
  let rest = String(buffer || '').replace(/\r\n/g, '\n')
  let index
  while ((index = rest.indexOf('\n\n')) >= 0) {
    dispatch(rest.slice(0, index), onEvent)
    rest = rest.slice(index + 2)
  }
  return rest
}

/** 解析一条完整的 SSE 消息并回调（多条 data: 行按规范用换行拼接） */
function dispatch(block, onEvent) {
  const data = []
  block.split('\n').forEach((line) => {
    if (line.charAt(0) === ':') return                       // 注释行（心跳 ping）
    if (line.indexOf('data:') === 0) data.push(line.slice(5).replace(/^ /, ''))
  })
  if (!data.length) return
  const payload = data.join('\n')
  if (payload === '[DONE]') {
    onEvent({ type: 'done' })
    return
  }
  try {
    onEvent(JSON.parse(payload))
  } catch (e) {
    // 单个畸形事件不该把整轮对话打断：丢掉它，后面的照常处理
  }
}

// ---------------- H5：fetch + ReadableStream ----------------

// #ifdef H5
async function streamByFetch(body, onEvent, ctx) {
  // AbortController 让"停止"能真的掐断连接（fetch 没有别的取消手段）
  const controller = typeof AbortController !== 'undefined' ? new AbortController() : null
  ctx.abort = () => { if (controller) controller.abort() }

  let idle = null
  const kick = () => {
    if (!controller) return
    if (idle) clearTimeout(idle)
    idle = setTimeout(() => {
      ctx.timedOut = true
      controller.abort()
    }, IDLE_TIMEOUT)
  }

  kick()
  try {
    const response = await fetch(BASE_URL + STREAM_PATH, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': 'Bearer ' + getToken()
      },
      body: JSON.stringify(body),
      signal: controller ? controller.signal : undefined
    })
    if (!response.ok) throw new Error('HTTP ' + response.status)

    // 极老的浏览器没有 body.getReader()：拿整段文本解析。
    // 事件协议完全一样，只是退化成"一次性"——有兜底总比白屏好。
    if (!response.body || !response.body.getReader) {
      const text = await response.text()
      feed(text + '\n\n', onEvent)
      return
    }

    const reader = response.body.getReader()
    // stream: true —— 让解码器自己留住被切断的多字节字符（中文必备）
    const decoder = new TextDecoder('utf-8')
    let buffer = ''
    for (;;) {
      const { value, done } = await reader.read()
      if (done) break
      kick()
      buffer = feed(buffer + decoder.decode(value, { stream: true }), onEvent)
    }
    buffer = feed(buffer + decoder.decode(), onEvent)
    if (buffer.trim()) dispatch(buffer, onEvent)     // 服务端没补结尾空行时也不丢内容
  } finally {
    if (idle) clearTimeout(idle)
  }
}
// #endif

// ---------------- 非 H5：uni.request 分块（小程序端） ----------------

/**
 * 小程序端没有 fetch，但 uni.request 支持 enableChunked + onChunkReceived
 *
 * 两条保险：
 *   1. onChunkReceived 不存在（该端不支持）时，success 回调里拿整段数据照样能解析；
 *   2. 这一路整体走不通时，上层会自动退回 POST /api/v1/chat（一次性返回）。
 * ⚠️ 演示走 H5，这一路没有实机验证过；改小程序端时请先用真机跑一遍。
 */
// #ifndef H5
function streamByRequest(body, onEvent, ctx) {
  return new Promise((resolve, reject) => {
    const decoder = createUtf8Decoder()
    let buffer = ''
    let sawChunk = false

    const task = uni.request({
      url: BASE_URL + STREAM_PATH,
      method: 'POST',
      header: {
        'Content-Type': 'application/json',
        'Authorization': 'Bearer ' + getToken()
      },
      data: body,
      enableChunked: true,
      responseType: 'arraybuffer',
      timeout: IDLE_TIMEOUT,
      success(res) {
        if (!sawChunk) {
          // 一个分块都没收到：说明这个端不支持，拿整段 body 解析
          buffer = feed(buffer + decoder.decode(res.data, true), onEvent)
          if (res.statusCode && res.statusCode !== 200) {
            reject(new Error('HTTP ' + res.statusCode))
            return
          }
        }
        if (buffer.trim()) dispatch(buffer, onEvent)
        resolve()
      },
      fail(err) {
        reject(err)
      }
    })

    ctx.abort = () => { if (task && task.abort) task.abort() }
    if (task && typeof task.onChunkReceived === 'function') {
      task.onChunkReceived((res) => {
        sawChunk = true
        buffer = feed(buffer + decoder.decode(res.data, false), onEvent)
      })
    }
  })
}

/**
 * 手写一个"能跨块"的 UTF-8 解码器
 *
 * 为什么不用 TextDecoder：小程序运行时里没有它。
 * 为什么不能直接把字节拼成字符串：一个中文字是 3 个字节，而分块边界
 * 极可能正好切在中间，硬拼会得到乱码（而且只在某些分块下出现，极难复现）。
 * 所以：凑不齐一个完整字符的字节先留在 tail 里，等下一块。
 */
function createUtf8Decoder() {
  let tail = []
  const toBytes = (input) => {
    const out = []
    if (!input) return out
    if (typeof input === 'string') {
      for (let i = 0; i < input.length; i++) out.push(input.charCodeAt(i) & 0xFF)
      return out
    }
    const view = input instanceof Uint8Array ? input : new Uint8Array(input)
    for (let i = 0; i < view.length; i++) out.push(view[i])
    return out
  }

  return {
    /** @param {ArrayBuffer|Uint8Array|string} input @param {boolean} flush 是否把残留字节也吐出来 */
    decode(input, flush) {
      const bytes = tail.concat(toBytes(input))
      const chars = []
      let i = 0
      while (i < bytes.length) {
        const head = bytes[i]
        let len = 1
        if (head >= 0xF0) len = 4
        else if (head >= 0xE0) len = 3
        else if (head >= 0xC0) len = 2
        if (i + len > bytes.length) {
          if (!flush) break                 // 半个字符：留给下一块
          len = 1
        }
        let code = len === 1 ? head : head & (0xFF >> (len + 1))
        for (let k = 1; k < len; k++) code = (code << 6) | (bytes[i + k] & 0x3F)
        chars.push(String.fromCodePoint(code))
        i += len
      }
      tail = flush ? [] : bytes.slice(i)
      return chars.join('')
    }
  }
}
// #endif

// ---------------- 兜底：一次性接口 ----------------

/** 把一次性接口的返回"翻译"成同样的事件序列，让页面只认一套协议 */
async function streamByFallback(body, onEvent) {
  const res = await request({
    url: CHAT_PATH,
    method: 'POST',
    data: { message: body.message, thread_id: body.thread_id }
  })
  onEvent({ type: 'meta', thread_id: res.thread_id })
  if (res.reply) onEvent({ type: 'text', text: res.reply })
  if (res.trip) onEvent({ type: 'trip', trip: res.trip })
  if (res.structured) {
    onEvent({ type: 'text', text: `已生成行程卡：${res.structured.summary || ''}` })
    onEvent({ type: 'card', card: res.structured })
  }
  onEvent({ type: 'evidences', evidences: res.evidences || [] })
  if (res.guidance) onEvent({ type: 'guidance', guidance: res.guidance })
}
