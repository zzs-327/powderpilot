// 万龙专项地形布局 v2：以**具名雪道**为主，山体围绕雪道成形。
//
// 依据：万龙官方《萬龍度假天堂雪道全景圖》（2025.10 版，档案 provenance 里记录的
// 那张官方全景图）。从图上读到、并写进本文件的是：
//   1. 朝向：图中罗盘 S 在上 → 山顶在南（z 小），酒店群与雪场入口在北（z 大）
//   2. 山顶索道上站集中在中央偏左（约 x = -3…+2），雪道自山顶呈扇形向下展开，
//      到山脚排成一列、各自对应索道下站
//   3. 分区：西侧山翼（x < -6）是初级绿道与野雪区，其中 01 长龙道是一条带大型
//      回头弯的长距离横切；中部是黑道密集的陡面（22/23/24/25/26 等）；东侧是
//      27/28 两条黑道长坡，以及 30 五彩龙道那条绕东侧山脊下行的长蓝道
//   4. 山脚自西向东：盛龙公寓 / 双龙酒店 / 万龙公寓 / 聚龙殿·龙宫酒店 / 汇龙殿 /
//      室外停车场 / 红花梁隧道（往崇礼城区）
//
// 有意不做的事：不给任何一条雪道写经纬度或实测宽度。下面的 top/bottom/bow 是
// **示意**，用来还原"谁在哪一片坡上、和谁相邻、往哪个方向下"；不等比、不用于导航。
// 雪道长度、坡度、海拔一律来自档案（graph.json 的 attrs），不在本文件重复。
//
// 坐标系（场景单位）：
//   x：西 -15 → 东 +15（全景图左 → 右）
//   z：山顶/南 -11 → 山脚/北 +11（全景图上 → 下）
// 1 个高度单位 ≈ 100 米（2110.3 - 1550 ≈ 560 米）；水平与垂直**不等比**。

//: 雪道按难度给一个**视觉**宽度（不是实测米数）：初级道最宽、高级道最窄。
//: 全景图上初级道确实最宽（常做教学与横切），高级道多为窄陡槽。
export const RUN_WIDTH = { beginner: 1.05, intermediate: 0.82, advanced: 0.62, other: 0.75 }

