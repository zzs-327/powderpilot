<template>
	<view class="page">
		<!-- 顶部：品牌 + 新对话 + 当前画像（点击去"我的"页修改） -->
		<view class="header">
			<text class="brand">🏔 PowderPilot</text>
			<view class="header-right">
				<view class="head-chip" @click="newChat">＋ 新对话</view>
				<view class="head-chip" @click="goProfile">{{ profileText }}</view>
			</view>
		</view>

		<!-- 行程要素：把"显式状态机"直接露在界面上——缺什么一眼看见，不用等模型追问 -->
		<view v-if="tripChips.length" class="trip-bar">
			<text class="trip-bar-label">行程要素</text>
			<text v-for="(t, i) in tripChips" :key="i" class="trip-chip">{{ t }}</text>
		</view>

		<!-- 消息列表 -->
		<scroll-view class="chat" scroll-y :scroll-into-view="scrollTarget"
			:scroll-with-animation="!streaming" @scroll="onScroll">
<view v-if="isWelcome" class="welcome-panel"><text class="welcome-kicker">滑雪情报官</text><text class="welcome-title">先看有档案的答案</text><text class="welcome-copy">雪道长度、落差和坡度来自档案，不靠模型印象。两个口径会分开写。视频只给已收录链接，没看过的不会当成推荐。</text><button v-for="task in CHAT_STARTERS" :key="task.id" class="task-card" :disabled="loading" @click="send(task.question)"><text class="task-title">{{ task.title }} ›</text><text class="task-detail">{{ task.detail }}</text></button><text class="welcome-trust">点进回答后可以打开来源编号，看是谁说的、哪一天、几手数据。</text></view>
			<view v-for="(m, i) in messages" :key="i" :id="'msg' + i" class="row" :class="m.role" v-show="!isWelcome">
				<view v-if="m.role === 'agent'" class="avatar">🎿</view>

				<!-- 行程卡气泡：点一下切到"行程"页看完整方案与实拍证据 -->
				<view v-if="m.kind === 'card'" class="card-bubble" @click="goTrip">
					<text class="card-title">🗂 已生成行程卡 · {{ m.card.resort }}</text>
					<text class="card-summary">{{ m.card.summary }}</text>
					<text class="card-more">查看完整方案与实拍证据 ›</text>
				</view>

				<view v-else class="bubble-wrap" :class="m.role">
					<!-- 引用校验打回：把"护栏真的在工作"露出来（这是本项目的看点，不是丑事） -->
					<view v-if="m.notice" class="notice">
						<text class="notice-text">↻ {{ m.notice }}</text>
					</view>

					<view v-if="m.content" class="bubble" :class="m.role">
						<template v-if="m.role === 'agent' && !m.streaming"><text v-for="(paragraph, pi) in replyParagraphs(m.content)" :key="pi" class="answer-paragraph" :class="{ 'answer-lead': pi === 0 && !m.failed && !m.stopped }">{{ paragraph }}</text></template><text v-else class="bubble-text">{{ m.content }}</text>
						<text v-if="m.streaming" class="caret">▍</text>
					</view>

					<!-- 等待中的进度：正在检索资料 / 正在查价格…（比一句笼统的"正在查资料"有用得多） -->
					<view v-if="m.status" class="status-line">
						<text class="status-text">{{ m.status }}</text>
					</view>

					<view v-if="(m.failed || m.stopped) && !m.streaming" class="recovery"><text class="recovery-copy">{{ m.failed ? '本轮未完成，已有内容仅供参考。' : '已停止生成，保留已收到的内容。' }}</text><button v-if="retryQuestion(messages, i)" size="mini" :disabled="loading" @click="retry(i)">重新询问这条问题</button></view>

					<!-- 来源溯源：把回答里的 [E201] 变成可点标签——点开看"谁说的、哪天的、几手数据" -->
					<view v-if="m.evidences && m.evidences.length" class="evidences">
						<text class="evidences-title">查看来源与日期 · {{ m.evidences.length }} 条</text>
						<view class="chips">
							<text v-for="e in m.evidences" :key="e.id" class="ev-chip" :class="e.status_key"
								@click="showEvidence(e)">E{{ e.id }}<template v-if="e.warn"> ⚠️</template></text>
						</view>
					</view>
					<view v-if="m.role === 'agent' && !m.streaming && !m.failed && !m.stopped && m.guidance" class="guidance">
