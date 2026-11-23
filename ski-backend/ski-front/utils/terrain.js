// H5 三维雪场示意：山体 + 雪道 + 索道 + 山脚设施。
//
// 这个模块只负责"画"。数据来自 graph.json（节点/关系/证据），
// 位置来自 utils/wanlong.js 的布局（人工按官方全景图读出来的相对位置）。
//
// 三条不能越过的线：
//   1. 坐标是示意，不是测绘数据；不按长度等比，也不能用于导航。
//   2. 雪道的长度、坡度、海拔一律从档案读，不在这里写死。
//   3. 加载失败必须能让上层退回二维视图，不能白屏。

import { labelBox, canPlaceLabel } from './terrain-labels.js'
import { visualHeightAt, visualBudget, visualRunWidth, ribbonData, resampleRoute, forestPlan, distanceToPolyline, motionPhase, addLiftVisuals, terrainUnoccluded } from './terrain-visuals.js'

const VERSION = '0.128.0'

//: 通用山体高度场（没做专项布局的雪场用它，保证每座雪场都画得出来）
const GENERIC_PEAKS = [
	{ x: -2, z: -5, h: 5.2, spread: 24 },
	{ x: 5, z: -2, h: 3, spread: 15 },
	{ x: -7, z: 2, h: 2.2, spread: 14 }
]

// 山体高度场：多个视觉峰叠加；h 是无量纲示意尺度，不换算成实测米数。
export const DEFAULT_PEAKS = GENERIC_PEAKS

export function heightAt(x, z, peaks = DEFAULT_PEAKS) {
	return visualHeightAt(x, z, peaks)
}

//: 难度色与页面图例保持一致（初级绿 / 中级蓝 / 高级红）
const LEVEL_COLOR = { beginner: '#12a150', intermediate: '#1f6feb', advanced: '#b3261e', other: '#7c8f99' }

function loadScript(url) {
	return new Promise((resolve, reject) => {
		const tag = document.createElement('script')
		const timer = setTimeout(() => { tag.remove(); reject(new Error('三维资源加载超时，请检查网络')) }, 15000)
		tag.src = url
		tag.onload = () => { clearTimeout(timer); resolve() }
		tag.onerror = () => { clearTimeout(timer); tag.remove(); reject(new Error('三维资源加载失败，请检查网络')) }
		document.head.appendChild(tag)
	})
}

let libraryPromise

async function library() {
	if (!libraryPromise) {
		libraryPromise = (async () => {
			if (!window.THREE) await loadScript('/static/vendor/three.min.js').catch(() => loadScript(`https://cdn.jsdelivr.net/npm/three@${VERSION}/build/three.min.js`))
			if (!window.THREE.OrbitControls) await loadScript('/static/vendor/OrbitControls.js').catch(() => loadScript(`https://cdn.jsdelivr.net/npm/three@${VERSION}/examples/js/controls/OrbitControls.js`))
			return window.THREE
		})().catch((error) => { libraryPromise = null; throw error })
	}
	return libraryPromise
}

