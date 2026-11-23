<template>
	<view class="page">
		<!-- 没有行程卡时的引导 -->
		<view v-if="!card" class="empty">
			<text class="empty-title">还没有行程卡</text>
			<text class="empty-text">去「对话」页说一句：</text>
			<view class="example" @click="goChat">
				"我想下周末去万龙，两个人，人均预算 2000，玩两天，我玩单板"
			</view>
			<text class="empty-text">说清日期档位（周末/平日）、天数、人数、人均预算、单双板后，会生成结构化行程卡；预算为已知单价小计，不是全行程总价。</text>
		</view>

		<scroll-view v-else class="body" scroll-y>
			<!-- 头部 -->
			<view class="hero">
				<text class="hero-resort">{{ card.resort }}</text>
				<text class="hero-summary">{{ card.summary }}</text>
			</view>

			<!-- 日程 -->
			<view class="section" v-if="card.itinerary && card.itinerary.length">
				<view class="section-title">🗓 日程</view>
				<view v-for="d in card.itinerary" :key="d.day" class="line">
					<text class="tag">D{{ d.day }}</text>
					<text class="line-text">{{ d.plan }}</text>
				</view>
			</view>

      <view class="section">
        <MediaPanel :resources="media" :loading="mediaLoading" :error="mediaError" compact />
        <text class="muted">历史实拍来源记录（不是图片预览）</text>
        <text v-if="photosLoading" class="photo-meta-line">来源记录加载中…</text>
        <text v-else-if="photosError" class="photo-meta-line">{{ photosError }}</text>
        <text v-else-if="!photos.length" class="photo-meta-line">暂无历史实拍来源记录。</text>
        <view v-for="(p, i) in photos" :key="i" class="photo-meta">
          <text class="photo-meta-line">{{ p.location || '地点未注明' }} · 拍摄：{{ p.shot_at || '日期未知' }} · 署名：{{ p.credit || '未注明' }}</text>
          <text v-if="p.source || p.source_type" class="photo-meta-line">来源：{{ p.source || '未注明' }} {{ p.source_type || '' }}</text>
          <text class="photo-meta-line">{{ p.url ? '保留历史链接记录，未作为授权图片展示' : '未提供图片链接，仅保留来源元数据' }}</text>
        </view>
      </view>

			<!-- 预算 -->
			<view class="section" v-if="card.budget">
				<view class="section-title">💰 预算</view>
				<view v-for="(item, i) in card.budget.items" :key="i" class="budget-item">
					<view class="line">
						<text class="line-text">{{ item.item }}{{ item.counted === false ? (String(item.note || '').includes('多人共用') ? '（多人共用，未计入小计）' : '（参考单价，未计入小计）') : '' }}</text>
						<text class="amount">{{ formatMoney(Number(item.amount), item.currency || budget.currency) }}</text>
					</view>
					<text v-if="item.note" class="muted">{{ item.note }}</text>
				</view>
				<view v-for="s in budget.subtotals" :key="s.currency" class="line total">
					<text class="line-text">已知费用小计{{ budget.subtotals.length > 1 ? '（' + s.currency + '）' : '' }}（非完整预算）</text>
          <text class="amount">{{ formatMoney(s.total, s.currency) }}</text>
				</view>
				<view v-if="!budget.subtotals.length" class="line total">
					<text class="line-text">{{ budget.uncountedOnly ? '以上金额未计入小计（不能相加）' : '已知费用小计' }}</text>
					<text class="amount">{{ budget.uncountedOnly ? '未加总' : '待确认' }}</text>
				</view>
        <text v-if="budget.subtotals.length > 1" class="muted">不同币种分别列示，未换算、未相加。</text>
				<view v-if="budget.missing.length" class="missing">
					<text class="missing-title">缺失费用（待确认，不能视作 0）</text>
					<text v-for="m in budget.missing" :key="m" class="muted">· {{ m }}</text>
				</view>
        <text class="muted">预算仍需确认住宿、交通等实际费用，零金额项目不视为免费。</text>
			</view>

			<!-- 装备清单 -->
			<view class="section" v-if="card.gear_checklist && card.gear_checklist.length">
				<view class="section-title">🎒 装备清单</view>
				<view v-for="(g, i) in card.gear_checklist" :key="i" class="bullet">
					<text class="bullet-dot">·</text><text class="line-text">{{ g }}</text>
				</view>
			</view>

			<!-- 避坑 -->
			<view class="section" v-if="card.tips && card.tips.length">
				<view class="section-title">⚠️ 避坑提示</view>
				<view v-for="(t, i) in card.tips" :key="i" class="bullet">
					<text class="badge" :class="'badge-' + (t.source_type || 'C')">{{ t.source_type || 'C' }}</text>
					<text class="line-text">{{ t.text }}</text>
					<text v-if="t.source" class="muted">{{ t.source }}</text>
				</view>
			</view>

			<!-- 教学视频 -->
			<view class="section" v-if="card.teaching && card.teaching.length">
				<view class="section-title">🎬 教学视频</view>
				<view v-for="(v, i) in card.teaching" :key="i" class="line">
					<text class="line-text">{{ v.title }}<text class="muted">（{{ v.level }} {{ v.board }}）</text></text>
				</view>
			</view>

			<!-- 来源（溯源） -->
			<view class="section" v-if="card.sources && card.sources.length">
				<view class="section-title">🔍 信息来源</view>
				<view v-for="(s, i) in card.sources" :key="i" class="bullet">
					<text class="badge" :class="'badge-' + (s.source_type || 'C')">{{ s.source_type || 'C' }}</text>
					<text class="line-text">[{{ s.index }}] {{ s.source }}<text class="muted"> · {{ s.date }}</text></text>
				</view>
			</view>

			<!-- 时效与风险 -->
			<view class="section" v-if="card.caveats && card.caveats.length">
				<view class="section-title">⏳ 时效与风险</view>
				<view v-for="(c, i) in card.caveats" :key="i" class="bullet">
					<text class="bullet-dot">·</text><text class="line-text">{{ c }}</text>
				</view>
			</view>

			<view class="footer">
				<!-- 行程卡不是终点：用户看到方案后最自然的下一步是"改一下"
				     （换日期、加人、调预算），所以给一条明确的回路，
				     而不是让他自己想起来还要切回对话页再打一遍。 -->
				<button class="primary" @click="goChat">去对话页说"改成…"，继续完善</button>
				<button class="ghost" @click="clearCard">清空当前行程卡</button>
			</view>
			<view class="bottom-pad"></view>
		</scroll-view>
	</view>
