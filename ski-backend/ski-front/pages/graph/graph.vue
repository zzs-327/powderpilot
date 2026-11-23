<template>
	<view class="page">
		<view class="hero">
			<view class="hero-copy">
				<text class="eyebrow">POWDERPILOT · TERRAIN VIEW</text>
				<text class="hero-title">三维雪场</text>
				<text class="hero-sub">以雪山空间示意探索雪道、缆车与适合水平 · {{ loadError || (loaded ? summary : '正在读取图谱…') }}</text>
			</view>
			<view class="hero-mark"><text>⛰</text></view>
		</view>

		<scroll-view class="chips" scroll-x>
			<view v-for="item in resortList" :key="item.id" class="chip"
				:class="{ active: item.id === resortId }" @click="pick(item.id)">
				<text class="chip-text">{{ item.name }}<text v-if="item.count"> · {{ item.count }}</text></text>
			</view>
		</scroll-view>

		<view class="filters">
			<view v-for="item in filterList" :key="item.key" class="filter-chip"
				:class="{ active: item.key === terrainFilter }" @click="setFilter(item.key)">
				<view v-if="item.color" class="legend-dot" :style="{ backgroundColor: item.color }"></view>
				<text>{{ item.label }} · {{ item.count }}</text>
			</view>
		</view>

		<view class="entity-search"><input v-model="searchQuery" placeholder="搜索雪道或缆车名称" aria-label="搜索雪道或缆车" /><text v-if="searchQuery" @click="searchQuery = ''">清空</text></view>

		<view class="terrain-wrap">
			<view class="terrain-head">
				<view>
					<text class="terrain-kicker">{{ currentResort ? currentResort.label : '雪场' }}</text>
					<text class="terrain-title">{{ hasLayout ? '按官方全景图布置 · 可探索档案' : '山体关系 · 可探索档案' }}</text>
				</view>
				<text class="terrain-hint">点雪道或设施查看证据</text>
			</view>
			<view v-if="hasLayout" class="orientation"><text>{{ orientationText }}</text></view>

			<!-- #ifdef H5 -->
			<view id="terrain-3d" class="terrain-3d" v-show="!terrainError"></view>
			<view class="terrain-controls"><text>{{ terrainError || (terrainReady ? '拖动旋转 · 双指/滚轮缩放 · 点击标签查看证据' : '正在加载 3D 雪山…') }}</text><text v-if="terrainReady" @click="resetTerrain">重置视角</text></view>
			<!-- #endif -->
			<view class="mountain-stage" v-show="!terrainReady">
				<view class="sky-glow"></view>
				<view class="mountain-back"></view>
				<view class="mountain-main"></view>
				<view class="mountain-snow"></view>
				<view class="tree-line tree-left">♠ ♠ ♠ ♠ ♠</view>
				<view class="tree-line tree-right">♠ ♠ ♠ ♠</view>
				<view class="summit"><text class="summit-name">{{ currentResort ? currentResort.label : '雪场' }}</text><text class="summit-meta">人工策展 · 可溯源</text></view>

				<view v-for="lane in laneCards" :key="lane.key" class="lane-card" :class="lane.key">
					<view class="lane-dot" :style="{ backgroundColor: lane.color }"></view>
					<text>{{ lane.label }} {{ lane.count }}</text>
				</view>

				<view v-for="item in runVisuals" :key="item.id" class="run-marker" :class="{ selected: item.id === activeId }"
					:style="item.style" @click="showNode(item.id)">
					<view class="run-line" :style="{ backgroundColor: item.color }"></view>
					<text class="run-label">{{ item.label }}</text>
				</view>

				<view v-for="item in liftVisuals" :key="item.id" class="lift-marker" :class="{ selected: item.id === activeId }"
					:style="item.style" @click="showNode(item.id)">
					<view class="lift-icon">↗</view><text>{{ item.label }}</text>
				</view>

				<view class="resort-marker" @click="showNode(resortId ? 'resort:' + resortId : '')">
					<text class="resort-pin">●</text><text>雪场档案</text>
				</view>
				<view v-if="!loaded" class="loading-mask"><text>正在读取地形档案…</text></view>
			</view>

			<view class="terrain-legend">
				<view class="legend-item"><view class="legend-line beginner"></view><text>初级坡</text></view>
				<view class="legend-item"><view class="legend-line intermediate"></view><text>中级坡</text></view>
				<view class="legend-item"><view class="legend-line advanced"></view><text>高级坡</text></view>
				<view class="legend-item"><view class="legend-lift">↗</view><text>缆车</text></view>
			</view>
			<view class="notice"><text>空间示意 · 非真实地形或导航地图。新增雪面细节、视觉宽度与设施动画不代表实测或实时运行。山形、雪道位置与缆车布局为展示设计，不表示实际高度、连接或通行关系。</text></view>
			<view v-if="notice" class="notice"><text>{{ notice }}</text></view>
			<view class="entity-list">
				<text class="section-title">当前显示 · {{ visibleEntities.length }} / {{ entities.length }}</text>
				<text v-if="!visibleEntities.length" class="empty-result">没有匹配的雪道或缆车，请更换筛选或搜索词。</text>
				<view class="entity-grid">
					<view v-for="node in visibleEntities" :key="node.id" class="entity-chip" :class="{ selected: node.id === activeId }" @click="showNode(node.id)">
						<view class="lane-dot" :style="{ backgroundColor: node.type === 'lift' ? '#c67b2b' : (LEVEL_COLORS[levelOf(view, node.id)] || '#64748b') }"></view>
						<text>{{ node.label }}</text>
					</view>
				</view>
			</view>
		</view>

		<view v-if="detail" class="mask" @click="detail = null">
			<view class="sheet" @click.stop>
				<view class="sheet-head">
					<text class="sheet-title">{{ detail.title }}</text>
					<text v-if="detail.statusLabel" class="badge" :class="detail.statusKey">{{ detail.statusLabel }}</text>
				</view>
				<text class="sheet-sub">{{ detail.subtitle }}</text>
				<scroll-view class="sheet-body" scroll-y>
					<view v-for="(row, i) in detail.attrs" :key="'a' + i" class="row"><text class="row-label">{{ row.label }}</text><text class="row-value">{{ row.value }}</text></view>
					<view v-if="detail.statusMap.length" class="note"><text class="note-title">逐类型档位</text><view v-for="(row, i) in detail.statusMap" :key="'s' + i" class="row"><text class="row-label">{{ row.label }}</text><text class="row-value">{{ row.value }}</text></view></view>
					<view v-if="detail.evidence" class="note">
						<text class="note-title">这条数据凭什么可信</text>
						<view class="row"><text class="row-label">来源</text><text class="row-value">{{ detail.evidence.source }}</text></view>
						<view class="row"><text class="row-label">来源级别</text><text class="row-value">{{ detail.evidence.source_type_label }}</text></view>
						<view class="row"><text class="row-label">信息日期</text><text class="row-value">{{ detail.evidence.date || '未标注' }}</text></view>
						<view class="row"><text class="row-label">保鲜期至</text><text class="row-value">{{ detail.evidence.expire_at || '无时效' }}{{ expired ? '（⚠️ 已过期）' : '' }}</text></view>
						<view class="row"><text class="row-label">数据档位</text><text class="row-value">{{ detail.evidence.status_label || '不适用（本库整理的口径）' }}</text></view>
					</view>
					<text class="section-title">相邻关系（{{ detail.links.length }} 条）</text>
					<view v-for="(link, i) in detail.links" :key="'l' + i" class="line relation-link" @click="followLink(link.otherId)"><text class="badge small" :class="link.direction">{{ link.direction === 'out' ? '出' : '入' }}</text><text class="line-text">{{ link.typeLabel }} · {{ link.otherLabel }}</text><text class="relation-arrow">›</text></view>
				</scroll-view>
				<view class="sheet-close" @click="detail = null"><text>关闭</text></view>
			</view>
		</view>
	</view>
