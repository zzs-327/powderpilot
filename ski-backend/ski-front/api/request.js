// 后端地址对外导出：流式对话（api/chat.js）要自己拼 URL 走 fetch，
// 不能再写一份 'http://localhost:8000'——两个地方各写一份，改端口时必漏一个。
//
// 两种环境（2026-10-04 加）：
//   开发（HBuilderX「运行到浏览器」）：直连本机 8000
//   生产（HBuilderX「发行 → 网站-H5手机版」）：**空字符串 = 同源相对路径**，
//     由 Nginx 把 /api/v1/ 反代到后端（见 deploy/nginx.conf）。
//     为什么生产不用写死服务器 IP：换域名/换端口时不用重新构建前端，也不会跨域。
const isDev = process.env.NODE_ENV !== 'production'
export const BASE_URL = isDev ? 'http://localhost:8000' : ''

export function getToken() {
  try {
    return uni.getStorageSync('token') || ''
  } catch (e) {
    return ''
  }
}

/**
 * 登录态失效的统一处理
 *
 * 为什么要有：token 有效期 24 小时，过期后**每个接口都会返回 401**。
 * 原来只弹一个 toast（"token 已失效"），页面却停在原地——用户点什么都失败，
 * 又不知道该干什么。这里直接清掉本地 token 并送回登录页，重登一次就能继续。
 * 加 redirecting 是为了防抖：进页面时往往并发好几个请求，
 * 不拦一下会连续跳转好几次（现象是登录页闪一下又闪一下）。
 */
let redirecting = false
function handleUnauthorized() {
  if (redirecting) return
  redirecting = true
  uni.removeStorageSync('token')
  uni.showToast({ title: '登录已过期，请重新登录', icon: 'none' })
  setTimeout(() => {
    redirecting = false
    uni.reLaunch({ url: '/pages/login/login' })
  }, 800)
}

export function request(config) {
  return new Promise((resolve, reject) => {
    uni.request({
      url: BASE_URL + config.url,
      method: config.method || 'GET',
      data: config.data,
      // ⚠️ 必须显式设置超时。uni.request 默认只有 60 秒，而对话接口可能更久：
      //    Agent 要调工具（本地 SQL + 百度地图 MCP），如果回答没过引用校验
      //    还会打回重写一次、再调一遍模型。实测最慢能到 90 秒以上，
      //    默认超时会让用户看到"请求失败"，但其实后端还在正常干活。
      //    注意：对话页走的是流式（api/chat.js 的 chatStream），不受这里影响。
      timeout: config.timeout || 180000,
      header: {
        'Content-Type': 'application/json',
		// 将jwt放到Authorization请求头中
        'Authorization': 'Bearer ' + getToken(),
        // ...config.header
      },
      success(res) {
        const body = res.data || {}
        if (body.code === 200) {
          resolve(body.data)
          return
        }
        if (body.code === 401 || res.statusCode === 401) {
          handleUnauthorized()
          reject(body)
          return
        }
        uni.showToast({ title: body.msg || '请求失败', icon: 'none' })
        reject(body)
      },
      fail(err) {
        uni.showToast({ title: err.errMsg || '网络错误', icon: 'none' })
        reject(err)
      }
    })
  })
}
