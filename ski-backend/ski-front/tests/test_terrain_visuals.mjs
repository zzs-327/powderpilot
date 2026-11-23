import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
const source = await readFile(new URL('../utils/terrain-visuals.js', import.meta.url), 'utf8')
const visual = await import('data:text/javascript;base64,' + Buffer.from(source).toString('base64'))
const peaks = [{ x: 0, z: -2, h: 5, spread: 20 }]

test('高度场可重复、非负且有限，不改业务数据', () => {
  const before = JSON.stringify(peaks)
  for (let x = -17; x <= 17; x += 0.7) for (let z = -17; z <= 17; z += 0.7) {
    const h = visual.visualHeightAt(x, z, peaks)
    assert.ok(Number.isFinite(h) && h >= 0)
    assert.equal(h, visual.visualHeightAt(x, z, peaks))
  }
  assert.equal(JSON.stringify(peaks), before)
  assert.equal(visual.visualHeightAt(0, 0, []), 0)
})
test('预算硬上限与低配降级不接受任意数量注入', () => {
  const high = visual.visualBudget({ trees: 999999 })
  const low = visual.visualBudget({ quality: 'low' })
  assert.equal(high.trees, 2600); assert.equal(high.pixelRatio, 1.5)
  assert.ok(low.segments < high.segments && low.cabins < high.cabins)
  assert.equal(low.shadows, false); assert.equal(low.fps, 30)
  assert.ok(Object.isFrozen(high))
})
test('宽面贴合高度场，UV沿程累积、法线朝上，无长度或宽度业务猜测', () => {
  const data = visual.ribbonData([{x: 0, z: 0}, {x: 2, z: 0}, {x: 2, z: 2}], 0.4, () => 3)
  assert.equal(data.positions.length, 18); assert.equal(data.indices.length, 12)
  assert.equal(data.positions[1], 3.045)
  assert.ok(Math.abs(data.positions[2] - data.positions[5]) <= 0.4 + 1e-9)
  assert.deepEqual(data.uvs.filter((_, i) => i % 4 === 1), [0, 2, 4])
  const flat = visual.ribbonData([{x: 0, z: 0}, {x: 2, z: 0}], 0.4, () => 0)
  const [a,b,c] = flat.indices.map(i => flat.positions.slice(i*3,i*3+3))
  assert.ok((b[2]-a[2])*(c[0]-a[0])-(b[0]-a[0])*(c[2]-a[2]) > 0)
  assert.equal(visual.visualRunWidth({ level: 'beginner', width_m: 300 }), 1.05)
  assert.equal(visual.visualRunWidth({ level: 'advanced' }), 0.62)
})
test('重复控制点不会导致雪道NaN，动画相位稳定有界', () => {
  const data = visual.ribbonData([{x: 1,z: 1},{x: 1,z: 1}], 0.3, () => 0)
  assert.ok(data.positions.every(Number.isFinite))
  for (const time of [0, 5, 10000]) for (let i = 0; i < 7; i++) assert.ok(visual.motionPhase(time,i,7) >= 0 && visual.motionPhase(time,i,7) < 1)
})
class Object3D {
  constructor() { this.children=[]; this.userData={}; this.position={set(){}} }
  add(child) { child.parent=this; this.children.push(child) }
}
class Geometry {
  setAttribute() { return this } setIndex() { return this } computeVertexNormals() {} setFromPoints() { return this }
}
const THREE = { MeshStandardMaterial: class {}, BoxGeometry: Geometry, BufferGeometry: Geometry, Float32BufferAttribute: class {}, LineBasicMaterial: class {}, Group: Object3D,
 Mesh: class extends Object3D {}, Line: class extends Object3D {}, Vector3: class { constructor(x,y,z) { Object.assign(this,{x,y,z}) } } }
