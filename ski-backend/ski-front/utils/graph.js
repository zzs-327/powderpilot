// 关系图页的纯数据逻辑：不碰 uni API、不碰 DOM。
//
// 为什么单独一层：图谱页的"能不能画出来"不该只能靠在 HBuilderX 里肉眼看。
// 这一层可以在 Node 里直接对着 data/graph/graph.json 跑断言
// （见 scripts/export_graph.py 导出的 stats 与 tests/test_graph_export.py 的契约守卫）。

// 边类型只有 4 种（契约见 scripts/export_graph.py 的 docstring）
export const EDGE_LABELS = {
	has_run: '包含雪道',
	has_lift: '包含缆车',
	suitable_for: '适合水平',
	serves: '服务对象'
}

export const NODE_TYPE_LABELS = { resort: '雪场', run: '雪道', lift: '缆车', level: '水平' }

// 水平三档配色与"雪场库"页的徽章保持一致，免得同一个概念两种颜色
export const LEVEL_COLORS = { beginner: '#12a150', intermediate: '#1f6feb', advanced: '#dc2626' }
export const LEVEL_ORDER = ['beginner', 'intermediate', 'advanced']

// 属性键 → 中文（后端 attrs 用的是 YAML 原始键名，中文只在展示层做）
export const ATTR_LABELS = {
	region: '地区', aliases: '别名', data_status: '雪场默认档', weakest_status: '整场最弱档',
	status_map: '逐类型档位', runs_recorded: '本库收录雪道', lifts_recorded: '本库收录缆车',
	address: '地址', phone: '电话', season: '雪季', website: '官网',
	runs_total: '来源口径雪道总数', vertical_drop_m: '垂直落差（米）', price_updated_at: '价格有效期至',
	difficulty: '难度', length_m: '长度（米）', top_altitude_m: '最高海拔（米）',
	avg_slope_deg: '平均坡度（°）', max_slope_deg: '最大坡度（°）', vertical_m: '落差（米）',
	note: '备注', category: '类别', spec: '规格', capacity_per_hour: '运力（人/小时）',
	serves: '档案里的服务对象', level: '水平'
}

// 节点 evidence 里的中文标签由导出脚本给（与聊天里的证据块同一张表），
// 只对 attrs.status_map 里那串 key 需要一份兜底
export const STATUS_LABELS = {
	verified: '✅ 官方直采', corroborated: '🔁 多源印证',
	transcribed: '⚠️ 二手转引', sample: '🚫 示例数据'
}

// 与后端 rag/parser.py 的 DOC_TYPE_LABEL 对应（只在"逐类型档位"里用）
export const DOC_TYPE_LABELS = {
	overview: '概览', transport: '交通参考', run: '雪道', lift: '索道',
	price: '价格', tip: '避坑与经验', video: '教学视频', photo: '实拍图'
}

/**
 * 取单雪场视图：本雪场节点 + 由边带出来的全局 level 节点
 * @param {object} graph graph.json 全文
 * @param {string} resortId 雪场 id，如 'wanlong'
 */
export function buildView(graph, resortId) {
	const nodes = (graph.nodes || []).filter((n) => n.resort_id === resortId)
	const edges = (graph.edges || []).filter((e) => e.resort_id === resortId)
	const byId = new Map((graph.nodes || []).map((n) => [n.id, n]))
	const ownIds = new Set(nodes.map((n) => n.id))
	const extra = []
	for (const edge of edges) {
		for (const id of [edge.source, edge.target]) {
			if (ownIds.has(id)) continue
			ownIds.add(id)
			if (byId.has(id)) extra.push(byId.get(id))
		}
	}
	return { nodes: nodes.concat(extra), edges, byId }
}

/**
 * 雪道的水平：直接从 suitable_for 边读。
 * 为什么不在前端另抄一份"难度→水平"映射：抄一份就会和后端各说各话，
 * 而且档案里那些没映射的难度（公园/连接道）本来就不该有边。
 */
export function levelOf(view, nodeId) {
	const edge = view.edges.find((e) => e.type === 'suitable_for' && e.source === nodeId)
	return edge ? edge.target.replace('level:', '') : ''
}

/** 二维、三维和实体列表共享同一筛选口径。 */
export function filterEntities(view, filter = 'all', query = '') {
	const keyword = query.trim().toLocaleLowerCase()
	return (view?.nodes || []).filter((node) => {
		if (node.type !== 'run' && node.type !== 'lift') return false
		const matchesType = filter === 'all' || (filter === 'lift'
			? node.type === 'lift'
			: node.type === 'run' && levelOf(view, node.id) === filter)
		return matchesType && (!keyword || node.label.toLocaleLowerCase().includes(keyword))
	})
}

/** 某个节点的相邻边（带方向和另一端的名字），侧栏用它列关系 */
export function adjacent(view, nodeId) {
	return view.edges
		.filter((e) => e.source === nodeId || e.target === nodeId)
		.map((e) => {
			const outward = e.source === nodeId
			const otherId = outward ? e.target : e.source
			const other = view.byId.get(otherId)
			return {
				type: e.type,
				typeLabel: EDGE_LABELS[e.type] || e.type,
				direction: outward ? 'out' : 'in',
				otherId,
				otherLabel: other ? other.label : otherId,
				otherType: other ? other.type : ''
			}
		})
}

/** 是否已过保鲜期：与今天比较（导出时写死 expired 会随时间失真，所以前端算） */
export function isExpired(evidence, today) {
	const expire = evidence && evidence.expire_at
	return Boolean(expire && expire < today)
}

/** 本地日期 YYYY-MM-DD（不用 toISOString：那是 UTC，跨时区会差一天） */
export function todayString(now) {
	const date = now || new Date()
	const pad = (n) => String(n).padStart(2, '0')
	return date.getFullYear() + '-' + pad(date.getMonth() + 1) + '-' + pad(date.getDate())
}