<view v-if="m.guidance.run_brief" class="run-brief"><text class="guidance-title">{{ m.guidance.run_brief.title }}</text><text class="scene-caption">档案参数 · 不是模型印象</text><view v-for="run in m.guidance.run_brief.runs" :key="run.name" class="run-row"><text class="run-name"><text class="run-dot" :class="difficultyTone(run.difficulty)"></text>{{ run.name }} · {{ run.difficulty }}</text><text v-if="runMeta(run)" class="run-meta">{{ runMeta(run) }}</text><text v-if="run.note" class="scene-caption">{{ run.note }}</text><text v-if="m.guidance.scenes.length === 1" class="run-open" @click="openRun(m.guidance.scenes[0], run)">查看来源档案 ›</text></view><text v-if="m.guidance.run_brief.caveat" class="run-caveat">{{ m.guidance.run_brief.caveat }}</text><text class="scene-caption">{{ m.guidance.run_brief.source }} · {{ m.guidance.run_brief.date }}</text></view>
<MediaPanel v-if="m.guidance.media.length" :resources="m.guidance.media" compact heading="和这个问题相关的链接" />
<view v-if="m.guidance.suggestions.length" class="suggestions"><text class="guidance-title">你可以接着问</text><button v-for="q in m.guidance.suggestions" :key="q.id" class="suggestion" :disabled="loading" @click="send(q.question)">{{ q.label }} ›</button></view>
<view v-if="layoutScenes(m.guidance.scenes).length" class="scene-tools"><text class="scene-caption">可选辅助 · 已有全景布置的雪道示意，非实景或导航地图</text><view v-for="scene in layoutScenes(m.guidance.scenes)" :key="scene.resort_id" class="scene-actions"><button size="mini" @click="openScene(scene, 'all')">{{ scene.name }}雪道示意</button><button size="mini" @click="openScene(scene, 'beginner')">初级道筛选</button></view></view>
						<view v-if="m.guidance.learning.resources.length" class="learning-card">
							<text class="guidance-title">教学资源</text><text class="scene-caption">{{ m.guidance.learning.notice }}</text>
							<view v-for="resource in m.guidance.learning.resources" :key="resource.url" class="learning-item" @click="openLearning(resource)"><text>{{ resource.title }}</text><text class="scene-caption">{{ resource.source }} · {{ resource.updated_at }} · {{ resource.level }}</text></view>
						</view>
						<text v-else-if="m.guidance.learning.notice" class="resource-notice">{{ m.guidance.learning.notice }}</text>
					</view>


				</view>
			</view>
			<!-- 两个锚点：scroll-into-view 写同一个值不会触发滚动，靠它俩交替 -->
			<view id="bottom-a" class="bottom-pad"></view>
			<view id="bottom-b" class="bottom-pad"></view>
		</scroll-view>

		<!-- 往上翻历史时不要把人拽回底部：给个按钮，由用户决定 -->
		<view v-if="!atBottom && messages.length > 1" class="to-bottom" @click="scrollToBottom(true)">
			<text class="to-bottom-text">↓ 回到底部</text>
		</view>

		<!-- 输入区 -->
		<view class="input-bar">
			<input class="input" v-model="draft" :maxlength="-1" placeholder="说说你的计划，或直接问一个问题"
				confirm-type="send" @confirm="send()" />
			<button v-if="streaming" class="send-btn stop" @click="stop()">停止</button>
			<button v-else class="send-btn" :disabled="loading || !draft.trim()" @click="send()">
				{{ loading ? '思考中' : '发送' }}
			</button>
		</view>

		<!-- 证据详情弹层 -->
		<view v-if="evidence" class="mask" @click="evidence = null">
			<view class="sheet" @click.stop>
				<view class="sheet-head">
					<text class="sheet-title">E{{ evidence.id }}</text>
					<text class="badge" :class="evidence.status_key">{{ evidence.status || '未标注可信度' }}</text>
				</view>
				<!-- ⚠️ 这里刻意用普通 view + CSS overflow，**不用 <scroll-view>**：
				     uni-app 的 scroll-view 只在**固定 height** 下才滚动，给 max-height 它不认——
				     实测：内容真实高 1554px、容器 522px，而 scrollHeight 却等于 clientHeight
				     （判定为"不需要滚动"）→ 文字直接溢出弹层糊在页面上，比截断更难看。
				     证据原文现在最长 2000 字，必须能滚。H5 端 overflow-y:auto 完全够；
				     小程序端这条会退化成"看不全"，届时改成固定高度的 scroll-view 即可。 -->
				<view class="sheet-body">
					<view class="kv"><text class="kv-label">出处</text><text class="kv-value">{{ evidence.source || '—' }}</text></view>
					<view class="kv"><text class="kv-label">来源性质</text><text class="kv-value">{{ evidence.label || '—' }}</text></view>
					<view class="kv"><text class="kv-label">信息日期</text><text class="kv-value">{{ evidence.date || '—' }}</text></view>
					<view v-if="evidence.warn" class="warn-box"><text class="warn-text">⚠️ {{ evidence.warn }}</text></view>
					<text class="section-title">证据原文</text>
					<!-- 走 prettify：证据原文是带 markdown 标记的源文本，
					     直接渲染会露出 **两个口径** 这种星号（用户以为数据坏了） -->
					<text class="snippet">{{ evidence.snippet ? prettify(evidence.snippet) : '—' }}</text>
				</view>
				<view class="sheet-close" @click="evidence = null"><text>关闭</text></view>
			</view>
		</view>
	</view>