</template>

<script setup>
	import { ref, computed } from 'vue'
	import { onShow } from '@dcloudio/uni-app'
	import MediaPanel from '@/components/MediaPanel.vue'
  import { createRequestGuard, budgetSummary, formatMoney } from '@/utils/media.js'
	import { listResorts, resortPhotos, resortMedia } from '@/api/resort.js'

	const card = ref(null)
	const photos = ref([])
  const media = ref([])
  const mediaLoading = ref(false)
  const mediaError = ref('')
  const photosLoading = ref(false)
  const photosError = ref('')
  const guard = createRequestGuard()
  const budget = computed(() => budgetSummary(card.value?.budget))

	const goChat = () => uni.switchTab({ url: '/pages/index/index' })

  const loadPhotos = async () => {
    const id = guard.next()
    photos.value = []; media.value = []; photosError.value = ''; mediaError.value = ''
    photosLoading.value = false; mediaLoading.value = false
    if (!card.value?.resort) return
    const name = card.value.resort
    photosLoading.value = true; mediaLoading.value = true
    let match
    try {
      const resorts = await listResorts()
      if (!guard.current(id)) return
      match = (resorts || []).find(r => r.name === name || r.resort_key === name)
      if (!match) {
        photosLoading.value = false; mediaLoading.value = false
        photosError.value = '无法明确匹配雪场，未加载来源记录'
        mediaError.value = '无法明确匹配雪场，未加载媒体'
        return
      }
    } catch (_) {
      if (guard.current(id)) {
        photosError.value = '雪场查询失败，请重新进入行程页重试'
        mediaError.value = photosError.value
        photosLoading.value = false; mediaLoading.value = false
      }
      return
    }
    const fetchPart = async (request, target, loading, error) => {
      try { const data = await request(); if (guard.current(id)) target.value = data || [] }
      catch (_) { if (guard.current(id)) error.value = '读取失败，请重新进入行程页重试' }
      finally { if (guard.current(id)) loading.value = false }
    }
    fetchPart(() => resortPhotos(match.resort_key), photos, photosLoading, photosError)
    fetchPart(() => resortMedia(match.resort_key), media, mediaLoading, mediaError)
  }
  const clearCard = () => {
    guard.next()
    uni.removeStorageSync('lastTripCard')
    card.value = null; photos.value = []; media.value = []
    photosLoading.value = false; mediaLoading.value = false
    photosError.value = ''; mediaError.value = ''
  }

	onShow(() => {
		const token = uni.getStorageSync('token')
		if (!token) {
			uni.reLaunch({ url: '/pages/login/login' })
			return
		}
		card.value = uni.getStorageSync('lastTripCard') || null
		loadPhotos()
	})
