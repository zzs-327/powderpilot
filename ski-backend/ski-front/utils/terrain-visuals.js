// 三维雪场的几何与预算工具（纯函数，便于离线测试）。
//
// ⚠️ 这里所有高度、宽度、疏密都是**视觉示意**：不代表实测高程、实测雪道宽度或
// 设施坐标。业务数据（长度/坡度/海拔/来源）一律来自档案，不从这些数字反推。
//
// 坐标：x 西(-15) → 东(+15)；z 山顶/南(-11) → 山脚/北(+11)。

//: 主坡面的起止（山脚平台 → 山顶平台），与 wanlong.js 的布局同一套坐标
export const FACE_TOP_Z = -9
export const FACE_BOTTOM_Z = 9.5

//: 雪道**视觉**宽度：初级最宽、高级最窄（与 wanlong.js 的 RUN_WIDTH 保持一致）
export const RUN_VISUAL_WIDTH = { beginner: 1.05, intermediate: 0.82, advanced: 0.62, other: 0.75 }

//: 垂直夸张系数（仅视觉，不改变任何业务数据）
export const VERTICAL_EXAGGERATION = 1.45

const clamp01 = (value) => Math.max(0, Math.min(1, value))
const smoothstep = (value) => { const t = clamp01(value); return t * t * (3 - 2 * t) }

/** 点到折线的最近距离（用于"雪道上不种树"与山脊成形） */
export function distanceToPolyline(x, z, points) {
	if (!points || !points.length) return Infinity
	if (points.length === 1) return Math.hypot(x - points[0][0], z - points[0][1])
	let best = Infinity
	for (let i = 1; i < points.length; i++) {
		const a = points[i - 1], b = points[i]
		const dx = b[0] - a[0], dz = b[1] - a[1]
		const length2 = dx * dx + dz * dz || 1
		const t = clamp01(((x - a[0]) * dx + (z - a[1]) * dz) / length2)
		best = Math.min(best, Math.hypot(x - a[0] - t * dx, z - a[1] - t * dz))
	}
	return best
}

/**
 * 山体高度场：南北向主坡面 + 东西向差异 + 山脊/浅沟 + 褶皱细节。
 *
 * 兼容旧调用：第三个参数可以直接是 peaks 数组（老的通用山包）。
 * 传入 profile 时用 peaks（局部山包）+ ridges（山脊线）+ gullies（雪道分组浅沟）。
 */
export function visualHeightAt(x, z, profile) {
	const list = Array.isArray(profile) ? profile : (profile?.peaks || [])
	const ridges = (!Array.isArray(profile) && profile?.ridges) || []
	const gullies = (!Array.isArray(profile) && profile?.gullies) || []
	const summit = list.reduce((max, peak) => Math.max(max, peak.h || 0), 0)
	if (!summit) return 0

	// 1) 南北主坡面：山脚平台为 0，向南山顶抬升；指数 >1 让下段缓、上段陡
	const t = clamp01((FACE_BOTTOM_Z - z) / (FACE_BOTTOM_Z - FACE_TOP_Z))
	let height = summit * Math.pow(t, 1.45)
	if (z < FACE_TOP_Z) height += 0.1 * smoothstep((FACE_TOP_Z - z) / 2.5)

	// 2) 东西向：西侧山翼更缓（全景图里西翼是初级道与野雪区），东端略低
	height *= 1 - 0.3 * smoothstep((-5.5 - x) / 6.5)
	height *= 1 - 0.24 * smoothstep((x - 7) / 6.5)
	// 山体四缘收敛：东西两端与南侧都要落回地面，避免出现横贯画面的高墙
	height *= 1 - smoothstep((Math.abs(x) - 11.5) / 6)
	height *= 1 - smoothstep((-13 - z) / 4)

	// 3) peaks 只提供局部山包起伏（不再决定整体坡度），并随高度衰减
	for (const peak of list) {
		const spread = peak.spread || 20
		height += 0.16 * (peak.h / summit) * Math.exp(-(((x - peak.x) ** 2 + (z - peak.z) ** 2) / spread)) * t
	}

	// 4) 山脊线：沿折线略微抬起，形成山肩
	for (const ridge of ridges) {
		const distance = distanceToPolyline(x, z, ridge)
		height += 0.34 * Math.exp(-(distance * distance) / 5) * Math.min(1, t * 1.6)
	}

	// 5) 浅沟：沿雪道分组方向（南北）压低，让雪道看起来是"切"在山面上
	for (const gully of gullies) {
		const spread = gully.spread || 24
		height -= (gully.depth || 0.4)
			* Math.exp(-((x - gully.x) ** 2) / spread)
			* Math.exp(-((z - gully.z) ** 2) / (spread * 3))
	}

	// 6) 褶皱细节：细小起伏只增加质感，不改变整体形状
	const folds = Math.sin(x * 0.9 + z * 0.3) * Math.sin(z * 0.62) * 0.2
		+ Math.sin(x * 2.1 - z * 0.5) * Math.cos(z * 1.3) * 0.07
		+ Math.sin(x * 4.3 + z * 1.7) * 0.03
	height += folds * Math.min(1, height / 2)
	// 垂直夸张：水平 1 单位≈170 米、高度 1 单位≈100 米，本来就非等比；
	// 再乘一个统一系数让坡面在透视里读得出坡度，纯视觉处理。
	return Math.max(0, height) * VERTICAL_EXAGGERATION
}