export async function mountTerrain(container, entities, onSelect, options = {}) {
	if (!container) throw new Error('找不到三维容器')
	const profile = options.profile || { peaks: GENERIC_PEAKS, landmarks: [] }
	const shape = profile.peaks ? profile : DEFAULT_PEAKS
	const THREE = await library()
	const budget = visualBudget({ ...options, quality: options.quality || ((container.clientWidth || 360) < 600 || (window.navigator?.hardwareConcurrency || 8) <= 4 ? 'low' : 'high') })
	const motionQuery = window.matchMedia?.('(prefers-reduced-motion: reduce)')
	let reducedMotion = options.reducedMotion ?? !!motionQuery?.matches

	const owned = new Set()
	const elements = []
	const cleanups = []
	let renderer, controls, observer, frame
	let disposed = false
	const scene = new THREE.Scene()
	function cleanup() {
		if (disposed) return
		disposed = true
		cancelAnimationFrame(frame)
		for (const release of cleanups) { try { release() } catch (_) {} }
		observer?.disconnect(); controls?.dispose()
		scene.traverse(object => {
			if (object.isInstancedMesh) owned.add(object)
			if (object.geometry) owned.add(object.geometry)
			if (object.material) (Array.isArray(object.material) ? object.material : [object.material]).forEach(material => owned.add(material))
			if (object.shadow?.map) owned.add(object.shadow.map)
		})
		owned.forEach(resource => resource.dispose())
		renderer?.dispose(); renderer?.domElement.remove()
		elements.forEach(element => element.remove())
	}
	try {
	scene.background = new THREE.Color('#cfe3ec')
	scene.fog = new THREE.Fog('#cfe3ec', 46, 92)

	// 默认视角：从北侧（山脚一侧）正对山面，像官方全景图那样一眼看到整片雪道扇形
	const camera = new THREE.PerspectiveCamera(42, 1, 0.1, 260)
	camera.position.set(0.5, 17.5, 34)

	renderer = new THREE.WebGLRenderer({ antialias: true })
	renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, budget.pixelRatio))
	if (THREE.sRGBEncoding) renderer.outputEncoding = THREE.sRGBEncoding
	renderer.shadowMap.enabled = budget.shadows
	renderer.shadowMap.type = THREE.PCFSoftShadowMap
	renderer.toneMapping = THREE.ACESFilmicToneMapping
	renderer.toneMappingExposure = 1.02
	container.appendChild(renderer.domElement)

	const VIEW = { position: new THREE.Vector3(0.5, 17.5, 34), target: new THREE.Vector3(0.5, 4.4, -1.5) }
	controls = new THREE.OrbitControls(camera, renderer.domElement)
	controls.target.copy(VIEW.target)
	controls.enableDamping = !reducedMotion
	controls.enablePan = true
	controls.minDistance = 9
	controls.maxDistance = 74
	controls.minPolarAngle = 0.08 * Math.PI
	controls.maxPolarAngle = 0.495 * Math.PI

	scene.add(new THREE.HemisphereLight('#f4fbff', '#5c7f7a', 0.95))
	const sun = new THREE.DirectionalLight('#fff3dd', 1.0)
	sun.position.set(-17, 17, 7)
	sun.castShadow = true
	sun.shadow.mapSize.set(2048, 2048)
	Object.assign(sun.shadow.camera, { left: -26, right: 26, top: 26, bottom: -26, near: 1, far: 90 })
	sun.shadow.camera.updateProjectionMatrix()
	sun.shadow.bias = -0.0009
	scene.add(sun)

	// ---------------- 山体 ----------------
	// 平面比雪场范围大一圈：山体四缘自然落回地面，形成"一座山"而不是一块板。
	const terrain = new THREE.PlaneGeometry(42, 38, budget.segments, Math.round(budget.segments * 0.9))
	owned.add(terrain)
	terrain.rotateX(-Math.PI / 2)
	const position = terrain.attributes.position
	const heights = []
	let maxHeight = 0
	for (let i = 0; i < position.count; i++) {
		const height = heightAt(position.getX(i), position.getZ(i), shape)
		maxHeight = Math.max(maxHeight, height)
		position.setY(i, height)
		heights.push(height)
	}
	terrain.computeVertexNormals()
	const snowCanvas = document.createElement('canvas')
	snowCanvas.width = snowCanvas.height = 128
	const snowContext = snowCanvas.getContext('2d')
	if (!snowContext) throw new Error('无法创建雪面纹理')
	let seed = 43
	const random = () => { seed = (seed * 1664525 + 1013904223) >>> 0; return seed / 4294967296 }
	const pixels = snowContext.createImageData(128, 128)
	for (let i = 0; i < pixels.data.length; i += 4) {
		const shade = 208 + Math.floor(random() * 46)
		pixels.data.set([shade, shade, shade, 255], i)
	}
	snowContext.putImageData(pixels, 0, 0)
	const snowTexture = new THREE.CanvasTexture(snowCanvas)
	owned.add(snowTexture)
	snowTexture.wrapS = snowTexture.wrapT = THREE.RepeatWrapping
	snowTexture.repeat.set(14, 13)
	const groomCanvas = document.createElement('canvas')
	groomCanvas.width = 64
	groomCanvas.height = 64
	const groomContext = groomCanvas.getContext('2d')
	if (!groomContext) throw new Error('无法创建压雪纹理')
	groomContext.fillStyle = '#e6ecef'; groomContext.fillRect(0, 0, 64, 64)
	for (let x = 0; x < 64; x += 5) {
		groomContext.fillStyle = x % 10 ? '#d3dbdf' : '#c6d0d5'
		groomContext.fillRect(x, 0, 2, 64)
	}
	const groomTexture = new THREE.CanvasTexture(groomCanvas)
	owned.add(groomTexture)
	groomTexture.wrapS = groomTexture.wrapT = THREE.RepeatWrapping
	groomTexture.repeat.set(1, 6)
	// 雪道贴图：中央是压雪雪面（带 corduroy 细条），最外圈是**难度色包边**。
	// UV 的 u 横跨雪道宽度、v 沿雪道方向，所以包边正好落在雪道两侧——
	// 这样"这条是什么难度的雪道"一眼可辨，而雪面本身仍然是白的（不把雪染成别的颜色）。
	function runSurfaceTexture(edgeHex) {
		const canvas = document.createElement('canvas')
		canvas.width = canvas.height = 64
		const context = canvas.getContext('2d')
		if (!context) throw new Error('无法创建雪道纹理')
		const edge = [1, 3, 5].map((i) => parseInt(edgeHex.slice(i, i + 2), 16))
		for (let y = 0; y < 64; y++) {
			for (let x = 0; x < 64; x++) {
				const u = Math.min(x, 63 - x) / 31.5              // 0 = 雪道边缘, 1 = 雪道中央
				const shade = 232 + Math.round(23 * Math.min(1, u * 2.4)) - (x % 4 === 0 ? 9 : 0) - (y % 8 === 0 ? 4 : 0)
				const border = Math.max(0, 1 - u / 0.085)
				const mix = (value, target) => Math.round(value * (1 - border) + target * border)
				context.fillStyle = `rgb(${mix(shade, edge[0])},${mix(Math.min(255, shade + 2), edge[1])},${mix(Math.min(255, shade + 4), edge[2])})`
				context.fillRect(x, y, 1, 1)
			}
		}
		const texture = new THREE.CanvasTexture(canvas)
		owned.add(texture)
		texture.wrapS = texture.wrapT = THREE.RepeatWrapping
		return texture
	}
	const runTextures = {
		beginner: runSurfaceTexture(LEVEL_COLOR.beginner),
		intermediate: runSurfaceTexture(LEVEL_COLOR.intermediate),
		advanced: runSurfaceTexture(LEVEL_COLOR.advanced),
		other: runSurfaceTexture(LEVEL_COLOR.other)
	}
	// 雪面配色：越高越白，陡坡偏灰蓝（露出岩面/硬雪的观感），完全按视觉需要调
	const snowLow = new THREE.Color('#bccfd9')
	const snowHigh = new THREE.Color('#eef6fa')
	const rock = new THREE.Color('#7b8b93')
	const normal = terrain.attributes.normal
	const colorArray = []
	const vertex = new THREE.Vector3()
	for (let i = 0; i < position.count; i++) {
		const ratio = heights[i] / (maxHeight || 1)
		const color = snowLow.clone().lerp(snowHigh, Math.min(1, ratio * 0.85 + 0.15))
		vertex.set(normal.getX(i), normal.getY(i), normal.getZ(i))
		const steep = Math.max(0, 1 - vertex.y / 0.78)
		color.lerp(rock, Math.min(0.55, steep * 0.75))
		const noise = Math.sin(position.getX(i) * 1.7) * Math.cos(position.getZ(i) * 1.3) * 0.012
		colorArray.push(color.r + noise, color.g + noise, color.b + noise)
	}
	terrain.setAttribute('color', new THREE.Float32BufferAttribute(colorArray, 3))
	const mountain = new THREE.Mesh(terrain, new THREE.MeshStandardMaterial({ vertexColors: true, roughness: 0.94, bumpMap: snowTexture, bumpScale: 0.035 }))
	mountain.receiveShadow = true
	scene.add(mountain)

	// 树林：抖动网格布点，雪道走廊、山脚建筑带与山顶平台都留空。
	// 「雪道上长不出树」是真实滑雪场最基本的观感，所以走廊判定由 forestPlan 统一负责。
	const trees = forestPlan(profile, entities, budget)
	const foliage = new THREE.InstancedMesh(new THREE.ConeGeometry(0.16, 1.0, 7), new THREE.MeshStandardMaterial({ color: '#1f3b35', roughness: 1 }), Math.max(1, trees.length))
	const caps = new THREE.InstancedMesh(new THREE.ConeGeometry(0.09, 0.3, 7), new THREE.MeshStandardMaterial({ color: '#dfeff3', roughness: 0.95 }), Math.max(1, trees.length))
	const dummy = new THREE.Object3D()
	trees.forEach((tree, index) => {
		const y = heightAt(tree.x, tree.z, shape)
		dummy.scale.set(tree.size * 0.92, tree.size, tree.size * 0.92)
		dummy.position.set(tree.x, y + 0.42 * tree.size, tree.z)
		dummy.updateMatrix(); foliage.setMatrixAt(index, dummy.matrix)
		dummy.position.y = y + 0.74 * tree.size
		dummy.updateMatrix(); caps.setMatrixAt(index, dummy.matrix)
	})
	foliage.count = trees.length
	caps.count = trees.length
	foliage.castShadow = budget.shadows
	scene.add(foliage, caps)
	// 岩石只落在雪道之外的陡坡上，用来点出岩面
	const rockSpots = []
	for (let i = 0; i < budget.rocks * 3 && rockSpots.length < budget.rocks; i++) {
		const x = random() * 33 - 16.5, z = random() * 22 - 11
		if (z > 8) continue
		if (entities.some((entity) => entity.type === 'run' && distanceToPolyline(x, z, resampleRoute(entity.points || [], 73).map(p => [p.x, p.z])) < visualRunWidth(entity) / 2 + 0.3)) continue
		rockSpots.push({ x, z, size: 0.4 + random() * 0.9 })
	}
	const rocks = new THREE.InstancedMesh(new THREE.DodecahedronGeometry(0.18, 0), new THREE.MeshStandardMaterial({ color: '#7d8f96', roughness: 1 }), Math.max(1, rockSpots.length))
	rockSpots.forEach((spot, index) => {
		dummy.position.set(spot.x, heightAt(spot.x, spot.z, shape) + 0.05, spot.z)
		dummy.scale.set(spot.size, spot.size * 0.6, spot.size)
		dummy.rotation.y = random() * Math.PI
		dummy.updateMatrix(); rocks.setMatrixAt(index, dummy.matrix)
	})
	rocks.count = rockSpots.length
	scene.add(rocks)

	// ---------------- 雪道与索道 ----------------
	const pickables = []
	const labels = []
	const groups = new Map()
	const movingLifts = []

	function curveOf(points, lift) {
		const lifted = resampleRoute(points, 73).map((point) =>
			new THREE.Vector3(point.x, heightAt(point.x, point.z, shape) + (lift ? 0.7 : 0.14), point.z))
		const path = new THREE.CurvePath()
		for (let i = 1; i < lifted.length; i++) path.add(new THREE.LineCurve3(lifted[i - 1], lifted[i]))
		return path
	}

	entities.forEach((entity) => {
		const lift = entity.type === 'lift'
		const points = entity.points
		if (!points || points.length < 2) return
		const color = lift ? '#e5a94f' : LEVEL_COLOR[entity.level] || LEVEL_COLOR.other
		const group = new THREE.Group()
		group.userData = { id: entity.id, kind: entity.type, level: entity.level || '' }
		groups.set(entity.id, group)
		const carpet = lift && /魔毯|carpet/i.test(`${entity.label || ''} ${entity.kind || ''}`)
		const curve = curveOf(points, lift && !carpet)
		let pickable
		if (lift) {
			const visuals = addLiftVisuals(THREE, group, curve, entity, (x, z) => heightAt(x, z, shape), budget)
			pickables.push(...visuals.pickables)
			if (visuals.cabins.length) movingLifts.push({ group, curve, cabins: visuals.cabins })
			pickable = new THREE.Line(new THREE.BufferGeometry().setFromPoints(curve.getPoints(48)), new THREE.LineBasicMaterial({ color }))
			group.add(pickable)
		} else {
			// 雪道是主角：宽压雪面 + 难度色中心线。
			// 宽度只按难度分级，是**视觉示意**，不代表实测雪道宽度。
			const data = ribbonData(resampleRoute(points, 73), visualRunWidth(entity), (x, z) => heightAt(x, z, shape))
			const geometry = new THREE.BufferGeometry()
			geometry.setAttribute('position', new THREE.Float32BufferAttribute(data.positions, 3))
			geometry.setAttribute('uv', new THREE.Float32BufferAttribute(data.uvs, 2))
			geometry.setIndex(data.indices); geometry.computeVertexNormals()
			const tint = new THREE.Color('#ffffff').lerp(new THREE.Color(color), 0.12)
			const material = new THREE.MeshStandardMaterial({ map: runTextures[entity.level] || runTextures.other, color: tint, roughness: 0.82, bumpMap: groomTexture, bumpScale: 0.01, side: THREE.DoubleSide })
			pickable = new THREE.Mesh(geometry, material)
			const guide = new THREE.Mesh(new THREE.TubeGeometry(curve, 72, 0.05, 5, false), new THREE.MeshStandardMaterial({ color, emissive: new THREE.Color(color), emissiveIntensity: 0.35, roughness: 0.7 }))
			guide.userData.id = entity.id; group.add(guide)
			group.add(pickable)
		}
		pickable.userData.id = entity.id
		const marker = new THREE.Mesh(new THREE.SphereGeometry(lift ? 0.14 : 0.12, 10, 8), new THREE.MeshStandardMaterial({ color, emissive: new THREE.Color(color), emissiveIntensity: 0.4 }))
		marker.position.copy(curve.getPoint(1))
		marker.userData.id = entity.id
		group.add(marker)
		pickables.push(pickable, marker)
		scene.add(group)

		const anchor = curve.getPoint(lift ? 0.62 : entity.level === 'beginner' ? 0.28 : 0.42)
		const label = document.createElement('button')
		label.className = 'terrain-label' + (lift ? ' is-lift' : '')
		label.type = 'button'
		label.textContent = entity.label
		label.style.setProperty('--marker-color', color)
		label.title = '点击查看参数与来源'
		label.addEventListener('click', () => onSelect(entity.id))
		elements.push(label)
		container.appendChild(label)
		labels.push({ label, anchor, id: entity.id, priority: lift ? 1 : 0 })
	})

	// ---------------- 山脚与山顶设施 ----------------
	;(profile.landmarks || []).forEach((landmark) => {
		const color = landmark.kind === 'summit' ? '#f2f8ff' : landmark.kind === 'gate' ? '#8fd0c4' : landmark.kind === 'rest' ? '#e8b46a' : '#c9d4dc'
		const geometry = landmark.kind === 'summit' ? new THREE.ConeGeometry(0.42, 0.85, 12) : new THREE.BoxGeometry(0.75, 0.5, 0.75)
		const mesh = new THREE.Mesh(geometry, new THREE.MeshStandardMaterial({ color, roughness: 0.7 }))
		const y = heightAt(landmark.x, landmark.z, shape)
		mesh.position.set(landmark.x, y + (landmark.kind === 'summit' ? 0.55 : 0.3), landmark.z)
		scene.add(mesh)
		if (landmark.kind !== 'summit') {
			const roof = new THREE.Mesh(new THREE.BoxGeometry(0.9, 0.09, 0.9), new THREE.MeshStandardMaterial({ color: '#f1f7fa', roughness: 0.95 }))
			roof.position.set(landmark.x, y + 0.59, landmark.z); scene.add(roof)
			const glazing = new THREE.Mesh(new THREE.BoxGeometry(0.55, 0.19, 0.02), new THREE.MeshStandardMaterial({ color: '#557887', roughness: 0.35 }))
			glazing.position.set(landmark.x, y + 0.34, landmark.z + 0.385); scene.add(glazing)
			const terrace = new THREE.Mesh(new THREE.BoxGeometry(1.3, 0.06, 1.2), new THREE.MeshStandardMaterial({ color: '#97aeb6', roughness: 1 }))
			terrace.position.set(landmark.x, y + 0.035, landmark.z + 0.35); scene.add(terrace)
		}
		const label = document.createElement('span')
		label.className = 'terrain-landmark ' + landmark.kind
		label.textContent = landmark.label
		container.appendChild(label)
		labels.push({ label, anchor: new THREE.Vector3(landmark.x, y + 1.1, landmark.z), id: '', priority: 2 })
	})

	if (profile.summit_elevation_m) {
		const crest = (profile.landmarks || []).find((item) => item.kind === 'summit') || { x: 0, z: -10 }
		const summit = new THREE.Vector3(crest.x, heightAt(crest.x, crest.z, shape) + 0.6, crest.z)
		const label = document.createElement('span')
		label.className = 'terrain-summit'
		label.textContent = `山顶 ${profile.summit_elevation_m} 米`
		container.appendChild(label)
		labels.push({ label, anchor: summit, id: '', priority: 3 })
	}

	// ---------------- 交互 ----------------
	const raycaster = new THREE.Raycaster()
	raycaster.params.Line.threshold = 0.22
	let pointerStart = null
	const onDown = (event) => { pointerStart = [event.clientX, event.clientY] }
	const onUp = (event) => {
		if (!pointerStart || Math.hypot(event.clientX - pointerStart[0], event.clientY - pointerStart[1]) > 6) return
		const rect = renderer.domElement.getBoundingClientRect()
		raycaster.setFromCamera(new THREE.Vector2(
			((event.clientX - rect.left) / rect.width) * 2 - 1,
			-((event.clientY - rect.top) / rect.height) * 2 + 1
		), camera)
		const hit = raycaster.intersectObjects(pickables.filter((item) => {
			let object = item
			while (object) { if (!object.visible) return false; object = object.parent }
			return true
		}), false)[0]
		const occluder = raycaster.intersectObject(mountain, false)[0]
		if (hit && terrainUnoccluded(hit.distance, occluder)) onSelect(hit.object.userData.id)
	}
	renderer.domElement.addEventListener('pointerdown', onDown)
	renderer.domElement.addEventListener('pointerup', onUp)
	cleanups.push(() => { renderer.domElement.removeEventListener('pointerdown', onDown); renderer.domElement.removeEventListener('pointerup', onUp) })

	function resize() {
		const width = container.clientWidth || 360
		const height = container.clientHeight || 430
		renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, budget.pixelRatio, 1920 / width, 1080 / height))
		camera.aspect = width / height
		camera.updateProjectionMatrix()
		renderer.setSize(width, height)
	}
	observer = new ResizeObserver(resize)
	observer.observe(container)
	resize()

	let currentFilter = options.filter || 'all'
	let visibleIds = null
	let highlighted = ''

	function applyVisibility() {
		groups.forEach((group, id) => {
			const kind = group.userData.kind
			const level = group.userData.level
			group.visible = visibleIds ? visibleIds.has(id) : (currentFilter === 'all'
				|| (currentFilter === 'lift' && kind === 'lift')
				|| (currentFilter !== 'lift' && kind === 'run' && level === currentFilter))
			const label = labels.find((item) => item.id === id)
			if (label && !group.visible) label.label.style.display = 'none'
		})
	}

	function applyHighlight() {
		labels.forEach((item) => { if (item.id) { item.label.classList.toggle('is-selected', item.id === highlighted); item.label.setAttribute('aria-pressed', String(item.id === highlighted)) } })
		groups.forEach((group, id) => {
			group.traverse((object) => {
				if (!object.material || !object.material.emissive) return
				const active = id === highlighted
				object.material.emissiveIntensity = active ? 1.1 : 0.25
				if (object.material.opacity !== undefined && object.material.transparent) object.material.opacity = active ? 1 : 0.92
			})
		})
	}

	applyVisibility()

	const projected = new THREE.Vector3()
	const placed = []
	let paused = false

	function placeLabels() {
		placed.length = 0
		// 每帧重新判定，不能把上帧因遮挡隐藏的标签永久排除。
		labels.forEach((item) => { item.label.style.display = 'none' })
		const ordered = labels
			.filter((item) => !item.id || groups.get(item.id)?.visible)
			.slice()
			.sort((a, b) => Number(b.id === highlighted) - Number(a.id === highlighted)
				|| (a.priority - b.priority) || camera.position.distanceTo(a.anchor) - camera.position.distanceTo(b.anchor))
		const width = container.clientWidth || 360
		const height = container.clientHeight || 430
		for (const item of ordered) {
			projected.copy(item.anchor).project(camera)
			const visible = projected.z < 1 && Math.abs(projected.x) < 0.97 && Math.abs(projected.y) < 0.97
			if (!visible) { item.label.style.display = 'none'; continue }
			const direction = new THREE.Vector3().subVectors(item.anchor, camera.position)
			const distance = direction.length()
			raycaster.set(camera.position, direction.normalize())
			const blocker = raycaster.intersectObject(mountain, false)[0]
			if (!terrainUnoccluded(distance, blocker, 0.12)) continue
			const x = (projected.x + 1) * width / 2
			const y = (-projected.y + 1) * height / 2
			item.label.style.display = ''
			const box = labelBox(x, y, item.label.offsetWidth, item.label.offsetHeight)
			if (!canPlaceLabel(box, placed, width, height)) {
				item.label.style.display = 'none'
				continue
			}
			placed.push(box)
			item.label.style.display = ''
			item.label.style.left = `${x}px`
			item.label.style.top = `${y}px`
		}
	}

	let lastFrame = 0
	let elapsed = 0
	let lastTime = 0
	const onMotionChange = () => {
		reducedMotion = options.reducedMotion ?? !!motionQuery?.matches
		controls.enableDamping = !reducedMotion
	}
	motionQuery?.addEventListener?.('change', onMotionChange)
	cleanups.push(() => motionQuery?.removeEventListener?.('change', onMotionChange))
	function updateCabins() {
		movingLifts.forEach(({ group, curve, cabins }) => {
			if (!group.visible) return
			cabins.forEach((cabin, index) => {
				const t = motionPhase(elapsed, index, cabins.length)
				cabin.position.copy(curve.getPoint(t)); cabin.position.y -= 0.27
				const tangent = curve.getTangent(t)
				cabin.rotation.y = Math.atan2(tangent.x, tangent.z)
			})
		})
	}
	updateCabins()
	function animate(time = 0) {
		if (disposed || paused) return
		frame = requestAnimationFrame(animate)
		if (document.hidden) { lastTime = 0; return }
		if (time - lastFrame < 1000 / budget.fps && time !== 0) return
		const delta = lastTime ? Math.min((time - lastTime) / 1000, 0.1) : 0
		lastTime = time; lastFrame = time
		if (!reducedMotion && !document.hidden) { elapsed += delta; updateCabins() }
		controls.update()
		renderer.render(scene, camera)
		placeLabels()
	}
	animate()

	return {
		pause() { paused = true; cancelAnimationFrame(frame) },
		resume() { if (!disposed && paused) { paused = false; lastTime = 0; resize(); animate() } },
		performance: budget,
		focus(id) {
			const group = groups.get(id)
			if (!group || !group.visible) return false
			const bounds = new THREE.Box3().setFromObject(group)
			const center = bounds.getCenter(new THREE.Vector3())
			controls.target.copy(center)
			camera.position.copy(center).add(new THREE.Vector3(5.5, 4.5, 8.5))
			highlighted = id; applyHighlight(); controls.update()
			return true
		},
		reset() {
			camera.position.copy(VIEW.position)
			controls.target.copy(VIEW.target)
			controls.update()
		},
		setFilter(mode) {
			currentFilter = mode || 'all'
			visibleIds = null
			applyVisibility()
		},
		setVisibleIds(ids) {
			visibleIds = new Set(ids)
			applyVisibility()
		},
		highlight(id) {
			highlighted = id || ''
			applyHighlight()
		},
		dispose: cleanup
	}
	} catch (error) {
		cleanup()
		throw error
	}
}