//: 每条雪道：top 上端（山顶方向）/ bottom 下端（山脚方向）/ note 图上看到的特点。
//: bow 是中途横向偏移（正=向东鼓、负=向西鼓），用来造出横切与回头弯。
export const WANLONG_RUNS = {
	// ---------------- 初级（4 条）----------------
	长龙道: { level: 'beginner', top: [-2.4, -8.9], bottom: [-3.8, 9.2], bow: -9.6, note: '西侧山翼长距离横切，带大型回头弯' },
	短龙道: { level: 'beginner', top: [-6.3, -3.4], bottom: [-7.0, -0.4], bow: -0.7, note: '西侧山腰短程连接道' },
	卧龙道: { level: 'beginner', top: [-5.4, 8.0], bottom: [-4.3, 9.5], bow: -0.4, note: '山脚最缓，配魔毯' },
	娇龙道: { level: 'beginner', top: [1.1, 5.1], bottom: [6.4, 6.3], bow: 0.5, note: '中部陡面脚下的缓坡归途' },

	// ---------------- 中级（9 条）----------------
	游龙道: { level: 'intermediate', top: [-4.7, -8.5], bottom: [-2.2, 6.6], bow: -3.2, note: '中西部 S 形长中级道' },
	玉龙道: { level: 'intermediate', top: [-2.1, -9.0], bottom: [0.7, 6.5], bow: -2.3, note: '中西部，上段接近山顶' },
	飞龙道: { level: 'intermediate', top: [-1.3, -9.0], bottom: [-0.9, 6.5], bow: -0.7 },
	小龙道: { level: 'intermediate', top: [-3.7, -3.8], bottom: [-4.9, 2.2], bow: -1.0, note: '中西部山腰短中级道' },
	酷龙道: { level: 'intermediate', top: [-5.3, -2.6], bottom: [-6.4, 2.8], bow: -1.1, note: '西侧山腰中级道' },
	银龙道: { level: 'intermediate', top: [-0.8, -9.0], bottom: [0.2, 6.5], bow: 0.6 },
	雨龙道: { level: 'intermediate', top: [1.0, -2.5], bottom: [2.4, 5.9], bow: 0.7, note: '中下部中级道' },
	紫龙道: { level: 'intermediate', top: [5.6, 0.6], bottom: [8.5, 6.3], bow: 0.6, note: '东侧 6 号索道旁' },
	五彩龙道: { level: 'intermediate', top: [2.4, -9.0], bottom: [7.9, 6.5], bow: 5.3, note: '绕东侧山脊下行的长蓝道' },

	// ---------------- 高级（19 条）----------------
	中华龙道: { level: 'advanced', top: [-3.1, -5.4], bottom: [-6.8, 6.6], bow: -1.0, note: '纵贯中西部的长陡坡' },
	金龙道: { level: 'advanced', top: [-3.5, -9.0], bottom: [-13.5, 6.2], bow: -2.6, note: '长陡坡，下半段较窄' },
	恐龙道: { level: 'advanced', top: [-3.0, -9.0], bottom: [-11.5, 6.4], bow: -2.1 },
	翔龙道: { level: 'advanced', top: [-2.4, -9.0], bottom: [-9.5, 6.6], bow: -1.7 },
	炫龙道: { level: 'advanced', top: [1.5, -3.5], bottom: [4.6, 6.5], bow: 0.7, note: '中下部陡面' },
	猛龙道: { level: 'advanced', top: [0.9, -4.7], bottom: [3.2, 6.5], bow: 0.6 },
	大奔头: { level: 'advanced', top: [0.6, -2.2], bottom: [1.6, 1.7], bow: 0.3, note: '中部著名陡段' },
	威龙道: { level: 'advanced', top: [0.4, -5.3], bottom: [2.8, 6.5], bow: -0.3 },
	腾龙道: { level: 'advanced', top: [1.0, -7.1], bottom: [3.6, 6.5], bow: 0.4, note: '中部长陡坡' },
	逸龙道: { level: 'advanced', top: [1.2, -8.7], bottom: [2.2, -1.6], bow: 0.3, note: '中部上段陡坡' },
	海龙道: { level: 'advanced', top: [0.3, -9.0], bottom: [1.8, 6.5], bow: 0.5 },
	雾龙道: { level: 'advanced', top: [0.7, -9.0], bottom: [1.2, -3.2], bow: 0.3, note: '中部上段短陡坡' },
	霜龙道: { level: 'advanced', top: [1.1, -9.0], bottom: [2.0, -1.0], bow: 0.4, note: '中部上段陡坡' },
	雪龙道: { level: 'advanced', top: [1.5, -9.0], bottom: [2.6, 0.6], bow: 0.5, note: '中部上段陡坡' },
	风龙道: { level: 'advanced', top: [2.0, -9.0], bottom: [5.8, 6.6], bow: 1.4, note: '档案里平均坡度最陡' },
	云龙道: { level: 'advanced', top: [2.3, -9.0], bottom: [7.8, 6.6], bow: 1.0 },
	绿龙道: { level: 'advanced', top: [1.8, -9.0], bottom: [9.8, 6.6], bow: 2.0, note: '东侧长陡坡' },
	赤龙道: { level: 'advanced', top: [11.7, 3.2], bottom: [12.7, 6.2], bow: 0.4, note: '东端极短陡坡' },
	黄龙道: { level: 'advanced', top: [10.5, -1.2], bottom: [12.9, 6.4], bow: 0.6, note: '东端陡坡' }
}

const STEPS = 7

/**
 * 由 上端/下端/横向鼓包 生成折线。
 *
 * 输出顺序沿用旧约定：**山脚 → 山顶**（z 由大变小），因为地形渲染按这个方向取
 * 标签锚点与缆车路径。bow 用 sin 曲线施加，这样横切道会形成平滑的回头弯。
 */