const curve = { getPoints: count => Array.from({length:count+1},(_,i)=>({x: i/count*4,y:1,z:0})) }
test('索道站房支架、双缆线、吊厢受预算限制且拾取保留实体id', () => {
  const group = new THREE.Group()
  const output = visual.addLiftVisuals(THREE,group,curve,{id:'lift',label:'索道'},()=>0,visual.visualBudget({quality:'low'}))
  assert.equal(output.cabins.length,4)
  assert.ok(output.pickables.length > 12)
  assert.ok(output.pickables.every(child=>child.userData.id==='lift' && (child.parent===group || child.parent.parent===group)))
  assert.ok(group.children.length < 40)
})
test('魔毯绘制宽面输送带与护栏而不冒充吊厢索道', () => {
  const group = new THREE.Group()
  const output = visual.addLiftVisuals(THREE,group,curve,{id:'carpet',label:'魔毯'},()=>0,visual.visualBudget())
  assert.equal(output.cabins.length,0); assert.ok(output.pickables.length > 1)
  assert.equal(output.pickables[0].userData.id,'carpet')
})
test('山体剖面：山顶高于山脚、西翼低于中部、垂直夸张只作用于视觉', () => {
  const profile = { peaks: [{ x: 0.2, z: -9.8, h: 5.6, spread: 34 }], ridges: [[[-3.6, -9.6], [2.8, -9.6]]], gullies: [{ x: 2.2, z: -1, depth: 0.5, spread: 24 }] }
  for (const x of [-10, -4, 0, 4, 10]) {
    assert.ok(visual.visualHeightAt(x, -6, profile) > visual.visualHeightAt(x, 0, profile), '上段应高于中段 x=' + x)
    assert.ok(visual.visualHeightAt(x, 0, profile) > visual.visualHeightAt(x, 6, profile), '中段应高于下段 x=' + x)
  }
  assert.ok(visual.visualHeightAt(-12, 0, profile) < visual.visualHeightAt(0, 0, profile), '西翼应低于中部')
  assert.ok(visual.visualHeightAt(0, 12, profile) < 0.2, '山脚平台接近地面')
  assert.ok(visual.VERTICAL_EXAGGERATION > 1)
  assert.equal(visual.visualHeightAt(0, 0, profile), visual.visualHeightAt(0, 0, profile))
})
test('树林：雪道走廊与山脚建筑带不长树，数量受预算约束且可复现', () => {
  const entities = [
    { type: 'run', level: 'beginner', points: [[-4, 9], [-4, -8]] },
    { type: 'run', level: 'advanced', points: [[3, 9], [3, -8]] },
    { type: 'lift', level: '', points: [[0, 9], [0, -8]] }
  ]
  const profile = { peaks: [{ x: 0, z: -9, h: 5.6, spread: 34 }], glades: [{ label: 'E', x: 0, z: 0, r: 2, trees: 0.1 }] }
  const budget = visual.visualBudget()
  const trees = visual.forestPlan(profile, entities, budget)
  assert.ok(trees.length > 200 && trees.length <= budget.trees)
  assert.deepEqual(trees, visual.forestPlan(profile, entities, budget), '同参数结果必须可复现')
  for (const tree of trees) {
    assert.ok(tree.z <= 9.2 && tree.z >= -8.8, '山脚建筑带与山顶平台不应有树')
    for (const entity of entities.filter(e => e.type === 'run')) {
      assert.ok(visual.distanceToPolyline(tree.x, tree.z, entity.points) >= visual.visualRunWidth(entity) / 2, '雪道上不能有树')
    }
  }
  assert.equal(visual.forestPlan(profile, entities, { trees: 0 }).length, 0)
})
test('万龙布局：32 条档案雪道都有折线，宽度常量两处一致', async () => {
  const layout = await import('data:text/javascript;base64,' + Buffer.from(await readFile(new URL('../utils/wanlong.js', import.meta.url), 'utf8')).toString('base64'))
  const names = ['长龙道','短龙道','卧龙道','娇龙道','游龙道','玉龙道','飞龙道','小龙道','酷龙道','银龙道','雨龙道','紫龙道','五彩龙道','中华龙道','金龙道','恐龙道','翔龙道','炫龙道','猛龙道','大奔头','威龙道','腾龙道','逸龙道','海龙道','雾龙道','霜龙道','雪龙道','风龙道','云龙道','绿龙道','赤龙道','黄龙道']
  assert.equal(names.length, 32)
  for (const name of names) {
    const trail = layout.WANLONG_PROFILE.trails[name]
    assert.ok(trail, name + ' 缺少布局折线')
    assert.ok(trail.points.length >= 3, name + ' 折线过短')
    assert.ok(trail.points.every(p => Number.isFinite(p[0]) && Number.isFinite(p[1])), name + ' 坐标非法')
  }
  assert.deepEqual({ ...layout.RUN_WIDTH }, { ...visual.RUN_VISUAL_WIDTH })
  for (const [name, run] of Object.entries(layout.WANLONG_PROFILE.runs)) {
    assert.ok(['beginner','intermediate','advanced'].includes(run.level), name + ' 难度取值异常')
  }
})
test('折线重采样与点线距离：平滑、有限、退化输入不炸', () => {
  const points = [[0, 0], [1, 2], [3, 1], [4, 4]]
  const smooth = visual.resampleRoute(points, 25)
  assert.equal(smooth.length, 25)
  assert.ok(smooth.every(p => Number.isFinite(p.x) && Number.isFinite(p.z)))
  assert.deepEqual(visual.resampleRoute([[1, 1]], 8), [{ x: 1, z: 1 }])
  assert.equal(visual.resampleRoute([], 8).length, 0)
  assert.equal(visual.distanceToPolyline(0, 1, [[0, 0], [0, 4]]), 0)
  assert.equal(visual.distanceToPolyline(2, 1, []), Infinity)
})
test('地形入口可解析且保留旧生命周期和交互API', async () => {
  let terrain = await readFile(new URL('../utils/terrain.js', import.meta.url),'utf8')
  const labels = await readFile(new URL('../utils/terrain-labels.js', import.meta.url),'utf8')
  terrain = terrain.replace('./terrain-visuals.js','data:text/javascript;base64,'+Buffer.from(source).toString('base64'))
    .replace('./terrain-labels.js','data:text/javascript;base64,'+Buffer.from(labels).toString('base64'))
  const module = await import('data:text/javascript;base64,'+Buffer.from(terrain).toString('base64'))
  assert.equal(typeof module.mountTerrain,'function'); assert.equal(module.heightAt(0,0,[]),0)
  for (const name of ['pause','resume','reset','setFilter','setVisibleIds','highlight','dispose','focus']) assert.ok(terrain.includes(name+'('))
  assert.ok(terrain.includes('prefers-reduced-motion: reduce'))
})

