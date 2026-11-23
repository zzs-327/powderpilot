import { safeMediaUrl } from './media.js'
export function openMediaPage(resource) {
  const url = safeMediaUrl(resource?.url)
  if (!url) { uni.showToast({ title: '暂无安全原页链接', icon: 'none' }); return }
  // #ifdef H5
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.target = '_blank'
  anchor.rel = 'noopener noreferrer'
  anchor.referrerPolicy = 'no-referrer'
  anchor.click()
  return
  // #endif
  // #ifndef H5
  uni.setClipboardData({ data: url })
  // #endif
}