</template>

<script setup>
	import { computed, ref, watch, nextTick, onBeforeUnmount } from 'vue'
	import { onHide, onShow } from '@dcloudio/uni-app'
	import { ATTR_LABELS, DOC_TYPE_LABELS, EDGE_LABELS, LEVEL_COLORS, NODE_TYPE_LABELS, STATUS_LABELS, adjacent, buildView, filterEntities, isExpired, levelOf, todayString } from '@/utils/graph.js'

	// 布局模块没有任何宿主 API 依赖，所有端都可导入（模板里的朝向说明要用到它）
	import { WANLONG_PROFILE, routeFor } from '@/utils/wanlong.js'
	import { LAYOUT_RESORTS, SCENE_INTENT_KEY, validateSceneIntent } from '@/utils/guidance.js'
	// #ifdef H5
	import { mountTerrain } from '@/utils/terrain.js'
	// #endif
	const terrainReady = ref(false)
	const terrainError = ref('')
	const terrainFilter = ref('all')
	const searchQuery = ref('')
	let terrainInstance
	let terrainVersion = 0
	//: 只有做过专项布局的雪场才有"按全景图布置"的说法；其余是通用示意
	const hasLayout = computed(() => LAYOUT_RESORTS.includes(resortId.value))
	const orientationText = computed(() => hasLayout.value && WANLONG_PROFILE.orientation
		? `${WANLONG_PROFILE.orientation} · 路线按官方全景图的相对位置示意，不等比、不用于导航`
		: '')
	const filterList = computed(() => {
		const runs = (view.value?.nodes || []).filter((node) => node.type === 'run')
		const count = (level) => runs.filter((node) => levelOf(view.value, node.id) === level).length
		return [
			{ key: 'all', label: '全部', count: entities.value.length, color: '' },
			{ key: 'beginner', label: '初级', count: count('beginner'), color: LEVEL_COLORS.beginner },
			{ key: 'intermediate', label: '中级', count: count('intermediate'), color: LEVEL_COLORS.intermediate },
			{ key: 'advanced', label: '高级', count: count('advanced'), color: LEVEL_COLORS.advanced },
			{ key: 'lift', label: '缆车', count: (view.value?.nodes || []).filter((node) => node.type === 'lift').length, color: '#e5a94f' }
		]
	})
	function setFilter(key) {
		terrainFilter.value = key
		syncVisibility()
	}
	function syncVisibility() { terrainInstance?.setVisibleIds(visibleEntities.value.map((node) => node.id)) }
	watch(searchQuery, syncVisibility)
	async function renderTerrain() {
		// #ifdef H5
		const version = ++terrainVersion
		terrainInstance?.dispose()
		terrainInstance = null
		terrainReady.value = false
		terrainError.value = ''
		await nextTick()
		const profile = hasLayout.value ? WANLONG_PROFILE : null
		const payload = entities.value.map((node, index) => {
			const level = levelOf(view.value, node.id)
			const entity = { id: node.id, label: node.label, type: node.type, level }
			return { ...entity, points: routeFor(profile, entity, index).points }
		})
		try {
			const instance = await mountTerrain(document.getElementById('terrain-3d'), payload, showNode, { profile, filter: terrainFilter.value })
			if (version !== terrainVersion) { instance.dispose(); return }
			terrainInstance = instance
			syncVisibility()
			if (!pageVisible) instance.pause()
			terrainReady.value = true
			if (activeId.value) instance.highlight(activeId.value)
		} catch (error) {
			if (version === terrainVersion) terrainError.value = '3D 加载失败，已保留二维示意与实体列表：' + error.message
		}
		// #endif
	}
	function resetTerrain() { terrainInstance?.reset() }
	onBeforeUnmount(() => { terrainVersion++; terrainInstance?.dispose() })
	const GRAPH_URLS = ['/static/graph/graph.json', '/graph/graph.json']
	const graphData = ref(null)
	const loaded = ref(false)
	const resortId = ref('wanlong')
	const detail = ref(null)
	//: 当前选中的雪道/缆车：三维里高亮它，二维列表和侧栏也用它做呼应
	const activeId = ref('')
	const notice = computed(() => {
		const stats = graphData.value?.stats || {}
		const unmapped = (stats.unmapped_runs || []).filter((item) => item.resort_id === resortId.value)
		const noServes = (stats.lifts_without_serves || []).filter((item) => item.resort_id === resortId.value)
		return [unmapped.length ? `${unmapped.length} 条雪道未映射到水平分层` : '', noServes.length ? `${noServes.length} 条缆车未写服务对象` : ''].filter(Boolean).join('；')
	})
	const entities = computed(() => (view.value?.nodes || []).filter((node) => node.type === 'run' || node.type === 'lift'))
	const visibleEntities = computed(() => filterEntities(view.value, terrainFilter.value, searchQuery.value))
	const visibleIds = computed(() => new Set(visibleEntities.value.map((node) => node.id)))
	const loadError = ref('')

	const resortList = computed(() => {
		if (!graphData.value) return []
		const counts = new Map()
		for (const node of graphData.value.nodes) if (node.type === 'run' || node.type === 'lift') counts.set(node.resort_id, (counts.get(node.resort_id) || 0) + 1)
		return graphData.value.nodes.filter((node) => node.type === 'resort').map((node) => ({ id: node.resort_id, name: node.label, count: counts.get(node.resort_id) || 0 }))
	})
	const view = computed(() => graphData.value ? buildView(graphData.value, resortId.value) : null)
	const currentResort = computed(() => view.value && view.value.nodes.find((node) => node.type === 'resort'))
	const summary = computed(() => {
		if (!view.value) return ''
		return `${view.value.nodes.filter((n) => n.type === 'run').length} 条雪道 · ${view.value.nodes.filter((n) => n.type === 'lift').length} 条缆车 · 点击节点查看来源`
	})
	const laneCards = computed(() => ['beginner', 'intermediate', 'advanced'].map((key) => ({ key, label: { beginner: '初级', intermediate: '中级', advanced: '高级' }[key], color: LEVEL_COLORS[key], count: (view.value?.nodes || []).filter((node) => node.type === 'run' && levelOf(view.value, node.id) === key).length })))
	const runVisuals = computed(() => {
		const runs = (view.value?.nodes || []).filter((node) => node.type === 'run')
		const seen = { beginner: 0, intermediate: 0, advanced: 0, other: 0 }
		return runs.map((node) => {
			const level = levelOf(view.value, node.id) || 'other'
			const index = seen[level]++
			const color = LEVEL_COLORS[level] || '#64748b'
			const lane = level === 'beginner' ? 57 : level === 'intermediate' ? 39 : level === 'advanced' ? 21 : 67
			const left = 14 + ((index * 37 + (level === 'advanced' ? 9 : 0)) % 72)
			const top = lane + ((index % 3) * 4)
			return { id: node.id, label: node.label, color, style: { left: `${left}%`, top: `${top}%`, transform: `rotate(${(index % 2 ? 7 : -6)}deg)` } }
		}).filter((node) => visibleIds.value.has(node.id))
	})
	const liftVisuals = computed(() => (view.value?.nodes || []).filter((node) => node.type === 'lift').map((node, index) => ({ id: node.id, label: node.label, style: { left: `${8 + ((index * 61) % 82)}%`, top: `${17 + ((index * 13) % 54)}%` } })).filter((node) => visibleIds.value.has(node.id)))
	const expired = computed(() => Boolean(detail.value?.evidence && isExpired(detail.value.evidence, todayString())))

	function requestJson(url) {
		return new Promise((resolve, reject) => {
			uni.request({ url, method: 'GET', success: (res) => { let body = res.data; if (typeof body === 'string') { try { body = JSON.parse(body) } catch (e) { reject(new Error('graph.json 不是合法 JSON')); return } } if (res.statusCode === 200 && body?.nodes) resolve(body); else reject(new Error(`读取失败（${res.statusCode}）`)) }, fail: (err) => reject(new Error(err.errMsg || '网络错误')) })
		})
	}
	async function loadGraph() { for (const url of GRAPH_URLS) { try { return await requestJson(url) } catch (e) {} } throw new Error('graph.json 读不到：先跑 python -m scripts.export_graph') }
	function showNode(nodeId) {
		const current = view.value
		const node = current?.byId.get(nodeId)
		if (!node) return
		activeId.value = nodeId
		terrainInstance?.highlight(nodeId)
		const extra = node.attrs.difficulty || node.attrs.category || ''
		detail.value = { title: node.label, subtitle: (NODE_TYPE_LABELS[node.type] || node.type) + (extra ? ` · ${extra}` : ''), statusLabel: node.evidence.status_label, statusKey: node.evidence.data_status, evidence: node.evidence, attrs: Object.keys(node.attrs).filter((key) => key !== 'status_map' && node.attrs[key] !== '').map((key) => ({ label: ATTR_LABELS[key] || key, value: formatValue(node.attrs[key]) })), statusMap: Object.keys(node.attrs.status_map || {}).map((key) => ({ label: DOC_TYPE_LABELS[key] || key, value: STATUS_LABELS[node.attrs.status_map[key]] || node.attrs.status_map[key] })), links: adjacent(current, nodeId) }
	}
	function followLink(id) {
		const node = view.value?.byId.get(id)
		if (node && (node.type === 'run' || node.type === 'lift')) { terrainFilter.value = 'all'; searchQuery.value = ''; syncVisibility() }
		showNode(id)
	}
	function formatValue(value) { if (Array.isArray(value)) return value.join('、'); if (value && typeof value === 'object') return JSON.stringify(value); return String(value) }
	function pick(id) { if (id !== resortId.value) { resortId.value = id; detail.value = null; activeId.value = ''; terrainFilter.value = 'all'; searchQuery.value = ''; renderTerrain() } }
	async function init() {
		if (loaded.value) { applyIntent(); return }
		try { graphData.value = await loadGraph(); if (!resortList.value.some((item) => item.id === resortId.value)) resortId.value = resortList.value[0]?.id || ''; loaded.value = true; applyIntent(); renderTerrain() } catch (e) { loadError.value = e.message || String(e); uni.showToast({ title: loadError.value, icon: 'none', duration: 3000 }) }
	}
	let pendingIntent = null
	function consumeIntent() {
		const intent = uni.getStorageSync(SCENE_INTENT_KEY)
		if (intent) { uni.removeStorageSync(SCENE_INTENT_KEY); pendingIntent = intent }
	}
	function applyIntent() {
		if (!pendingIntent || !loaded.value) return
		const intent = validateSceneIntent(pendingIntent, resortList.value.map(item => item.id))
		pendingIntent = null
		if (!intent) { uni.showToast({ title: '该雪场暂无可用场景', icon: 'none' }); return }
		pick(intent.resort_id)
		terrainFilter.value = intent.focus ? 'all' : intent.filter
		searchQuery.value = intent.focus || ''
		syncVisibility()
		if (!intent.focus || !view.value) return
		const runs = view.value.nodes.filter((node) => node.type === 'run' && node.resort_id === intent.resort_id)
		const exact = runs.find((node) => node.label === intent.focus) || runs.find((node) => node.label.includes(intent.focus) || intent.focus.includes(node.label))
		if (exact) showNode(exact.id)
	}
	let pageVisible = false
	onShow(() => {
		if (!uni.getStorageSync('token')) { uni.reLaunch({ url: '/pages/login/login' }); return }
		consumeIntent()
		pageVisible = true
		terrainInstance?.resume()
		init()
	})
	onHide(() => { pageVisible = false; terrainInstance?.pause() })