test('离线Three r128真实几何：雪道法线和设施包围盒有效', async () => {
  const { runInNewContext } = await import('node:vm')
  const vendor = await readFile(new URL('../static/vendor/three.min.js',import.meta.url),'utf8')
  const context = {}
  runInNewContext(vendor, context)
  const T = context.THREE
  assert.equal(T.REVISION,'128')
  const curve = new T.CatmullRomCurve3([new T.Vector3(0,1,0),new T.Vector3(0.5,2,-3),new T.Vector3(1,3,-5)])
  for (const label of ['索道','魔毯']) {
    const group = new T.Group()
    const output = visual.addLiftVisuals(T,group,curve,{id:label,label},()=>0,visual.visualBudget())
    const bounds = new T.Box3().setFromObject(group)
    assert.ok(!bounds.isEmpty() && Number.isFinite(bounds.max.y))
    assert.ok(group.children.length <= 50)
    assert.ok(output.pickables.every(mesh=>mesh.userData.id===label))
    const resources = new Set()
    group.traverse(mesh=>{ if(mesh.geometry)resources.add(mesh.geometry); if(mesh.material)resources.add(mesh.material) })
    resources.forEach(resource=>resource.dispose())
  }
})

test('真实Three场景装配：过滤、聚焦、暂停恢复、减弱动画与幂等释放', async () => {
  const { runInNewContext } = await import('node:vm')
  const context = {}; runInNewContext(await readFile(new URL('../static/vendor/three.min.js',import.meta.url),'utf8'),context)
  const T = context.THREE
  const created = []
  function element() { return { style:{setProperty(){}},classList:{toggle(){}},offsetWidth:80,offsetHeight:32,addEventListener(){},removeEventListener(){},setAttribute(){},remove(){this.removed=true},getBoundingClientRect(){return {left:0,top:0,width:360,height:430}},getContext(){return {createImageData:(w,h)=>({data:new Uint8ClampedArray(w*h*4)}),putImageData(){},fillRect(){}}} } }
  let renderer, controls, frames=0, cancelled=0, removedMotion=0, failure='', instanceDisposals=0
  const originalInstanceDispose=T.InstancedMesh.prototype.dispose
  T.InstancedMesh.prototype.dispose=function(){instanceDisposals++;originalInstanceDispose.call(this)}
  T.WebGLRenderer = class {
    constructor(){renderer=this;this.domElement=element();this.shadowMap={};this.renders=0}
    setPixelRatio(value){this.ratio=value} setSize(){} render(scene){this.scene=scene;this.renders++;if(failure==='render')throw new Error('render failed')} dispose(){this.disposed=true}
  }
  T.OrbitControls = class { constructor(){if(failure==='controls')throw new Error('controls failed');controls=this;this.target=new T.Vector3()} update(){} dispose(){this.disposed=true} }
  const names = ['window','document','ResizeObserver','requestAnimationFrame','cancelAnimationFrame']
  const prior = names.map(name=>globalThis[name])
  Object.assign(globalThis, { window:{THREE:T,devicePixelRatio:3,matchMedia:()=>({matches:true,addEventListener(){},removeEventListener(){removedMotion++}})},
    document:{hidden:false,createElement(){const e=element();if(failure==='context')e.getContext=()=>null;created.push(e);return e}},ResizeObserver:class {constructor(){if(failure==='observer')throw new Error('observer failed')} observe(){} disconnect(){this.disconnected=true}},
    requestAnimationFrame(){frames++;return frames},cancelAnimationFrame(){cancelled++} })
  try {
    let terrain = await readFile(new URL('../utils/terrain.js',import.meta.url),'utf8')
    const labels = await readFile(new URL('../utils/terrain-labels.js',import.meta.url),'utf8')
    terrain=terrain.replace('./terrain-visuals.js','data:text/javascript;base64,'+Buffer.from(source).toString('base64'))
      .replace('./terrain-labels.js','data:text/javascript;base64,'+Buffer.from(labels).toString('base64'))
    const {mountTerrain}=await import('data:text/javascript;base64,'+Buffer.from(terrain+'\n//integration').toString('base64'))
    const api=await mountTerrain({clientWidth:360,clientHeight:430,appendChild(){}},[
      {id:'run',type:'run',level:'beginner',label:'雪道',points:[[0,0],[1,-4]]},
      {id:'lift',type:'lift',label:'索道',points:[[2,0],[3,-4]]}],()=>{}, {})
    assert.equal(controls.enableDamping,false)
    assert.equal(renderer.shadowMap.enabled,false); assert.equal(renderer.ratio,1)
    assert.equal(api.focus('run'),true); assert.equal(api.focus('missing'),false)
    api.setVisibleIds(['lift']); assert.equal(api.focus('run'),false); assert.equal(api.focus('lift'),true)
    api.setFilter('beginner'); assert.equal(api.focus('run'),true); assert.equal(api.focus('lift'),false)
    api.highlight('run'); api.reset(); api.pause(); const old=frames; api.resume(); assert.ok(frames>old)
    const textures = new Set(); renderer.scene.traverse(mesh=>{if(mesh.material?.bumpMap)textures.add(mesh.material.bumpMap)})
    let released=0; textures.forEach(texture=>texture.addEventListener('dispose',()=>released++))
    api.dispose(); api.dispose(); api.resume()
    assert.equal(renderer.disposed,true); assert.equal(controls.disposed,true); assert.equal(removedMotion,1)
    assert.equal(released,2); assert.equal(instanceDisposals,3); assert.ok(cancelled>=2)
    for (const stage of ['controls','context','observer','render']) {
      failure=stage
      await assert.rejects(mountTerrain({clientWidth:360,clientHeight:430,appendChild(){}},[],()=>{}), /failed|纹理/)
      assert.equal(renderer.disposed,true)
      assert.equal(renderer.domElement.removed,true)
    }
    assert.ok(created.filter(e=>e.className).every(e=>e.removed))
  } finally { names.forEach((name,i)=>{if(prior[i]===undefined)delete globalThis[name];else globalThis[name]=prior[i]}) }
})

