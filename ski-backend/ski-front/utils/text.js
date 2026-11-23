/**
 * 文本格式化：把模型返回的 markdown 转成适合手机气泡里读的纯文本
 *
 * 为什么需要这一层：
 *   对话页用 <text> 原样渲染回复，而模型的输出是 markdown。
 *   实测一条"万龙雪票多少钱"的回答会带 markdown 表格，用户看到的是：
 *
 *       | 票种 | 价格 |
 *       |---|---|
 *       | 平日全天 | 560 元/人 [E301] |
 *
 *   一堆竖线和短横线，手机上完全没法看。
 *
 * 为什么不用 markdown 渲染组件（mp-html / ua-markdown 之类）：
 *   1. uni-app 装插件要手动操作 HBuilderX，演示前的环境成本太高
 *   2. 聊天气泡宽度有限，表格再渲染也挤；转成"一行一条"反而更好读
 *   3. 这点转换逻辑自己写就够，不引依赖 = 少一个以后会坏的东西
 *
 * 注意：只做"降级展示"，不改语义。来源编号 [E301] 这类一律保留——
 * 那是本项目"可溯源"的可见证据，不能为了好看把它抹掉。
 */

/** 去掉行内的 markdown 标记，保留文字本身 */
function stripInline(line, streaming) {
  const out = String(line)
    // 链接 [文字](地址) -> 文字（地址）
    .replace(/\[([^\]]+)\]\(([^)]+)\)/g, '$1（$2）')
    // 加粗 / 斜体 / 删除线
    .replace(/\*\*([^*]+)\*\*/g, '$1')
    .replace(/(^|[^*])\*([^*]+)\*/g, '$1$2')
    .replace(/__([^_]+)__/g, '$1')
    .replace(/~~([^~]+)~~/g, '$1')
    // 行内代码
    .replace(/`([^`]+)`/g, '$1')
    // 标题符号
    .replace(/^\s{0,3}#{1,6}\s*/, '')
    // 引用符号
    .replace(/^\s{0,3}>\s?/, '')
    .trimEnd()
  return streaming ? stripDangling(out) : out
}

/**
 * 流式专属：抹掉"落单的标记"
 *
 * 成对的标记上面已经处理掉了，**剩下的这些必然是没写完的**——
 * 模型正在写"**560 元/人**"，此刻只到"**560 元"，屏幕上就会挂着一个孤零零的 `**`。
 * 它下一帧就会被补全，但闪一下也是噪音，所以流式期间先不显示。
 * 只在 streaming 分支调用，收尾渲染一律不碰（避免误伤正文里正常的符号）。
 */
function stripDangling(line) {
  return String(line)
    .replace(/\*\*/g, '')     // 没配对的加粗
    .replace(/~~/g, '')       // 没配对的删除线
    .replace(/`/g, '')        // 没配对的行内代码
}

/** 把一行表格拆成单元格 */
function splitRow(line) {
  return line
    .replace(/^\s*\|/, '')
    .replace(/\|\s*$/, '')
    .split('|')
    .map(cell => cell.trim())
}

/** 判断是不是表格的"分隔行"（|---|---|） */
function isDivider(line) {
  return /^\s*\|?[\s:|-]+\|[\s:|-]*$/.test(line) && line.indexOf('-') >= 0
}

/**
 * 主函数：markdown -> 纯文本
 * @param {string} text 模型返回的原始文本
 * @param {{streaming?: boolean}} [options] streaming=true 时按"还没写完"处理（见下）
 * @returns {string} 适合直接塞进 <text> 的纯文本
 */
