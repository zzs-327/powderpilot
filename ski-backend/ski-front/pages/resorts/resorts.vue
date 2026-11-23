<template>
	<view class="page">
		<view class="hero">
			<text class="hero-title">雪场库</text>
			<!-- 没加载完就显示"0 座雪场"会让人以为库是空的，先给个加载态 -->
			<text class="hero-sub">{{ loaded ? resorts.length + ' 座雪场' : '正在加载…' }} · 每座都标明"这批数据是几手的"</text>
		</view>

		<scroll-view class="body" scroll-y>
      <text v-if="!loaded" class="muted">雪场列表加载中…</text>
      <text v-else-if="listError" class="muted">{{ listError }}</text>
      <text v-else-if="!resorts.length" class="muted">暂无已收录雪场。</text>
			<view v-for="item in resorts" :key="item.resort_key" class="card" @click="open(item)">
				<view class="card-head">
					<text class="name">{{ item.name }}</text>
					<text class="badge" :class="item.data_status">{{ statusText(item.data_status) }}</text>
				</view>
				<text class="meta">{{ item.region }} · 雪道明细 {{ item.runs_total }} 条<template
						v-if="item.price_count"> · 价格 {{ item.price_count }} 条</template></text>
				<text v-if="item.official_total && item.official_total !== item.runs_total" class="conflict">
					⚠️ 官网口径 {{ item.official_total }} 条，与库内明细 {{ item.runs_total }} 条不一致——本库两个都留着，不替它取舍
				</text>
			</view>
			<view class="foot">
				<text class="foot-text">数据档位：✅ 官方直采 ｜ 🔁 多源印证 ｜ ⚠️ 二手转引 ｜ 🚫 示例数据</text>
			</view>
			<view class="bottom-pad"></view>
		</scroll-view>

		<!-- 详情弹层：点开看雪道/价格与来源说明 -->
		<view v-if="detail" class="mask" @click="close">
			<view class="sheet" @click.stop>
				<view class="sheet-head">
					<text class="sheet-title">{{ detail.name }}</text>
					<text class="badge" :class="detail.data_status">{{ statusText(detail.data_status) }}</text>
				</view>
				<scroll-view class="sheet-body" scroll-y>
					<view v-if="detail.official_total" class="row">
						<text class="row-label">官网/来源口径</text>
						<text class="row-value">{{ detail.official_total }} 条 · 库内明细 {{ detail.runs_total }} 条</text>
					</view>
					<view v-if="detail.data_status_note" class="note">
						<text class="note-title">数据来源说明</text>
						<text class="note-text">{{ detail.data_status_note }}</text>
					</view>

					<text v-if="tipsLoading" class="muted">注意事项加载中…</text><text v-else-if="tipsError" class="muted">{{ tipsError }}</text>
					<text v-if="tips.length" class="section-title">已知缺口与注意事项（{{ tips.length }} 条）</text>
					<view v-for="(tip, i) in tips" :key="'t' + i" class="line">
						<text class="line-text">{{ tip.text }}</text>
						<text v-if="tip.source" class="line-sub">{{ tip.source }}{{ tip.date ? ' · ' + tip.date : '' }}</text>
					</view>

					<text v-if="runsLoading" class="muted">雪道加载中…</text><text v-else-if="runsError" class="muted">{{ runsError }}</text>
					<text class="section-title">雪道（{{ runs.length }} 条明细）</text>
					<view v-for="(run, i) in runs" :key="i" class="line">
						<text class="badge small" :class="levelClass(run.difficulty)">{{ run.difficulty }}</text>
						<text class="line-text">{{ run.name }}<template v-if="run.length_m"> · {{ run.length_m }} 米</template><template
								v-if="run.avg_slope_deg"> · 均坡 {{ run.avg_slope_deg }}°</template></text>
					</view>
					<text v-if="!runsLoading && !runsError && !runs.length" class="muted">该雪场暂无逐条雪道明细</text>

					<text v-if="pricesLoading" class="muted">价格加载中…</text><text v-else-if="pricesError" class="muted">{{ pricesError }}</text>
					<text class="section-title">价格（{{ prices.length }} 条）</text>
					<text v-if="priceListNotice(prices)" class="note-text">{{ priceListNotice(prices) }}</text>
					<view v-for="(p, i) in prices" :key="'p' + i" class="line">
						<text v-if="p.data_status" class="badge small" :class="p.data_status">{{ statusText(p.data_status) }}</text>
						<text class="line-text">{{ p.item }}：{{ formatMoney(Number(p.price), p.currency) }}/{{ p.unit }}</text>
						<text v-if="priceTerms(p).length" class="line-sub">{{ priceTerms(p).join(' · ') }}</text>
						<text v-for="(condition, j) in priceConditions(p)" :key="'c' + i + '-' + j" class="line-sub">{{ condition }}</text>
						<text class="line-sub">录入 {{ p.updated_at }}{{ p.expire_at ? ' · 保鲜期至 ' + p.expire_at : '' }}</text>
						<text v-if="safeMediaUrl(p.source_url)" class="line-sub link" @click="openPriceSource(p)">官网原文</text>
					</view>
					<text v-if="!pricesLoading && !pricesError && !prices.length" class="muted">该雪场暂无价格数据（本库宁缺勿造）</text>

					<MediaPanel :resources="media" :loading="mediaLoading" :error="mediaError" />
				</scroll-view>
				<view class="sheet-close" @click="close"><text>关闭</text></view>
			</view>
		</view>
	</view>