export function visualBudget(options = {}) {
	const low = options.quality === 'low'
	return Object.freeze({ segments: low ? 110 : 168, trees: low ? 1200 : 2600, rocks: low ? 40 : 90,
		pixelRatio: low ? 1 : 1.5, cabins: low ? 4 : 7, shadows: !low, fps: 30 })
}

export function visualRunWidth(entity) {
	return RUN_VISUAL_WIDTH[entity?.level] || RUN_VISUAL_WIDTH.other
}

/** 把折线重采样成平滑曲线（2D Catmull-Rom），让宽面雪道不至于七个点一段一段的 */
export function resampleRoute(points, count = 40) {
	const source = (points || []).filter((point) => Array.isArray(point) && Number.isFinite(point[0]) && Number.isFinite(point[1]))
	if (source.length < 2) return source.map((point) => ({ x: point[0], z: point[1] }))
	if (source.length === 2 || count <= source.length) {
		return source.map((point) => ({ x: point[0], z: point[1] }))
	}
	const at = (index) => source[Math.max(0, Math.min(source.length - 1, index))]
	const out = []
	for (let i = 0; i < count; i++) {
		const u = (i / (count - 1)) * (source.length - 1)
		const index = Math.min(source.length - 2, Math.floor(u))
		const f = u - index
		const p0 = at(index - 1), p1 = at(index), p2 = at(index + 1), p3 = at(index + 2)
		const curve = (a, b, c, d) => 0.5 * ((2 * b) + (-a + c) * f + (2 * a - 5 * b + 4 * c - d) * f * f + (-a + 3 * b - 3 * c + d) * f * f * f)
		out.push({
			x: curve(p0[0], p1[0], p2[0], p3[0]),
			z: curve(p0[1], p1[1], p2[1], p3[1])
		})
	}
	return out
}

/** 沿折线铺一条宽面（雪道/魔毯输送带），UV 的 v 沿程累积，供压雪纹理用 */
export function ribbonData(points, width, heightAt) {
	const positions = [], uvs = [], indices = []
	let distance = 0
	points.forEach((point, i) => {
		const a = points[Math.max(0, i - 1)], b = points[Math.min(points.length - 1, i + 1)]
		const dx = b.x - a.x, dz = b.z - a.z, length = Math.hypot(dx, dz) || 1
		if (i) distance += Math.hypot(point.x - a.x, point.z - a.z)
		for (const side of [-1, 1]) {
			const x = point.x - dz / length * width / 2 * side, z = point.z + dx / length * width / 2 * side
			positions.push(x, heightAt(x, z) + 0.045, z)
			uvs.push((side + 1) / 2, distance)
		}
		if (i) { const n = i * 2; indices.push(n - 2, n - 1, n, n - 1, n + 1, n) }
	})
	return { positions, uvs, indices }
}

/**
 * 树林布点：抖动网格 + 跳过雪道走廊与山脚建筑带，野雪区按各自疏密。
 *
 * 「雪道上长不出树」是真实滑雪场最基本的观感，所以走廊判定放在最前面。
 * 返回的每个点都是视觉装饰，不代表真实林线或植被分布。
 */
export function forestPlan(profile, entities, budget) {
	const runs = (entities || []).filter((entity) => entity.type === 'run' && Array.isArray(entity.points))
	const corridors = runs.map((entity) => ({ points: resampleRoute(entity.points, 73).map(p => [p.x, p.z]), half: visualRunWidth(entity) / 2 + 0.2 }))
	const glades = (profile && profile.glades) || []
	const limit = Math.max(0, budget?.trees || 0)
	if (!limit) return []
	const spacing = Math.sqrt((33 * 24) / limit) * 1.02
	const trees = []
	let seed = 20260101
	const random = () => { seed = (seed * 1664525 + 1013904223) >>> 0; return seed / 4294967296 }
	for (let x = -16.5; x <= 16.5; x += spacing) {
		for (let z = -11.5; z <= 12.5; z += spacing) {
			const px = x + (random() - 0.5) * spacing * 0.9
			const pz = z + (random() - 0.5) * spacing * 0.9
			random()                                  // 让密度抽签与位置解耦，便于复现
			if (pz > 9.2 || pz < -8.8) continue       // 山脚建筑带、山顶平台不种树
			if (corridors.some((corridor) => distanceToPolyline(px, pz, corridor.points) < corridor.half)) continue
			let density = 0.92
			for (const glade of glades) {
				const radius = glade.r || 2
				const dx = (px - glade.x) / radius, dz = (pz - glade.z) / (radius * 0.7)
				if (dx * dx + dz * dz <= 1) { density = Math.min(density, glade.trees ?? 0.45); break }
			}
			if (random() > density) continue
			trees.push({ x: Math.round(px * 100) / 100, z: Math.round(pz * 100) / 100, size: Math.round((0.6 + random() * 0.75) * 100) / 100 })
			if (trees.length >= limit) return trees
		}
	}
	return trees
}