export function formatReply(text, options = {}) {
  if (!text) return ''
  let lines = String(text).replace(/\r\n/g, '\n').split('\n')

  // 流式专属：把"还没写完的尾巴"藏掉
  //
  // 为什么必须单独处理：流式是逐 token 到的，任何时刻最后一行都可能是半截。
  // 表格最典型——`| 周末全天 | 780` 这一行还没收到收尾的 `|`，
  // 表格解析会把它当成一条完整数据行，于是先渲染成
  // "· 周末全天：780"，等真正的价格到了又变成"· 周末全天：780 元/人"。
  // 用户看到的是文字反复跳变，比慢一点更难受。
  // 所以：**流式期间丢掉最后一行不完整的表格行与没闭合的标记**，
  // 等它写完了下一帧自然会出现——只影响最后一行，前面的内容照常渲染。
  const streaming = !!options.streaming
  if (streaming && lines.length > 1) {
    const last = lines[lines.length - 1]
    const halfTableRow = /^\s*\|/.test(last) && !/\|\s*$/.test(last)
    const halfDivider = /^\s*\|?[\s:|-]*$/.test(last) && last.indexOf('-') >= 0
    if (halfTableRow || halfDivider) lines = lines.slice(0, -1)
  }

  const out = []

  for (let i = 0; i < lines.length; i++) {
    const raw = lines[i]

    // 1.表格：连续以 | 开头的行整体处理，转成"一行一条"
    if (/^\s*\|/.test(raw)) {
      const block = []
      while (i < lines.length && /^\s*\|/.test(lines[i])) {
        block.push(lines[i])
        i++
      }
      i-- // 退回一行，外层循环会再 +1

      const rows = block.filter(l => !isDivider(l)).map(splitRow)
      if (rows.length) {
        const header = rows[0]
        for (let r = 1; r < rows.length; r++) {
          const cells = rows[r]
          // 第一列当"名字"，其余列拼在后面；表头用来给列加标签
          const parts = []
          for (let c = 1; c < cells.length; c++) {
            if (!cells[c]) continue
            const label = header[c] && header[c] !== cells[0] ? '' : ''
            parts.push(label + cells[c])
          }
          out.push('· ' + cells[0] + (parts.length ? '：' + parts.join('，') : ''))
        }
      }
      out.push('')
      continue
    }

    // 2.分隔线 --- / *** 直接丢
    if (/^\s*([-*_])\1{2,}\s*$/.test(raw)) {
      out.push('')
      continue
    }

    // 3.列表项统一成 ·
    const bullet = raw.match(/^\s*[-*+]\s+(.*)$/)
    if (bullet) {
      out.push('· ' + stripInline(bullet[1], streaming))
      continue
    }
    const ordered = raw.match(/^\s*\d+[.、)]\s+(.*)$/)
    if (ordered) {
      out.push('· ' + stripInline(ordered[1], streaming))
      continue
    }

    out.push(stripInline(raw, streaming))
  }

  // 4.压缩连续空行（最多留一个）
  let result = out.join('\n').replace(/\n{3,}/g, '\n\n').trim()

  // 5.流式专属：抹掉结尾还没闭合的标记（`**加粗` 里后两个星号还没到，
  //   或者 `[E30` 这种引用的编号才写了一半）——这些字符对用户没有意义，
  //   只在屏幕上闪一下就没了。写完了自然会显示出来。
  if (streaming) result = trimPartialTail(result)

  return result
}

/**
 * 去掉结尾"没写完的标记"
 *
 * 只处理**结尾**：正文中间出现的 `**` 一定是完整的（模型不会把加粗写到一半又停），
 * 所以这里不会误伤正文。宁可不处理，也不要把正常内容切掉。
 */
function trimPartialTail(text) {
  return String(text)
    .replace(/\[E?\d*$/, '')      // 引用编号只写了一半：[E30
    .replace(/[*_`~|\s]+$/, '')   // 未闭合的加粗/代码/斜体标记
}

/**
 * 给消息做展示前的清洗：统一入口，页面里只调这一个
 * @param {string} content 原始文本
 * @param {{streaming?: boolean}} [options] 流式增量请传 { streaming: true }
 */
export function prettify(content, options = {}) {
  return formatReply(content, options)
}
