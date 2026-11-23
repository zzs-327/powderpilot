export function safeMediaUrl(value) {
  if (typeof value !== 'string' || value.length > 2048 || !/^https:\/\//i.test(value) || /[\s\\\u0000-\u001f\u007f]/.test(value)) return ''
  try { const url = new URL(value); return url.protocol === 'https:' && !url.username && !url.password ? url.href : '' } catch (_) { return '' }
}
export function authorizedImage(resource) {
  return resource?.rights === 'authorized' && resource?.verification === 'content_checked' && safeMediaUrl(resource.image_url) !== safeMediaUrl(resource.url) && ['photo', 'map'].includes(resource.kind) ? safeMediaUrl(resource.image_url) : ''
}
export function verificationLabel(resource) {
  return resource?.verification === 'content_checked' ? '内容已核验' : resource?.verification === 'page_checked' ? '原页已核验 · 不代表已观看视频或核验画面/教学内容' : '核验状态未注明'
}
export function normalizeMedia(resources, limit = 100) {
  const seen = new Set()
  return (Array.isArray(resources) ? resources : []).slice(0, limit).filter(r => {
    const url = safeMediaUrl(r?.url)
    if (!r || !['photo', 'video', 'map'].includes(r.kind) || !['page_checked', 'content_checked'].includes(r.verification) || !url || seen.has(url)) return false
    seen.add(url)
    return true
  }).map(r => ({ ...r, url: safeMediaUrl(r.url), image_url: authorizedImage(r) }))
}
export function createRequestGuard() {
  let generation = 0
  return { next: () => ++generation, current: id => id === generation }
}
/** 预算汇总：按币种分别合计 counted 明细，不同币种不相加；共用费用（counted=false）只列示 */
export function budgetSummary(budget) {
  const items = Array.isArray(budget?.items) ? budget.items : []
  const known = items.filter(i => typeof i.amount === 'number' && Number.isFinite(i.amount) && i.amount > 0)
  const fallback = String(budget?.currency || 'CNY').toUpperCase()
  const totals = new Map()
  for (const i of known) {
    if (i.counted === false) continue
    const code = String(i.currency || fallback).toUpperCase()
    totals.set(code, (totals.get(code) || 0) + i.amount)
  }
  const subtotals = [...totals].map(([currency, total]) => ({ currency, total }))
  const missing = Array.isArray(budget?.missing) ? budget.missing.filter(x => typeof x === 'string' && x) : []
  const listed = known.length > 0
  return {
    subtotal: subtotals[0]?.total || 0,
    currency: subtotals[0]?.currency || fallback,
    subtotals,
    missing,
    uncountedOnly: listed && subtotals.length === 0,
    pending: !items.length || known.length !== items.length || missing.length > 0 || budget?.complete !== true
  }
}
const UNIT_BASIS_LABEL = {
  per_person: '按人计', per_instructor: '按教练计（非每人价）', per_room: '按房间计',
  per_seat_one_way: '单程每座（往返需另计）', per_set: '按套计', per_item: '按件计'
}
const TAX_STATUS_LABEL = { included: '含税', excluded: '不含税', unknown: '税费待确认' }
/** 价格口径短语（与后端 rag/pricing.py 的 price_qualifiers 同义） */
export function priceTerms(row) {
  const terms = []
  if (row?.valid_from || row?.valid_to) terms.push(`适用 ${row.valid_from || '?'}～${row.valid_to || '?'}`)
  if (row?.season) terms.push(row.season)
  if (UNIT_BASIS_LABEL[row?.unit_basis]) terms.push(UNIT_BASIS_LABEL[row.unit_basis])
  if (TAX_STATUS_LABEL[row?.tax_status]) terms.push(TAX_STATUS_LABEL[row.tax_status])
  if (Array.isArray(row?.includes) && row.includes.length) terms.push('含：' + row.includes.join('、'))
  if (Array.isArray(row?.excludes) && row.excludes.length) {
    const known = row.excludes.filter(x => x !== 'unknown')
    terms.push(known.length ? '不含：' + known.join('、') : '不含项未逐项说明')
  }
  if (row?.budget_usable === false) terms.push('仅供参考，不计入预算小计')
  return terms
}
/** 整表里只要有一条不能计入预算，就在列表上方提示，避免把已知单价看成完整总账 */
export function priceListNotice(prices) {
  const rows = Array.isArray(prices) ? prices : []
  if (!rows.some(row => row && row.budget_usable === false)) return ''
  return '这些是已知单价，有的不能计入预算。不能加总成完整旅行总价，不能把缺失费用当成 0，不同币种也不要换算后相加。'
}
export function priceConditions(row) {
  return (Array.isArray(row?.conditions) ? row.conditions : [])
    .filter(item => typeof item === 'string' && item.trim())
    .map(item => item.trim())
}
/** 金额展示：CNY 用 ¥；其他币种前缀 ISO 码，避免把外币数字显示成「元」 */
export function formatMoney(amount, currency = 'CNY') {
  if (!(typeof amount === 'number' && Number.isFinite(amount) && amount > 0)) return '待确认'
  const code = String(currency || 'CNY').toUpperCase()
  if (code === 'CNY') return '¥' + amount
  return code + ' ' + amount
}
