// 对消息存档与服务端交互数据统一做有界校验。
export const SCENE_INTENT_KEY = 'terrainIntent'
export const LAYOUT_RESORTS = ['wanlong']
const text = (value, max) => typeof value === 'string' ? value.slice(0, max) : ''
export function safeLearningUrl(value) {
	if (typeof value !== 'string' || value.length > 2048 || !/^https:\/\//i.test(value) || /[\s\\\u0000-\u001f\u007f]/.test(value)) return ''
	try { const url = new URL(value); return url.protocol === 'https:' && !url.username && !url.password ? url.href : '' } catch (_) { return '' }
}
export function normalizeGuidance(value) {
	const data = value && typeof value === 'object' ? value : {}
	const suggestions = (Array.isArray(data.suggestions) ? data.suggestions : []).slice(0, 3).map(item => ({ id: text(item?.id, 64), label: text(item?.label, 60), question: text(item?.question, 500) })).filter(item => item.id && item.label && item.question)
	const scenes = (Array.isArray(data.scenes) ? data.scenes : []).slice(0, 3).map(item => ({ resort_id: text(item?.resort_id, 64), name: text(item?.name, 80), preview_url: safeLearningUrl(item?.preview_url) })).filter(item => /^[a-z0-9_-]+$/.test(item.resort_id) && item.name)
	const resources = (Array.isArray(data.learning?.resources) ? data.learning.resources : []).slice(0, 3).map(item => ({ title: text(item?.title, 120), url: safeLearningUrl(item?.url), source: text(item?.source, 120), level: text(item?.level, 40), topic: text(item?.topic, 80), updated_at: text(item?.updated_at, 40) })).filter(item => item.title && item.url && item.source && item.updated_at)
	const media = (Array.isArray(data.media) ? data.media : []).slice(0, 3).map(item => ({
		title: text(item?.title, 120).trim(), resort_id: text(item?.resort_id, 64).trim(),
		kind: item?.kind, url: safeLearningUrl(item?.url), source: text(item?.source, 120).trim(),
		published_at: text(item?.published_at, 40).trim() || '未注明', checked_at: text(item?.checked_at, 40).trim(),
		verification: item?.verification, rights: item?.rights, notice: text(item?.notice, 400).trim(),
    ...Object.fromEntries(['category', 'credit', 'author', 'board', 'level', 'topic', 'duration', 'recommendation'].filter(key => typeof item?.[key] === 'string' && item[key].trim()).map(key => [key, text(item[key], 400).trim()])),
    ...(item?.rights === 'authorized' && item?.verification === 'content_checked' && safeLearningUrl(item?.image_url) ? { image_url: safeLearningUrl(item.image_url) } : {})
	})).filter(item => item.title && /^[a-z0-9_-]+$/.test(item.resort_id) &&
		['photo', 'video', 'map'].includes(item.kind) && item.url && item.source && item.checked_at &&
		['page_checked', 'content_checked'].includes(item.verification) &&
		['link_only', 'authorized'].includes(item.rights) && item.notice)
	const runs = (Array.isArray(data.run_brief?.runs) ? data.run_brief.runs : []).slice(0, 6).map(run => ({
		name: text(run?.name, 40).trim(), difficulty: text(run?.difficulty, 20).trim(),
		...(Number.isFinite(run?.length_m) && run.length_m > 0 ? { length_m: run.length_m } : {}),
		...(Number.isFinite(run?.vertical_m) && run.vertical_m > 0 ? { vertical_m: run.vertical_m } : {}),
		...(Number.isFinite(run?.slope_deg) && run.slope_deg > 0 ? { slope_deg: run.slope_deg } : {}),
		...(text(run?.note, 120).trim() ? { note: text(run.note, 120).trim() } : {})
	})).filter(run => run.name && run.difficulty)
	const briefTitle = text(data.run_brief?.title, 40).trim()
	const run_brief = briefTitle && (runs.length || text(data.run_brief?.caveat, 240).trim()) ? {
		title: briefTitle, source: text(data.run_brief?.source, 80).trim(), date: text(data.run_brief?.date, 40).trim() || '未注明',
		...(text(data.run_brief?.caveat, 240).trim() ? { caveat: text(data.run_brief.caveat, 240).trim() } : {}),
		runs
	} : null
	return { suggestions, scenes, learning: { notice: text(data.learning?.notice, 400), resources }, media, ...(run_brief ? { run_brief } : {}) }
}
export function validateSceneIntent(value, resortIds) {
	if (!value || !resortIds.includes(value.resort_id)) return null
	const intent = { resort_id: value.resort_id, filter: value.filter === 'beginner' ? 'beginner' : 'all' }
	const focus = text(value.focus, 40).trim()
	if (focus) intent.focus = focus
	return intent
}