</template>

<script setup>
	import { ref } from 'vue'
	import { onShow } from '@dcloudio/uni-app'
	import MediaPanel from '@/components/MediaPanel.vue'
	import { createRequestGuard, formatMoney, priceConditions, priceListNotice, priceTerms, safeMediaUrl } from '@/utils/media.js'
	import { openMediaPage } from '@/utils/media-actions.js'
	import { listResorts, resortRuns, resortPrices, resortMedia, resortTips } from '@/api/resort.js'

	const resorts = ref([])
	const detail = ref(null)
	const runs = ref([])
	const prices = ref([])
	const tips = ref([])
	const media = ref([])
  const mediaLoading = ref(false)
  const mediaError = ref('')
  const runsLoading = ref(false)
  const pricesLoading = ref(false)
  const runsError = ref('')
  const pricesError = ref('')
  const tipsLoading = ref(false)
  const tipsError = ref('')
  const guard = createRequestGuard()
	const listError = ref('')
	const loaded = ref(false)      // 列表是否已加载完（用于区分"还没加载"和"真的一座都没有"）

	// 档位 → 中文标签（与后端 rag/parser.py 的 DATA_STATUS_LABEL 对应）
	const LABELS = {
		verified: '✅ 官方直采',
		corroborated: '🔁 多源印证',
		transcribed: '⚠️ 二手转引',
		sample: '🚫 示例数据'
	}
	const statusText = (s) => LABELS[s] || '未标注'

	// 难度 → 徽章配色（只看关键词，避免依赖具体写法）
	const levelClass = (difficulty) => {
		const text = String(difficulty || '')
		if (text.includes('高')) return 'level-high'
		if (text.includes('中')) return 'level-mid'
		if (text.includes('初')) return 'level-low'
		return 'level-other'
	}

	const openPriceSource = (row) => openMediaPage({ url: row?.source_url })

	const load = async () => {
    listError.value = ''
    loaded.value = false
		try {
			resorts.value = await listResorts()
		} catch (e) {
			listError.value = '雪场列表读取失败，请重新进入页面重试'
		} finally {
			loaded.value = true
		}
	}

  const open = (item) => {
    const id = guard.next()
    detail.value = item
    runs.value = []; prices.value = []; media.value = []; tips.value = []
    runsError.value = ''; pricesError.value = ''; mediaError.value = ''; tipsError.value = ''
    runsLoading.value = true; pricesLoading.value = true; mediaLoading.value = true; tipsLoading.value = true
    const fetchPart = async (request, target, loading, error, transform = data => data || []) => {
      try { const data = await request(); if (guard.current(id)) target.value = transform(data) }
      catch (_) { if (guard.current(id)) error.value = '读取失败，请关闭后重试' }
      finally { if (guard.current(id)) loading.value = false }
    }
    fetchPart(() => resortRuns(item.resort_key, ''), runs, runsLoading, runsError, data => data?.runs || [])
    fetchPart(() => resortPrices(item.resort_key, ''), prices, pricesLoading, pricesError)
    fetchPart(() => resortTips(item.resort_key), tips, tipsLoading, tipsError)
    fetchPart(() => resortMedia(item.resort_key), media, mediaLoading, mediaError)
  }
  const close = () => { guard.next(); detail.value = null; media.value = []; runs.value = []; prices.value = []; tips.value = [] }

	onShow(() => {
		const token = uni.getStorageSync('token')
		if (!token) {
			uni.reLaunch({ url: '/pages/login/login' })
			return
		}
		load()
	})