</template>

<script setup>
	import { ref, reactive, computed } from 'vue'
	import { CHAT_STARTERS, replyParagraphs, retryQuestion } from '@/utils/chat-presentation.js'
	import { onShow, onReady } from '@dcloudio/uni-app'
	import { chatStream } from '@/api/chat.js'
	import { getProfile } from '@/api/profile.js'
	import MediaPanel from '@/components/MediaPanel.vue'
  import { openMediaPage } from '@/utils/media-actions.js'
	import { prettify } from '@/utils/text.js'
	import { LAYOUT_RESORTS, normalizeGuidance, SCENE_INTENT_KEY } from '@/utils/guidance.js'

	const runMeta = (run) => [run.length_m ? `${run.length_m} 米` : '', run.vertical_m ? `落差 ${run.vertical_m} 米` : '', run.slope_deg ? `平均坡度约 ${run.slope_deg}°` : ''].filter(Boolean).join(' · ')
	const difficultyTone = (difficulty) => /中高|高级/.test(difficulty || '') ? 'advanced' : /中级/.test(difficulty || '') ? 'intermediate' : /初级/.test(difficulty || '') ? 'beginner' : 'other'
	const layoutScenes = (scenes) => (scenes || []).filter((scene) => LAYOUT_RESORTS.includes(scene.resort_id))

	const WELCOME = '你好，我是滑雪情报官。告诉我你的滑雪计划，我们一步一步准备；资料会附来源，缺少可靠信息时会明确说明。'

	// ⚠️ 这个 ref 曾经漏声明（2026-09-12 白屏事故）：
	//    下面 profileText / loadProfile 都在用它，少了这一行，页面在**渲染阶段**
	//    就抛 ReferenceError: profile is not defined → 整页白屏，而且控制台之外
	//    什么线索都没有。教训：script setup 里"用了但没声明"的变量不会在编译期报错，
	//    只会让整页静默挂掉；改完前端一定要真在浏览器里打开看一眼。
	const profile = ref({})
	const messages = ref([])
	const draft = ref('')
	// 当前点开的证据（null = 关闭）——来源溯源的详情弹层
	const evidence = ref(null)
	const showEvidence = (item) => { evidence.value = item }
	const loading = ref(false)      // 本轮是否在进行中（决定发送键是否可点）
	const streaming = ref(false)    // 是否正在逐字输出（决定"发送"要不要变成"停止"）
	const atBottom = ref(true)      // 用户是否贴着底部看（离得远就别自动滚）
	const scrollTarget = ref('')
	const tripState = ref(null)     // 本轮返回的行程要素（顶部那条状态栏用）

	// ⚠️ 会话 id 必须**每次打开 App 新生成一个**，不能留空。
	//    留空时后端会回退成一个固定 thread，于是所有会话永远共享同一段记忆——
	//    实测后果：模型从几周前的对话里背诵**数据更新前**的旧答案，
	//    还会说"这个刚才查过啦"，用户看到的是超时或"暂无可靠来源"。
	//    教训：记忆是有保质期的，数据一改，旧记忆就是污染源。
	const newThreadId = () => 'chat-' + Date.now() + '-' + Math.random().toString(36).slice(2, 8)
	const threadId = ref(newThreadId())

	// 当前这条流的句柄 + 它对应的那条消息（"停止"要用）
	let stream = null
	let currentReply = null

	const isWelcome = computed(() => !messages.value.some(m => m.role === 'user'))
	const retry = (index) => { const question = retryQuestion(messages.value, index); if (question) send(question) }

	const profileText = computed(() => {
		const p = profile.value
		if (!p || (!p.level && !p.board_type)) return '画像未设置 ›'
		return [p.level, p.board_type, p.home_region].filter(Boolean).join(' · ') + ' ›'
	})

	/** 顶部行程要素栏：有哪几项、还差哪几项 */
	const tripChips = computed(() => {
		const t = tripState.value
		if (!t) return []
		const chips = []
		if (t.resort) chips.push(t.resort)
		if (t.date) chips.push(t.date)
		if (t.days) chips.push(t.days + ' 天')
		if (t.people) chips.push(t.people + ' 人')
		if (t.budget) chips.push('预算 ' + t.budget)
		if (t.board) chips.push(t.board)
		if (!chips.length) return chips
		if (t.ready) {
			chips.push('要素齐全 ✓')
		} else {
			const missing = []
			if (!t.resort) missing.push('雪场')
			if (!t.date) missing.push('日期')
			if (!t.days) missing.push('天数')
			if (!t.people) missing.push('人数')
			if (!t.budget) missing.push('预算')
			chips.push('还差：' + missing.join('/'))
		}
		return chips
	})

	const goProfile = () => uni.switchTab({ url: '/pages/profile/profile' })
	const goTrip = () => uni.switchTab({ url: '/pages/trip/trip' })
	const openScene = (scene, filter) => {
		uni.setStorageSync(SCENE_INTENT_KEY, { resort_id: scene.resort_id, filter })
		uni.switchTab({ url: '/pages/graph/graph' })
	}
	const openRun = (scene, run) => {
		uni.setStorageSync(SCENE_INTENT_KEY, { resort_id: scene.resort_id, filter: 'all', focus: run.name })
		uni.switchTab({ url: '/pages/graph/graph' })
	}
	const openLearning = openMediaPage

	/** 开一段新对话：换 thread_id、清屏、顺手清掉上一份行程卡与本地存档 */
	const newChat = () => {
		if (loading.value) return
		threadId.value = newThreadId()
		messages.value = [addMessage({ role: 'agent', content: WELCOME })]
		tripState.value = null
		uni.removeStorageSync('lastTripCard')
		persist()                    // 覆盖掉上一段对话的存档
		scrollToBottom(true)
	}

	// ---------------- 会话持久化（刷新不丢对话） ----------------
	//
	// 为什么必须做：后端按 thread_id 存会话记忆（checkpoint），但前端原来**每次刷新
	// 都新生成一个 thread_id**、消息也只存在内存里 → 按一下 F5，对话内容和上下文一起没了。
	// 演示/录屏时手一抖（或者切窗口时误触），整段演示就废了，得从头再问一遍。
	// 现在把 thread_id 与消息一起落本地：刷新后接着聊，模型那边也还是同一段记忆
	// ——这也是验收标准"中断后重启可继续"真正的落点（光有 checkpointer 不够）。
	const HISTORY_KEY = 'chatHistory'
	const HISTORY_MAX = 40           // 只留最近这些条，别让 storage 无限长

	const persist = () => {
		try {
			const slim = messages.value.slice(-HISTORY_MAX).map((m) => ({
				role: m.role, kind: m.kind || '', content: m.content,
				card: m.card || null, notice: m.notice || '', guidance: m.guidance || null,
				stopped: !!m.stopped, failed: !!m.failed,
				evidences: m.evidences || []
			}))
			uni.setStorageSync(HISTORY_KEY, { threadId: threadId.value, messages: slim })
		} catch (e) {
			// 存不下（超限额/隐私模式）就算了：持久化坏了也不能把对话本身弄挂
		}
	}

	const restore = () => {
		try {
			const saved = uni.getStorageSync(HISTORY_KEY)
			if (!saved || !saved.messages || !saved.messages.length) return false
			messages.value = saved.messages.map((m) => addMessage(m))
			if (saved.threadId) threadId.value = saved.threadId
			return true
		} catch (e) {
			return false
		}
	}

	// ---------------- 消息与流式渲染 ----------------

	/**
	 * 新消息一律走这里，别直接 push 普通对象
	 *
	 * 为什么：往 ref 数组里 push 普通对象，模板里读到的确实是响应式代理，
	 * 但**手里拿到的那个引用还是原对象**——流式增量更新改的是原对象，
	 * 等于改了一份没人监听的副本，页面一个字都不会动。
	 * 包一层 reactive 之后，返回值本身就是代理，后续赋值才真的能渲染。
	 */
	const addMessage = (msg) => {
		const item = reactive(Object.assign({
			role: 'agent', content: '', raw: '', streaming: false,
			status: '', notice: '', stopped: false, failed: false, evidences: []
		}, msg))
		item.guidance = item.guidance && !item.failed && !item.stopped ? normalizeGuidance(item.guidance) : null
		messages.value.push(item)
		return item
	}

	// 流式渲染节流：token 的到达频率远高于屏幕刷新率，
	// 每个 token 都跑一遍 markdown 转换并重排，手机上会肉眼可见地卡。
	// 攒 50ms 渲染一次，观感仍是"一个字一个字往外蹦"。
	const dirty = new Set()
	let flushTimer = null
	const flushDirty = () => {
		flushTimer = null
		dirty.forEach((m) => {
			m.content = prettify(m.raw, { streaming: m.streaming })
		})
		dirty.clear()
		autoScroll()
	}
	const markDirty = (m) => {
		dirty.add(m)
		if (!flushTimer) flushTimer = setTimeout(flushDirty, 50)
	}
	/** 需要立刻上屏的时刻（收尾、清空重来、报错）：取消节流直接渲染 */
	const flushNow = (m) => {
		dirty.add(m)
		if (flushTimer) {
			clearTimeout(flushTimer)
			flushTimer = null
		}
		flushDirty()
	}

	// ---------------- 滚动 ----------------
	//
	// 用两个锚点交替当目标：scroll-into-view 传**相同的值不会再次触发滚动**，
	// 所以不能一直写 'bottom-a'。原实现靠"先置空、再 setTimeout 60ms 设回原值"
	// 来绕开它——流式期间每来一个字就起一个定时器，很快就堆爆了。
	const NEAR_BOTTOM = 140        // 距底部多近算"贴着底"（px）
	const chatHeight = ref(0)
	let flip = false
	let lastScrollAt = 0
	let ignoreScrollUntil = 0

	const scrollToBottom = (force) => {
		const now = Date.now()
		// 流式期间每个 token 都滚一次会吃掉帧率，节流到 80ms（约 12 帧/秒，够顺）
		if (!force && now - lastScrollAt < 80) return
		lastScrollAt = now
		// 自己滚动也会触发 scroll 事件，250ms 内不把它当成"用户在翻历史"
		ignoreScrollUntil = now + 250
		atBottom.value = true
		flip = !flip
		scrollTarget.value = flip ? 'bottom-a' : 'bottom-b'
	}

	const autoScroll = () => { if (atBottom.value) scrollToBottom(false) }

	const onScroll = (e) => {
		if (Date.now() < ignoreScrollUntil) return
		const d = (e && e.detail) || {}
		const view = d.clientHeight || chatHeight.value
		if (!d.scrollHeight || !view) return
		atBottom.value = (d.scrollHeight - d.scrollTop - view) < NEAR_BOTTOM
	}

	/** 量一下消息区高度：scroll 事件在 H5 端不带 clientHeight，自己量一次兜底 */
	const measureChat = () => {
		try {
			uni.createSelectorQuery().select('.chat').boundingClientRect((rect) => {
				if (rect && rect.height) chatHeight.value = rect.height
			}).exec()
		} catch (e) {
			// 量不到就保持"永远认为贴着底"——退回原来的行为，不会更糟
		}
	}

	// ---------------- 一轮对话 ----------------

	/**
	 * 处理一个流式事件
	 *
	 * 事件类型由后端 service/agent_service.py 的 achat_stream 定义：
	 *   meta 会话 id / status 进度 / text 正文增量 / reset 打回重写
	 *   trip 行程要素 / card 行程卡 / evidences 本轮来源 / error / done
	 */
	const handleEvent = (event, reply) => {
		switch (event.type) {
			case 'meta':
				// 后端生成的会话 id 会随第一条事件回来，存下来下一轮才能真的续聊
				if (event.thread_id) threadId.value = event.thread_id
				break
			case 'status':
				reply.status = event.text || ''
				break
			case 'text':
				reply.status = ''
				reply.raw += event.text || ''
				markDirty(reply)
				break
			case 'replace':
				reply.status = ''
				reply.notice = ''
				reply.raw = event.text || ''
				flushNow(reply)
				break
			case 'reset':
				// 引用校验打回：**必须把已经流出去的那一版清掉**，
				// 否则"被判不合格的回答"和"重写后的回答"会叠在同一个气泡里
				reply.guidance = null
				reply.raw = ''
				reply.notice = event.reason || '引用校验未通过，正在重新作答'
				reply.status = ''
				flushNow(reply)
				break
			case 'trip':
				tripState.value = event.trip || null
				break
			case 'card':
				showCard(event.card)
				break
			case 'evidences':
				reply.evidences = event.evidences || []
				break
			case 'guidance':
				if (!reply.failed && !reply.stopped) reply.guidance = normalizeGuidance(event.guidance)
				break
			case 'error':
				reply.guidance = null
				reply.failed = true
				reply.status = ''
				reply.raw += (reply.raw ? '\n\n' : '') + '⚠️ ' + (event.message || '服务出错了')
				flushNow(reply)
				break
			case 'done':
				finishTurn(reply)
				break
		}
	}

	/** 行程卡：存进本地（行程页读它），并在会话里留一个可点的卡片 */
	const showCard = (card) => {
		if (!card) return
		uni.setStorageSync('lastTripCard', card)
		addMessage({ role: 'agent', kind: 'card', card })
		scrollToBottom(true)
	}

	const finishTurn = (reply) => {
		if (!reply) return
		reply.streaming = false
		reply.status = ''
		if (!reply.raw && !reply.stopped) {
			reply.raw = reply.failed ? '' : '（本轮没有文字回复，可以换个问法再试）'
		}
		flushNow(reply)
		loading.value = false
		streaming.value = false
		stream = null
		currentReply = null
		persist()                    // 一轮结束才落盘：中途刷新不会留下"有问无答"的半截状态
		scrollToBottom(true)
	}

	const send = (preset) => {
		const content = (typeof preset === 'string' ? preset : draft.value).trim()
		if (!content || loading.value) return

		addMessage({ role: 'user', content })
		draft.value = ''
		loading.value = true
		streaming.value = true
		scrollToBottom(true)

		const reply = addMessage({
			role: 'agent', raw: '', content: '', streaming: true,
			status: '正在理解你的问题…', evidences: []
		})
		currentReply = reply

		// 流式：文字一到就上屏，不再等整段答案（原来最长要干等 30~90 秒）
		stream = chatStream(content, threadId.value, (event) => handleEvent(event, reply))
	}

	/** 停止生成：掐断这条流（后端也会收到断开并结束本轮） */
	const stop = () => {
		if (!stream) return
		if (currentReply) { currentReply.stopped = true; currentReply.guidance = null }
		stream.abort()
	}

	const loadProfile = async () => {
		try {
			profile.value = await getProfile()
		} catch (e) {
			profile.value = {}
		}
	}

	onReady(measureChat)

	onShow(() => {
		const token = uni.getStorageSync('token')
		if (!token) {
			uni.reLaunch({ url: '/pages/login/login' })
			return
		}
		measureChat()
		loadProfile()
		// 先恢复上次的对话（连 thread_id 一起），没有再摆欢迎语
		if (!messages.value.length && !restore()) {
			messages.value = [addMessage({ role: 'agent', content: WELCOME })]
		}
	})