</script>

<style>
	/* 与对话页同一个坑：H5 带 tabBar 时 height:100% 会连 tabBar 的 50px 一起算进去，
	   底部内容被压在 tabBar 底下。用 uni-app 注入的变量把导航栏/tabBar 让出来；
	   非 H5 端这两变量不存在，回退 0px，等价于 100vh。 */
	.page { height: calc(100vh - var(--window-top, 0px) - var(--window-bottom, 0px)); box-sizing: border-box; background-color: #f4f7fb; }
	.body { height: 100%; }
	.empty { display: flex; flex-direction: column; align-items: center; padding: 140rpx 60rpx; }
	.empty-title { font-size: 34rpx; color: #1c2b3a; font-weight: bold; margin-bottom: 20rpx; }
	.empty-text { font-size: 26rpx; color: #7a8aa0; margin-bottom: 12rpx; }
	.example { background-color: #e8f0fe; color: #1f6feb; font-size: 26rpx; padding: 24rpx; border-radius: 16rpx; margin: 16rpx 0 24rpx; line-height: 40rpx; }
	.hero { background-color: #0f2a4a; padding: 36rpx 32rpx; }
	.hero-resort { display: block; color: #ffffff; font-size: 40rpx; font-weight: bold; }
	.hero-summary { display: block; color: #cfe3ff; font-size: 26rpx; margin-top: 14rpx; line-height: 40rpx; }
	.section { background-color: #ffffff; margin: 20rpx 24rpx; padding: 24rpx; border-radius: 18rpx; }
	.section-title { font-size: 28rpx; font-weight: bold; color: #1c2b3a; margin-bottom: 18rpx; }
	.photos { white-space: nowrap; }
	.photo { display: inline-block; width: 300rpx; margin-right: 18rpx; vertical-align: top; }
	.photo-img { width: 300rpx; height: 200rpx; border-radius: 12rpx; background-color: #eef2f8; }
	.photo-placeholder { width: 300rpx; height: 200rpx; border-radius: 12rpx; background-color: #dbe7f7; display: flex; align-items: center; justify-content: center; }
	.photo-placeholder-text { color: #4a7ab5; font-size: 26rpx; }
	.photo-meta { margin-top: 10rpx; }
	.photo-meta-line { display: block; font-size: 22rpx; color: #7a8aa0; }
	.line { display: flex; align-items: flex-start; justify-content: space-between; margin-bottom: 14rpx; }
	.line.total { border-top: 1rpx dashed #e6ecf5; padding-top: 14rpx; margin-top: 6rpx; }
	.line-text { font-size: 26rpx; color: #33465c; line-height: 40rpx; flex: 1; }
	.amount { font-size: 26rpx; color: #1f6feb; font-weight: bold; margin-left: 16rpx; }
	.bullet { display: flex; align-items: flex-start; margin-bottom: 12rpx; }
	.bullet-dot { color: #1f6feb; font-size: 28rpx; margin-right: 10rpx; }
	.badge { width: 36rpx; height: 36rpx; line-height: 36rpx; text-align: center; border-radius: 8rpx; font-size: 22rpx; color: #ffffff; margin-right: 12rpx; flex-shrink: 0; }
	.badge-A { background-color: #1f6feb; }
	.badge-B { background-color: #2f9e44; }
	.badge-C { background-color: #e8930c; }
	.muted { font-size: 24rpx; color: #9aa8bb; }
	.budget-item { display: flex; flex-direction: column; margin-bottom: 10rpx; }
	.budget-item .line { margin-bottom: 4rpx; }
	.missing { display: flex; flex-direction: column; margin: 12rpx 0; padding: 14rpx; background-color: #fff7e6; border-radius: 10rpx; }
	.missing-title { font-size: 24rpx; color: #b26a00; margin-bottom: 6rpx; }
	.footer { padding: 10rpx 24rpx 0; }
	.primary { background-color: #1f6feb; color: #ffffff; font-size: 26rpx; border-radius: 12rpx; margin-bottom: 16rpx; }
	.ghost { background-color: transparent; color: #7a8aa0; font-size: 26rpx; border: 1rpx solid #dbe3ee; border-radius: 12rpx; }
	.bottom-pad { height: 40rpx; }
</style>