export function motionPhase(elapsed, index, count) { return ((elapsed * 0.018 + index / count) % 1 + 1) % 1 }

/** 索道/魔毯的设施细节：塔架、双缆、站房、吊厢（吊厢由调用方驱动动画） */
export function addLiftVisuals(THREE, group, curve, entity, heightAt, budget) {
	const steel = new THREE.MeshStandardMaterial({ color: '#526975', roughness: 0.55, metalness: 0.5 })
	const snow = new THREE.MeshStandardMaterial({ color: '#f2f7f9', roughness: 0.9 })
	const facilityPickables = []
	const carpet = /魔毯|carpet/i.test(`${entity.label || ''} ${entity.kind || ''}`)
	function box(w, h, d, material, x, y, z) {
		const mesh = new THREE.Mesh(new THREE.BoxGeometry(w, h, d), material)
		mesh.position.set(x, y, z); mesh.userData.id = entity.id; mesh.castShadow = budget.shadows
		group.add(mesh); facilityPickables.push(mesh); return mesh
	}
	const samples = curve.getPoints(48)
	if (carpet) {
		const belt = new THREE.MeshStandardMaterial({ color: '#334c59', roughness: 0.95 })
		const data = ribbonData(samples, 0.34, (x, z) => heightAt(x, z) + 0.06)
		const geo = new THREE.BufferGeometry()
		geo.setAttribute('position', new THREE.Float32BufferAttribute(data.positions, 3)); geo.setIndex(data.indices); geo.computeVertexNormals()
		const deck = new THREE.Mesh(geo, belt); deck.userData.id = entity.id; group.add(deck); facilityPickables.push(deck)
		for (const point of curve.getPoints(12)) {
			const y = heightAt(point.x, point.z)
			box(0.025, 0.26, 0.025, steel, point.x - 0.24, y + 0.13, point.z)
			box(0.025, 0.26, 0.025, steel, point.x + 0.24, y + 0.13, point.z)
			box(0.48, 0.035, 0.06, snow, point.x, y + 0.32, point.z)
		}
		return { pickables: facilityPickables, cabins: [] }
	}
	const red = new THREE.MeshStandardMaterial({ color: '#a8393e', roughness: 0.6 })
	const glass = new THREE.MeshStandardMaterial({ color: '#8db4c5', roughness: 0.22, metalness: 0.3 })
	for (const side of [-1, 1]) {
		const cable = new THREE.Line(new THREE.BufferGeometry().setFromPoints(samples.map(p => new THREE.Vector3(p.x + side * 0.14, p.y, p.z))), new THREE.LineBasicMaterial({ color: '#465a65' }))
		cable.userData.id = entity.id; group.add(cable)
	}
	// 塔架：细杆 + 小横臂。索道是配景，不能压过雪道，所以刻意画轻。
	curve.getPoints(14).forEach((p, i) => {
		if (i % 2) return
		const ground = heightAt(p.x, p.z), h = Math.max(0.15, p.y - ground)
		box(0.045, h, 0.045, steel, p.x, ground + h / 2, p.z)
		box(0.2, 0.045, 0.075, steel, p.x, p.y, p.z)
		if (i === 0 || i === 14) {
			box(0.72, 0.4, 0.86, glass, p.x, ground + 0.2, p.z)
			box(0.84, 0.08, 0.96, snow, p.x, ground + 0.44, p.z)
			box(0.34, 0.07, 0.58, red, p.x, ground + 0.09, p.z + 0.55)
		}
	})
	const cabins = []
	for (let i = 0; i < budget.cabins; i++) {
		const cabin = new THREE.Group(); cabin.userData.id = entity.id
		const body = new THREE.Mesh(new THREE.BoxGeometry(0.26, 0.24, 0.3), red)
		const windows = new THREE.Mesh(new THREE.BoxGeometry(0.195, 0.09, 0.245), glass); windows.position.y = 0.04
		const hanger = new THREE.Mesh(new THREE.BoxGeometry(0.022, 0.17, 0.022), steel); hanger.position.y = 0.21
		for (const child of [body, windows, hanger]) { child.userData.id = entity.id; cabin.add(child) }
		group.add(cabin); cabins.push(cabin)
	}
	return { pickables: [...facilityPickables, ...cabins.flatMap(c => c.children)], cabins }
}

export function terrainUnoccluded(distance, blocker, tolerance = 0.08) {
	return !blocker || distance <= blocker.distance + tolerance
}