test('山体遮挡距离用于标签与拾取，不允许背山实体穿透', () => {
  assert.equal(visual.terrainUnoccluded(8,{distance:4}),false)
  assert.equal(visual.terrainUnoccluded(3,{distance:4}),true)
  assert.equal(visual.terrainUnoccluded(4.04,{distance:4}),true)
  assert.equal(visual.terrainUnoccluded(10,null),true)
})

test('索道档案简称命中专项布局，未知编号仍保留通用回退', async () => {
  const layout = await import('data:text/javascript;base64,' + Buffer.from(await readFile(new URL('../utils/wanlong.js', import.meta.url), 'utf8')).toString('base64'))
  for (const n of [1,2,4,5,6,7]) {
    for (const label of [n+'索', n+'号索道', n+'索道']) {
      assert.deepEqual(layout.routeFor(layout.WANLONG_PROFILE,{ type:'lift',label },0).points,layout.WANLONG_PROFILE.lifts[n+'号索道'].points)
    }
  }
  assert.ok(layout.routeFor(layout.WANLONG_PROFILE,{type:'lift',label:'99索'},0).points.length >= 2)
})
test('弯道树林避让使用与雪道宽面相同的平滑中心线', () => {
  const entity={type:'run',level:'beginner',points:[[-10,7],[-2,3],[-10,-2],[4,-7]]}
  const sampled=visual.resampleRoute(entity.points,73)
  const line=sampled.map(p=>[p.x,p.z])
  const trees=visual.forestPlan({},[entity],visual.visualBudget())
  assert.ok(trees.length>0)
  for(const tree of trees) assert.ok(visual.distanceToPolyline(tree.x,tree.z,line)>=visual.visualRunWidth(entity)/2+0.18)
  const ribbon=visual.ribbonData(sampled,visual.visualRunWidth(entity),()=>0)
  assert.equal(ribbon.positions.length,sampled.length*6)
})