</script>

<style>
.welcome-panel { max-width: 760px; margin: 28rpx auto; padding: 24rpx 8rpx; }
.welcome-kicker { display: block; color: #24738b; font-size: 24rpx; font-weight: 600; }
.welcome-title { display: block; font-size: 46rpx; color: #12304a; font-weight: 700; margin: 12rpx 0; }
.welcome-copy { display: block; color: #52677b; line-height: 1.7; font-size: 27rpx; margin-bottom: 28rpx; }
.task-card { text-align: left; margin: 16rpx 0; padding: 24rpx; background: white; border: 1rpx solid #dce7ed; border-radius: 20rpx; line-height: 1.6; }
.task-card::after { border: none; }
.task-title { display: block; color: #173d56; font-size: 29rpx; font-weight: 600; }
.task-detail,.welcome-trust { display: block; color: #657b8e; font-size: 23rpx; line-height: 1.7; }
.welcome-trust { margin-top: 24rpx; }
.answer-paragraph { display: block; white-space: pre-wrap; font-size: 28rpx; line-height: 1.85; margin-top: 22rpx; overflow-wrap: anywhere; }
.answer-paragraph:first-child { margin-top: 0; }
.answer-lead { font-weight: 600; color: #173d56; }
.scene-tools { margin-top: 18rpx; padding: 12rpx 0; border-top: 1rpx solid #e2eaf0; }
.resource-notice { display: block; margin-top: 16rpx; color: #657b8e; font-size: 22rpx; line-height: 1.6; }
.recovery { margin-top: 14rpx; padding: 18rpx; border-radius: 14rpx; background: #fff5e8; }
.recovery-copy { display: block; font-size: 24rpx; line-height: 1.6; color: #865c2c; }
.recovery button { margin: 12rpx 0 0; color: #245c73; }
.row { max-width: 900px; margin-left: auto; margin-right: auto; }
@media (prefers-reduced-motion: reduce) { .caret,.status-text { animation: none !important; } }
	.guidance { width: 100%; margin-top: 14rpx; }.run-brief,.scene-card,.learning-card,.suggestions { margin-top: 14rpx; padding: 18rpx; border-radius: 18rpx; background: #fff; border: 1rpx solid #dce7ed; }.run-name { display: block; color: #173d56; font-size: 28rpx; font-weight: 600; margin-top: 12rpx; }.run-dot { display: inline-block; width: 14rpx; height: 14rpx; margin-right: 8rpx; border-radius: 50%; background: #64748b; }.run-dot.beginner { background: #12a150; }.run-dot.intermediate { background: #1f6feb; }.run-dot.advanced { background: #dc2626; }.run-open { display: block; margin-top: 6rpx; color: #24738b; font-size: 22rpx; }.run-meta { display: block; color: #1d4e63; font-size: 24rpx; line-height: 1.6; }.run-caveat { display: block; margin-top: 12rpx; padding: 12rpx; border-radius: 12rpx; background: #f4f8ea; color: #3d5a1f; font-size: 22rpx; line-height: 1.6; }.scene-preview { width: 100%; height: 180rpx; border-radius: 12rpx; }.scene-title,.guidance-title { display: block; font-size: 25rpx; font-weight: 600; color: #225268; margin: 8rpx 0; }.scene-caption { display: block; color: #718493; font-size: 20rpx; line-height: 30rpx; }.scene-actions { display: flex; flex-wrap: wrap; gap: 10rpx; margin-top: 14rpx; }.scene-actions button { margin: 0; color: #24738b; }.suggestion { text-align: left; margin: 12rpx 0 0; font-size: 23rpx; background: #edf5f7; color: #245c73; }.learning-item { padding: 14rpx 0; color: #24738b; }

	/* ⚠️ 这一页的布局有三个坑，踩过一次"输入框/对话框整个不见了"，逐条说明：
	 *
	 * 1. 页面高度不能用 height:100%
	 *    H5 带 tabBar 时，100% 把 tabBar 那 50px 也算在里头，底部元素正好被压在
	 *    tabBar 底下看不见。改用 uni-app 注入的 CSS 变量把导航栏与 tabBar 让出来；
	 *    非 H5 端这两个变量不存在，回退 0px，等价于 100vh，行为不变。
	 *
	 * 2. 输入栏必须 flex-shrink: 0
	 *    flex 子项默认 min-height:auto —— **它不肯缩到比内容还小**。消息一多，
	 *    .chat 撑住不缩 → 容器溢出 → 被挤扁的是没设保护的 .input-bar，
	 *    高度被压成 0：现象就是"聊着聊着，输入框没了"。
	 *
	 * 3. .chat 要显式 min-height: 0
	 *    与第 2 条是一对：放开 flex:1 子项的最小高度限制，它才会老老实实滚动，
	 *    而不是把下面的输入栏顶出屏幕。
	 */
	.page {
		display: flex; flex-direction: column; position: relative;
		height: calc(100vh - var(--window-top, 0px) - var(--window-bottom, 0px));
		box-sizing: border-box; background-color: #f4f7fb;
	}
	.header { display: flex; align-items: center; justify-content: space-between; padding: 20rpx 28rpx; background-color: #0f2a4a; flex-shrink: 0; }
	.brand { color: #ffffff; font-size: 30rpx; font-weight: bold; }
	.header-right { display: flex; align-items: center; }
	/* ⚠️ 头部按钮原来也叫 .chip，而证据标签的 .chip 写在后面 →
	   后写的规则赢，头部按钮被套上了白底深灰字，在深蓝导航条上几乎看不清。
	   现在拆成两个类名（head-chip / ev-chip），互不干扰。 */
	.head-chip { color: #cfe3ff; font-size: 24rpx; border: 1rpx solid #3f6ea8; border-radius: 999rpx; padding: 6rpx 20rpx; background-color: transparent; }
	.header-right .head-chip { margin-left: 14rpx; }

	/* 行程要素栏 */
	.trip-bar { display: flex; flex-wrap: wrap; align-items: center; padding: 12rpx 24rpx; background-color: #e8f0fe; flex-shrink: 0; }
	.trip-bar-label { font-size: 22rpx; color: #4a7ab5; margin-right: 14rpx; }
	.trip-chip { font-size: 22rpx; color: #1f6feb; background-color: #ffffff; border-radius: 999rpx; padding: 4rpx 16rpx; margin: 0 10rpx 6rpx 0; }

	.chat { flex: 1; min-height: 0; padding: 24rpx; box-sizing: border-box; }
	.bubble-wrap { max-width: 82%; display: flex; flex-direction: column; }
	.bubble-wrap.user { align-items: flex-end; }
	.evidences { margin-top: 10rpx; }
	.evidences-title { font-size: 20rpx; color: #9aa8bb; }
	.chips { display: flex; flex-wrap: wrap; margin-top: 8rpx; }
	.ev-chip { font-size: 20rpx; padding: 4rpx 14rpx; border-radius: 999rpx; margin: 0 10rpx 10rpx 0; background-color: #eef1f6; color: #64748b; }
	.ev-chip.verified { background-color: #e6f7ee; color: #12a150; }
	.ev-chip.corroborated { background-color: #e8f0fe; color: #1f6feb; }
	.ev-chip.transcribed { background-color: #fff4e5; color: #d97706; }
	.ev-chip.sample { background-color: #fdeaea; color: #dc2626; }

	/* 流式：光标 + 进度提示 */
	@keyframes pp-blink { 0%, 100% { opacity: 1; } 50% { opacity: 0; } }
	@keyframes pp-pulse { 0%, 100% { opacity: 0.5; } 50% { opacity: 1; } }
	.caret { font-size: 26rpx; color: #1f6feb; margin-left: 4rpx; animation: pp-blink 1s steps(1) infinite; }
	.status-line { display: flex; align-items: center; margin-top: 8rpx; padding: 8rpx 18rpx; background-color: #eef2f8; border-radius: 999rpx; align-self: flex-start; }
	.status-text { font-size: 22rpx; color: #6b7c93; animation: pp-pulse 1.2s ease-in-out infinite; }
	.stopped { font-size: 22rpx; color: #9aa8bb; margin-top: 8rpx; }
	.notice { background-color: #fff4e5; border-radius: 10rpx; padding: 8rpx 16rpx; margin-bottom: 10rpx; align-self: flex-start; }
	.notice-text { font-size: 22rpx; color: #d97706; }

	/* 行程卡气泡 */
	.card-bubble { max-width: 82%; background-color: #ffffff; border-left: 6rpx solid #1f6feb; border-radius: 18rpx; padding: 22rpx 24rpx; box-shadow: 0 2rpx 8rpx rgba(15, 42, 74, 0.06); }
	.card-title { display: block; font-size: 26rpx; font-weight: bold; color: #1c2b3a; }
	.card-summary { display: block; font-size: 24rpx; color: #4a5b70; line-height: 38rpx; margin-top: 10rpx; }
	.card-more { display: block; font-size: 24rpx; color: #1f6feb; margin-top: 14rpx; }

	/* 回到底部（浮在输入栏上方，不挡内容） */
	.to-bottom { position: absolute; right: 30rpx; bottom: 150rpx; z-index: 20; background-color: #ffffff; border-radius: 999rpx; padding: 12rpx 24rpx; box-shadow: 0 4rpx 16rpx rgba(15, 42, 74, 0.16); }
	.to-bottom-text { font-size: 22rpx; color: #1f6feb; }

	.mask { position: fixed; left: 0; right: 0; top: 0; bottom: 0; background-color: rgba(15, 42, 74, 0.45); display: flex; align-items: flex-end; z-index: 99; }
	.sheet { width: 100%; max-height: 78%; background-color: #ffffff; border-radius: 24rpx 24rpx 0 0; padding: 28rpx 30rpx 10rpx; box-sizing: border-box; display: flex; flex-direction: column; }
	/* 限高 + 滚动：证据原文最长 2000 字，不限高就会溢出弹层；
	   这里必须用 CSS overflow（不能靠 uni-app 的 scroll-view，它只认固定 height） */
	.sheet-body { max-height: 56vh; overflow-y: auto; -webkit-overflow-scrolling: touch; }
	.sheet-head { display: flex; align-items: center; justify-content: space-between; margin-bottom: 18rpx; }
	.sheet-title { font-size: 34rpx; font-weight: bold; color: #1c2b3a; }
	.badge { font-size: 22rpx; padding: 6rpx 16rpx; border-radius: 999rpx; background-color: #eef1f6; color: #64748b; }
	.badge.verified { background-color: #e6f7ee; color: #12a150; }
	.badge.corroborated { background-color: #e8f0fe; color: #1f6feb; }
	.badge.transcribed { background-color: #fff4e5; color: #d97706; }
	.badge.sample { background-color: #fdeaea; color: #dc2626; }
	.kv { display: flex; padding: 10rpx 0; border-bottom: 1rpx solid #f0f3f8; }
	.kv-label { font-size: 24rpx; color: #7a8aa0; width: 150rpx; flex-shrink: 0; }
	.kv-value { font-size: 24rpx; color: #1c2b3a; flex: 1; }
	.warn-box { background-color: #fff4e5; border-radius: 12rpx; padding: 14rpx 18rpx; margin-top: 14rpx; }
	.warn-text { font-size: 22rpx; color: #d97706; }
	.section-title { display: block; font-size: 26rpx; font-weight: bold; color: #1c2b3a; margin: 20rpx 0 10rpx; }
	.snippet { font-size: 23rpx; color: #4a5b70; line-height: 36rpx; }
	.sheet-close { text-align: center; color: #1f6feb; font-size: 28rpx; padding: 24rpx 0; }
	.row { display: flex; margin-bottom: 24rpx; }
	.row.user { justify-content: flex-end; }
	.avatar { width: 60rpx; height: 60rpx; border-radius: 50%; background-color: #e6eefc; text-align: center; line-height: 60rpx; font-size: 30rpx; margin-right: 14rpx; flex-shrink: 0; }
	.bubble { max-width: 100%; padding: 20rpx 24rpx; border-radius: 18rpx; background-color: #ffffff; box-shadow: 0 2rpx 8rpx rgba(15, 42, 74, 0.06); }
	.bubble.user { background-color: #1f6feb; }
	.bubble-text { font-size: 28rpx; line-height: 44rpx; color: #1c2b3a; white-space: pre-wrap; }
	.bubble.user .bubble-text { color: #ffffff; }
	.bottom-pad { height: 10rpx; }
	.quick { display: flex; flex-wrap: wrap; padding: 0 24rpx 16rpx; flex-shrink: 0; }
	.quick-item { font-size: 24rpx; color: #1f6feb; background-color: #e8f0fe; border-radius: 999rpx; padding: 10rpx 22rpx; margin: 0 14rpx 14rpx 0; }
	.input-bar { display: flex; align-items: center; padding: 16rpx 24rpx 24rpx; background-color: #ffffff; border-top: 1rpx solid #e6ecf5; flex-shrink: 0; }
	.input { flex: 1; height: 76rpx; background-color: #f1f4f9; border-radius: 999rpx; padding: 0 28rpx; font-size: 28rpx; }
	.send-btn { width: 150rpx; height: 76rpx; line-height: 76rpx; margin-left: 16rpx; background-color: #1f6feb; color: #ffffff; font-size: 28rpx; border-radius: 999rpx; flex-shrink: 0; }
	.send-btn[disabled] { background-color: #a8c4e8; color: #ffffff; }
	.send-btn.stop { background-color: #ffffff; color: #d9480f; border: 1rpx solid #f0b49a; }
</style>
