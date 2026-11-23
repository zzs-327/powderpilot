<template>
  <view class="media-panel">
    <text class="media-heading">{{ heading || '雪场媒体 · 图片 / 雪道图 / 精选视频' }}</text>
    <text v-if="compact" class="media-note">未观看的只提供链接，不会说成已验证推荐。</text>
    <text v-if="loading" class="media-note">媒体加载中…</text>
    <text v-else-if="error" class="media-note">{{ error }}</text>
    <template v-else>
      <text v-if="!normalized.length" class="media-note">暂无已收录媒体资源，不使用占位图代替实景。</text>
      <view v-for="group in groups" :key="group.key">
        <text v-if="group.items.length" class="media-group">{{ group.label }}</text>
        <view v-for="resource in group.items" :key="resource.url" class="media-item">
          <image v-if="!compact && authorizedImage(resource) && !broken[resource.image_url]" class="media-image" :src="resource.image_url" mode="aspectFit" @error="broken[resource.image_url] = true" @click="preview(resource)" />
          <text v-if="broken[resource.image_url]" class="media-note">授权图片加载失败，请查看原页。</text>
          <button size="mini" class="media-link" @click="openMediaPage(resource)">{{ resource.title }} · 查看原页 ↗</button>
          <text class="media-note">{{ verificationLabel(resource) }}</text>
          <text class="media-note">来源：{{ resource.source || '未注明' }} · 发布：{{ resource.published_at || '未注明' }} · 核验：{{ resource.checked_at || '未注明' }}</text>
          <text class="media-note">{{ resource.rights === 'authorized' ? '已授权（仅安全授权图片可预览）' : '仅链接 · 无转载授权' }}</text>
          <text v-if="resource.credit" class="media-note">署名：{{ resource.credit }}</text>
          <template v-if="resource.kind === 'video' && resource.verification === 'content_checked'">
            <text v-if="resource.author" class="media-note">作者：{{ resource.author }}</text>
            <text v-if="resource.board || resource.level || resource.topic || resource.duration" class="media-note">{{ [resource.board, resource.level, resource.topic, resource.duration].filter(Boolean).join(' · ') }}</text>
            <text v-if="resource.recommendation" class="media-note">推荐理由：{{ resource.recommendation }}</text>
          </template>
          <text v-if="resource.notice" class="media-note">{{ resource.notice }}</text>
        </view>
      </view>
    </template>
  </view>
</template>
<script setup>
import { computed, ref, watch } from 'vue'
import { normalizeMedia, authorizedImage, verificationLabel } from '@/utils/media.js'
import { openMediaPage } from '@/utils/media-actions.js'
const props = defineProps({ resources: { type: Array, default: () => [] }, loading: Boolean, error: { type: String, default: '' }, compact: Boolean, heading: { type: String, default: '' } })
const broken = ref({})
watch(() => props.resources, () => { broken.value = {} })
const normalized = computed(() => normalizeMedia(props.resources))
const labels = { cover: '雪场概览', trail_map: '雪道图', terrain: '地形', facility: '设施', stay: '住宿', other: '其他图片' }
const groups = computed(() => {
  const allImages = normalized.value.filter(r => r.kind !== 'video')
  const images = props.compact ? allImages.slice(0, 2) : allImages
  return [...Object.entries(labels).map(([key, label]) => ({ key, label, items: images.filter(r => (r.category || (r.kind === 'map' ? 'trail_map' : 'other')) === key) })), { key: 'video', label: '视频原页（最多 3 条，非已核验教学推荐）', items: normalized.value.filter(r => r.kind === 'video').slice(0, 3) }]
})
const preview = resource => {
  const url = authorizedImage(resource)
  if (url && !broken.value[url]) uni.previewImage({ current: url, urls: [url] })
}
</script>
<style scoped>
.media-panel { margin: 18rpx 0; padding: 18rpx; background: #f7f9fc; border-radius: 12rpx; }
.media-heading,.media-group { display: block; font-size: 25rpx; color: #225268; font-weight: 600; margin-bottom: 12rpx; }
.media-group { margin-top: 16rpx; font-size: 23rpx; }
.media-item { padding: 12rpx 0; border-bottom: 1rpx solid #e6ecf5; }
.media-note { display: block; color: #718493; font-size: 21rpx; line-height: 32rpx; }
.media-link { margin: 6rpx 0; text-align: left; color: #24738b; }
.media-image { width: 100%; height: 220rpx; }
</style>