</script>

<style>
	/* 与对话页同一套高度处理：H5 带 tabBar 时 height:100% 会把 tabBar 高度也算进去 */
	.page {
		height: calc(100vh - var(--window-top, 0px) - var(--window-bottom, 0px));
		box-sizing: border-box; background-color: #f4f7fb;
	}
	.hero { background-color: #0f2a4a; padding: 30rpx 32rpx; flex-shrink: 0; }
	.hero-title { display: block; color: #ffffff; font-size: 38rpx; font-weight: bold; }
	.hero-sub { display: block; color: #9fc0e8; font-size: 24rpx; margin-top: 10rpx; }
	.body { height: calc(100% - 150rpx); }
	.card { background-color: #ffffff; margin: 20rpx 24rpx; padding: 24rpx; border-radius: 18rpx; box-shadow: 0 2rpx 8rpx rgba(15, 42, 74, 0.06); }
	.card-head { display: flex; align-items: center; justify-content: space-between; }
	.name { font-size: 32rpx; font-weight: bold; color: #1c2b3a; }
	.badge { font-size: 22rpx; padding: 6rpx 16rpx; border-radius: 999rpx; }
	.badge.small { font-size: 20rpx; padding: 2rpx 12rpx; margin-right: 12rpx; }
	.badge.verified { background-color: #e6f7ee; color: #12a150; }
	.badge.corroborated { background-color: #e8f0fe; color: #1f6feb; }
	.badge.transcribed { background-color: #fff4e5; color: #d97706; }
	.badge.sample { background-color: #fdeaea; color: #dc2626; }
	.level-high { background-color: #fdeaea; color: #dc2626; }
	.level-mid { background-color: #e8f0fe; color: #1f6feb; }
	.level-low { background-color: #e6f7ee; color: #12a150; }
	.level-other { background-color: #eef1f6; color: #64748b; }
	.meta { display: block; font-size: 24rpx; color: #7a8aa0; margin-top: 12rpx; }
	.conflict { display: block; font-size: 22rpx; color: #d97706; margin-top: 10rpx; line-height: 34rpx; }
	.foot { padding: 10rpx 32rpx 30rpx; }
	.foot-text { font-size: 22rpx; color: #9aa8bb; line-height: 34rpx; }
	.bottom-pad { height: 40rpx; }

	.mask { position: fixed; left: 0; right: 0; top: 0; bottom: 0; background-color: rgba(15, 42, 74, 0.45); display: flex; align-items: flex-end; z-index: 99; }
	.sheet { width: 100%; max-height: 78%; background-color: #ffffff; border-radius: 24rpx 24rpx 0 0; padding: 28rpx 28rpx 10rpx; box-sizing: border-box; }
	.sheet-head { display: flex; align-items: center; justify-content: space-between; margin-bottom: 18rpx; }
	.sheet-title { font-size: 34rpx; font-weight: bold; color: #1c2b3a; }
	.sheet-body { max-height: 62vh; }
	.sheet-close { text-align: center; color: #1f6feb; font-size: 28rpx; padding: 22rpx 0; }
	.row { display: flex; justify-content: space-between; padding: 8rpx 0; }
	.row-label { font-size: 24rpx; color: #7a8aa0; }
	.row-value { font-size: 24rpx; color: #1c2b3a; }
	.note { background-color: #f7f9fc; border-radius: 12rpx; padding: 18rpx; margin: 14rpx 0; }
	.note-title { display: block; font-size: 24rpx; color: #1f6feb; margin-bottom: 8rpx; }
	.note-text { font-size: 22rpx; color: #4a5b70; line-height: 34rpx; }
	.section-title { display: block; font-size: 28rpx; font-weight: bold; color: #1c2b3a; margin: 22rpx 0 12rpx; }
	.line { display: flex; align-items: center; padding: 10rpx 0; border-bottom: 1rpx solid #f0f3f8; flex-wrap: wrap; }
	.line-text { font-size: 25rpx; color: #1c2b3a; flex: 1; }
	.line-sub { font-size: 21rpx; color: #9aa8bb; width: 100%; margin-top: 4rpx; }
	.link { color: #1f6feb; }
	.muted { font-size: 24rpx; color: #9aa8bb; padding: 10rpx 0; }
</style>