</script>

<style>
	.page { min-height: calc(100vh - var(--window-top, 0px) - var(--window-bottom, 0px)); box-sizing: border-box; background: #eef3f7; padding-bottom: calc(var(--window-bottom, 50px) + 24rpx + env(safe-area-inset-bottom, 0px)); }
	.hero { display: flex; justify-content: space-between; align-items: center; padding: 30rpx 34rpx 28rpx; color: #fff; background: linear-gradient(135deg, #102f4b 0%, #1d5271 62%, #638c9b 100%); }
	.hero-copy { flex: 1; }.eyebrow { display: block; color: #b9d8d8; font-size: 17rpx; letter-spacing: 3rpx; }.hero-title { display: block; margin-top: 8rpx; font-size: 42rpx; font-weight: 700; }.hero-sub { display: block; margin-top: 10rpx; color: #d3e6e9; font-size: 21rpx; line-height: 31rpx; }.hero-mark { width: 100rpx; height: 100rpx; border-radius: 50%; background: rgba(255,255,255,.13); display: flex; align-items: center; justify-content: center; }.hero-mark text { font-size: 62rpx; color: #e9f4ef; }
	.chips { white-space: nowrap; padding: 17rpx 22rpx 9rpx; height: 93rpx; box-sizing: border-box; }.chip { display: inline-block; margin-right: 12rpx; padding: 10rpx 22rpx; border: 1rpx solid #dce6ec; border-radius: 999rpx; background: #fff; }.chip.active { background: #24738b; border-color: #24738b; }.chip-text { color: #526879; font-size: 23rpx; }.chip.active .chip-text { color: #fff; }
	.terrain-wrap { margin: 6rpx 16rpx 0; border-radius: 24rpx; background: #fff; box-shadow: 0 5rpx 20rpx rgba(25, 58, 75, .09); overflow: hidden; }.terrain-head { display: flex; justify-content: space-between; align-items: flex-end; padding: 24rpx 25rpx 18rpx; }.terrain-kicker { display: block; color: #1b6e83; font-size: 29rpx; font-weight: 700; }.terrain-title { display: block; color: #6b7f8d; font-size: 20rpx; margin-top: 5rpx; }.terrain-hint { color: #9aabb4; font-size: 18rpx; }
	.mountain-stage { position: relative; height: 860rpx; overflow: hidden; background: linear-gradient(#d9eef2 0%, #f5faf8 53%, #dce9e6 53%, #c8d9d4 100%); }.sky-glow { position: absolute; width: 430rpx; height: 430rpx; right: -100rpx; top: -170rpx; border-radius: 50%; background: rgba(255,255,255,.55); }.mountain-back, .mountain-main, .mountain-snow { position: absolute; left: -18%; width: 136%; transform: rotate(-4deg); border-radius: 52% 48% 0 0 / 27% 30% 0 0; }.mountain-back { top: 165rpx; height: 700rpx; background: #a9c4c1; opacity: .52; transform: rotate(8deg); }.mountain-main { top: 230rpx; height: 720rpx; background: linear-gradient(128deg, #477970, #255b60 48%, #174c59); }.mountain-snow { top: 211rpx; height: 260rpx; background: #edf7f4; clip-path: polygon(0 76%, 18% 55%, 31% 64%, 47% 8%, 61% 55%, 76% 37%, 100% 72%, 100% 100%, 0 100%); opacity: .95; }
	.summit { position: absolute; top: 255rpx; left: 0; right: 0; text-align: center; text-shadow: 0 2rpx 8rpx rgba(10,47,53,.35); }.summit-name { display: block; color: #fff; font-size: 31rpx; font-weight: 700; }.summit-meta { display: block; margin-top: 5rpx; color: #d6ece8; font-size: 18rpx; }
	.lane-card { position: absolute; left: 24rpx; z-index: 4; display: flex; align-items: center; padding: 7rpx 12rpx; border-radius: 999rpx; background: rgba(255,255,255,.86); color: #425965; font-size: 19rpx; box-shadow: 0 2rpx 8rpx rgba(20,60,65,.12); }.lane-card.beginner { top: 57%; }.lane-card.intermediate { top: 39%; }.lane-card.advanced { top: 21%; }.lane-dot { width: 13rpx; height: 13rpx; margin-right: 7rpx; border-radius: 50%; }
	.run-marker { position: absolute; z-index: 5; width: 112rpx; transform-origin: left center; }.run-line { height: 8rpx; width: 105rpx; border-radius: 100%; box-shadow: 0 2rpx 4rpx rgba(0,0,0,.22); }.run-label { display: block; width: 120rpx; margin-top: 5rpx; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: #f7ffff; font-size: 18rpx; text-shadow: 0 1rpx 4rpx #16434a; }.lift-marker { position: absolute; z-index: 6; display: flex; align-items: center; padding: 5rpx 9rpx; border-radius: 8rpx; color: #244b5b; background: rgba(240,249,248,.9); font-size: 17rpx; box-shadow: 0 2rpx 7rpx rgba(10,50,60,.18); }.lift-icon { margin-right: 5rpx; color: #c67b2b; font-size: 26rpx; font-weight: 700; }.resort-marker { position: absolute; z-index: 7; bottom: 48rpx; left: 50%; transform: translateX(-50%); display: flex; align-items: center; padding: 9rpx 18rpx; border: 2rpx solid #d9b66d; border-radius: 999rpx; background: rgba(255,250,232,.94); color: #725a2f; font-size: 20rpx; }.resort-pin { margin-right: 7rpx; color: #d49b35; }.tree-line { position: absolute; z-index: 2; color: rgba(16,65,65,.6); font-size: 33rpx; letter-spacing: 7rpx; }.tree-left { left: 40rpx; bottom: 180rpx; transform: rotate(-8deg); }.tree-right { right: 18rpx; bottom: 265rpx; transform: rotate(9deg); }.loading-mask { position: absolute; z-index: 10; inset: 0; display: flex; align-items: center; justify-content: center; color: #426977; font-size: 23rpx; background: rgba(232,244,244,.82); }
	.filters { display: flex; flex-wrap: wrap; gap: 10rpx; padding: 4rpx 22rpx 12rpx; }.filter-chip { display: flex; align-items: center; padding: 9rpx 18rpx; border: 1rpx solid #dbe6ec; border-radius: 999rpx; background: #fff; color: #5b7180; font-size: 21rpx; }.filter-chip.active { border-color: #24738b; background: #e9f4f6; color: #17596c; font-weight: 600; }.legend-dot { width: 13rpx; height: 13rpx; margin-right: 7rpx; border-radius: 50%; }
	.orientation { padding: 12rpx 25rpx 0; color: #7d919c; font-size: 19rpx; line-height: 28rpx; }
	.terrain-3d { position: relative; width: 100%; height: 860rpx; overflow: hidden; touch-action: none; }.terrain-3d canvas { display: block; }.terrain-label { position: absolute; z-index: 3; max-width: 96px; padding: 3px 7px; border: 1px solid var(--marker-color, #89a4ae); border-radius: 7px; background: rgba(255,255,255,.9); color: #22485a; font-size: 10px; line-height: 14px; cursor: pointer; transform: translate(-50%, -50%); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; box-shadow: 0 1px 4px rgba(12,48,60,.16); }.terrain-label.is-lift { border-style: dashed; color: #8a6420; }.terrain-landmark { position: absolute; z-index: 1; padding: 2px 5px; border-radius: 5px; background: rgba(24,58,70,.62); color: #eaf6f8; font-size: 9px; transform: translate(-50%, -50%); white-space: nowrap; }.terrain-landmark.summit { background: rgba(255,255,255,.82); color: #17596c; }.terrain-landmark.gate { background: rgba(23,89,108,.72); }.terrain-summit { position: absolute; z-index: 2; padding: 3px 8px; border-radius: 999px; background: rgba(255,255,255,.9); color: #17596c; font-size: 10px; font-weight: 600; transform: translate(-50%, -50%); white-space: nowrap; }.terrain-controls { display: flex; justify-content: space-between; gap: 15rpx; padding: 12rpx 24rpx; font-size: 20rpx; color: #517786; }
	.entity-list { padding: 0 24rpx 24rpx; }.entity-grid { display: flex; flex-wrap: wrap; gap: 12rpx; }.entity-chip { display: flex; align-items: center; padding: 12rpx 16rpx; border-radius: 12rpx; background: #edf4f6; color: #365568; font-size: 23rpx; }
	.terrain-legend { display: flex; flex-wrap: wrap; padding: 18rpx 24rpx 12rpx; gap: 12rpx 23rpx; }.legend-item { display: flex; align-items: center; color: #617581; font-size: 20rpx; }.legend-line { width: 40rpx; height: 7rpx; margin-right: 7rpx; border-radius: 8rpx; }.legend-line.beginner { background: #12a150; }.legend-line.intermediate { background: #1f6feb; }.legend-line.advanced { background: #dc2626; }.legend-lift { margin-right: 7rpx; color: #c67b2b; font-size: 27rpx; }.notice { margin: 0 22rpx 18rpx; padding: 12rpx 15rpx; border-radius: 12rpx; background: #fff5e7; color: #b86e18; font-size: 19rpx; line-height: 29rpx; }
	.mask { position: fixed; inset: 0; z-index: 10000; display: flex; align-items: flex-end; background: rgba(15,42,58,.45); }.sheet { width: 100%; max-height: 78%; box-sizing: border-box; padding: 28rpx 28rpx 10rpx; border-radius: 24rpx 24rpx 0 0; background: #fff; }.sheet-head { display: flex; align-items: center; justify-content: space-between; }.sheet-title { flex: 1; color: #1c2b3a; font-size: 34rpx; font-weight: bold; }.sheet-sub { display: block; margin: 8rpx 0 14rpx; color: #7a8aa0; font-size: 22rpx; }.sheet-body { max-height: 60vh; }.sheet-close { padding: 22rpx 0; color: #1f6f83; font-size: 28rpx; text-align: center; }.badge { padding: 6rpx 14rpx; border-radius: 999rpx; background: #eef1f6; color: #64748b; font-size: 20rpx; }.badge.small { margin-right: 12rpx; padding: 2rpx 12rpx; font-size: 20rpx; }.badge.verified { background: #e6f7ee; color: #12a150; }.badge.corroborated { background: #e8f0fe; color: #1f6feb; }.badge.transcribed { background: #fff4e5; color: #d97706; }.badge.sample { background: #fdeaea; color: #dc2626; }.badge.out { background: #e8f0fe; color: #1f6feb; }.badge.in { background: #eef1f6; color: #64748b; }.row { display: flex; justify-content: space-between; padding: 8rpx 0; border-bottom: 1rpx solid #f4f7fb; }.row-label { flex-shrink: 0; margin-right: 20rpx; color: #7a8aa0; font-size: 23rpx; }.row-value { flex: 1; color: #1c2b3a; font-size: 23rpx; line-height: 34rpx; text-align: right; }.note { margin: 16rpx 0; padding: 16rpx 18rpx; border-radius: 12rpx; background: #f7f9fc; }.note-title { display: block; margin-bottom: 10rpx; color: #1f6f83; font-size: 23rpx; }.section-title { display: block; margin: 22rpx 0 12rpx; color: #1c2b3a; font-size: 27rpx; font-weight: bold; }.line { display: flex; align-items: center; padding: 10rpx 0; border-bottom: 1rpx solid #f0f3f8; }.line-text { flex: 1; color: #1c2b3a; font-size: 24rpx; }
	.entity-search { display: flex; align-items: center; gap: 16rpx; margin: 0 22rpx 16rpx; padding: 14rpx 20rpx; background: #fff; border: 1rpx solid #dbe6ec; border-radius: 16rpx; color: #24738b; font-size: 23rpx; }.entity-search input { flex: 1; min-width: 0; font-size: 24rpx; }.entity-chip.selected { background: #d9edf1; box-shadow: inset 0 0 0 2rpx #24738b; font-weight: 600; }.run-marker.selected .run-label { color: #fff3b0; font-weight: 700; }.lift-marker.selected { outline: 3rpx solid #24738b; }.empty-result { display: block; padding: 16rpx 0; color: #718493; font-size: 23rpx; }.relation-link { cursor: pointer; }.relation-arrow { padding-left: 14rpx; color: #24738b; font-size: 32rpx; }
	.terrain-label { box-sizing: border-box; min-height: 32px; padding: 7px 9px; font-size: 11px; line-height: 16px; }.terrain-label.is-selected { background: #fff3b0; outline: 2px solid #24738b; z-index: 5; font-weight: 700; }.terrain-label:focus-visible { outline: 3px solid #24738b; }
	@media (pointer: coarse) { .terrain-label { min-height: 44px; padding: 13px 9px; } }
</style>