function trailPoints(run) {
	const [tx, tz] = run.top
	const [bx, bz] = run.bottom
	const bow = run.bow || 0
	const points = []
	for (let i = 0; i < STEPS; i++) {
		const t = i / (STEPS - 1)          // 0 = 山脚, 1 = 山顶
		// 越靠山脚鼓包越明显：横切道与回头弯都发生在下段
		const x = bx + (tx - bx) * t + bow * Math.sin(Math.PI * t) * (1 - 0.35 * t)
		const z = bz + (tz - bz) * t
		points.push([Math.round(x * 100) / 100, Math.round(z * 100) / 100])
	}
	return points
}

function buildTrails(runs) {
	const table = {}
	for (const [name, run] of Object.entries(runs)) {
		table[name] = { points: trailPoints(run), note: run.note || '' }
	}
	return table
}

export const WANLONG_PROFILE = {
	id: 'wanlong',
	label: '万龙滑雪场',
	summit_elevation_m: 2110.3,
	base_elevation_m: 1550,
	orientation: '图上南下北：山顶在南、酒店群在北',
	// 单位高度 ≈ 100 米（2110.3 - 1550 ≈ 560 米 → 5.6 个单位）
	peaks: [
		{ x: 0.2, z: -9.8, h: 5.6, spread: 34 },
		{ x: -8.5, z: -6.0, h: 2.9, spread: 30 },
		{ x: 9.5, z: -7.4, h: 3.6, spread: 26 },
		{ x: 0, z: 8.5, h: 0.32, spread: 64 }
	],
	//: 山脊线：山顶平台沿东西向展开，与全景图上的山脊走向一致（示意）
	ridges: [
		[[-3.6, -9.6], [-0.6, -10.2], [2.8, -9.6], [6.6, -8.4], [11, -6.8], [14.4, -4.8]],
		[[-3.6, -9.6], [-7.6, -8.2], [-11, -5.8], [-13.6, -2.6]]
	],
	//: 雪道分组处的浅沟：让雪道看起来是"切"在山面上，而不是贴在山包上
	gullies: [
		{ x: -11.5, z: 2, depth: 0.55, spread: 30 },
		{ x: 2.2, z: -1, depth: 0.5, spread: 24 },
		{ x: 6.6, z: -1, depth: 0.42, spread: 22 }
	],
	runs: WANLONG_RUNS,
	// 名称 → 折线（山脚 → 山顶，z 由大变小）
	trails: buildTrails(WANLONG_RUNS),
	// 索道：山脚 → 山顶；编号与全景图一致，自左向右 1、2、4、5、6、7
	lifts: {
		'1号索道': { points: [[-3.0, 7.4], [-2.7, 1], [-2.3, -5], [-2.2, -8.9]] },
		'2号索道': { points: [[-2.2, 7.4], [-2.0, 1], [-1.7, -5], [-1.6, -8.9]] },
		'4号索道': { points: [[1.9, 7.2], [1.8, 1], [1.6, -5], [1.5, -8.9]] },
		'5号索道': { points: [[6.4, 7.4], [6.1, 1], [5.7, -5], [5.6, -8.8]] },
		'6号索道': { points: [[8.5, 7.4], [8.0, 1], [7.6, -5], [7.5, -8.6]] },
		'7号索道': { points: [[12.9, 7.6], [12.0, 1], [11.2, -5], [11.0, -8.4]] },
		卧龙道魔毯: { points: [[-5.1, 8.5], [-4.7, 9.5]], short: true },
		单板练习区魔毯: { points: [[-3.5, 8.1], [-3.1, 9.2]], short: true },
		雪具大厅魔毯: { points: [[3.7, 8.3], [4.2, 9.4]], short: true }
	},
	//: 山脚与山顶设施（名称转录自全景图，供定位参考）
	landmarks: [
		{ label: '盛龙公寓', x: -12.6, z: 10.4, kind: 'base' },
		{ label: '双龙酒店', x: -6.6, z: 11.6, kind: 'base' },
		{ label: '万龙公寓', x: -1.4, z: 12.2, kind: 'base' },
		{ label: '聚龙殿·龙宫酒店', x: 3.2, z: 11.8, kind: 'base' },
		{ label: '汇龙殿', x: 7.2, z: 11.8, kind: 'base' },
		{ label: '室外停车场', x: 10.2, z: 11.4, kind: 'base' },
		{ label: '雪场入口（往太子城/市区）', x: 13.6, z: 12.6, kind: 'gate' },
		{ label: '山顶广场·半山雪 Loong Café', x: 0.4, z: -10.6, kind: 'summit' },
		{ label: '万龙木屋', x: -2.6, z: -10.4, kind: 'summit' },
		{ label: '星空房车', x: 3.6, z: -10.2, kind: 'summit' },
		{ label: '八角亭休息室', x: -9.4, z: -5.2, kind: 'rest' }
	],
	//: 野雪区（全景图上 A–U 字母区块）：只给范围与树木疏密，不给实测边界
	glades: [
		{ label: 'A', x: -12.5, z: -2.5, r: 3.4, trees: 0.5 },
		{ label: 'B', x: -5.6, z: -6.4, r: 2.0, trees: 0.55 },
		{ label: 'C', x: -4.0, z: -5.4, r: 1.7, trees: 0.5 },
		{ label: 'D', x: -9.0, z: 1.2, r: 3.0, trees: 0.45 },
		{ label: 'E', x: -1.0, z: 0.6, r: 2.6, trees: 0.4 },
		{ label: 'H', x: -5.6, z: -0.6, r: 1.6, trees: 0.5 },
		{ label: 'I', x: -5.8, z: 1.8, r: 1.4, trees: 0.5 },
		{ label: 'J', x: 0.4, z: 2.6, r: 2.6, trees: 0.35 },
		{ label: 'M', x: 2.2, z: -1.6, r: 1.6, trees: 0.4 },
		{ label: 'P', x: 5.4, z: -0.4, r: 2.4, trees: 0.4 },
		{ label: 'Q', x: 6.6, z: -6.6, r: 2.0, trees: 0.4 },
		{ label: 'R', x: 5.2, z: 2.8, r: 2.6, trees: 0.4 },
		{ label: 'S', x: 7.8, z: -4.6, r: 1.8, trees: 0.45 },
		{ label: 'T', x: 8.6, z: -1.0, r: 1.8, trees: 0.4 },
		{ label: 'U', x: 10.4, z: 3.4, r: 1.8, trees: 0.45 }
	]
}

/** 取某条雪道的视觉样式（难度色、示意宽度、图上备注） */
export function runStyle(profile, name) {
	const run = (profile || WANLONG_PROFILE).runs?.[name]
	if (!run) return null
	return { level: run.level, width: RUN_WIDTH[run.level] || RUN_WIDTH.intermediate, note: run.note || '' }
}

/**
 * 取一条雪道/缆车的示意折线：优先用档案里的布局，没有再按水平退化生成。
 * 退化生成也能保证"每条实体都画得出来"，不会因为布局缺失而丢东西。
 */
export function routeFor(profile, entity, index) {
	const table = entity.type === 'lift' ? profile?.lifts : profile?.trails
	const liftNumber = entity.type === 'lift' && /^(\d+)(?:号)?(?:索道|索)$/.exec(entity.label || '')
	const key = liftNumber ? `${liftNumber[1]}号索道` : entity.label
	const hit = table && table[key]
	if (hit) return { points: hit.points, note: hit.note || '', short: Boolean(hit.short) }
	const level = entity.level || ''
	const base = entity.type === 'lift' ? -7 + (index % 12) * 1.2 : -6 + (index % 13) * 1.05
	const spread = level === 'advanced' ? 0.35 : level === 'intermediate' ? 0.7 : 1.2
	const from = level === 'beginner' ? 9.5 : 8.8
	const to = level === 'beginner' ? 0 : level === 'intermediate' ? -5 : -8.2
	return { points: [[base, from], [base + spread, (from + to) / 2], [base + spread * 1.4, to]], note: '', short: false }
}
