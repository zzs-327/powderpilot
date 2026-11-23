import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

const source = await readFile(new URL('../utils/graph.js', import.meta.url), 'utf8')
const { filterEntities, buildView, adjacent } = await import('data:text/javascript;base64,' + Buffer.from(source).toString('base64'))
const graph = {
  nodes: [
    { id: 'resort:a', type: 'resort', resort_id: 'a', label: '雪场A' },
    { id: 'run:a:1', type: 'run', resort_id: 'a', label: '银龙道' },
    { id: 'run:a:2', type: 'run', resort_id: 'a', label: '连接道' },
    { id: 'lift:a:1', type: 'lift', resort_id: 'a', label: 'A1索道' },
    { id: 'run:b:1', type: 'run', resort_id: 'b', label: '银龙道' },
    { id: 'level:beginner', type: 'level', label: '初级' }
  ],
  edges: [
    { source: 'run:a:1', target: 'level:beginner', type: 'suitable_for', resort_id: 'a' },
    { source: 'lift:a:1', target: 'level:beginner', type: 'serves', resort_id: 'a' }
  ]
}
const view = buildView(graph, 'a')

test('全部包括未分级雪道与缆车，不包含其他雪场和水平节点', () => {
  assert.deepEqual(filterEntities(view).map(n => n.id), ['run:a:1', 'run:a:2', 'lift:a:1'])
  assert.deepEqual(filterEntities(null), [])
})
test('水平筛选与名称搜索取交集', () => {
  assert.deepEqual(filterEntities(view, 'beginner', ' 银龙 ').map(n => n.id), ['run:a:1'])
  assert.equal(filterEntities(view, 'advanced', '银龙').length, 0)
  assert.equal(filterEntities(view, 'all', '不存在').length, 0)
})
test('缆车搜索忽略英文大小写', () => {
  assert.deepEqual(filterEntities(view, 'lift', 'a1').map(n => n.id), ['lift:a:1'])
})
test('相邻关系返回可导航的节点标识', () => {
  const links = adjacent(view, 'lift:a:1')
  assert.equal(links[0].otherId, 'level:beginner')
  assert.equal(view.byId.get(links[0].otherId).label, '初级')
  const levelLinks = adjacent(view, links[0].otherId)
  assert.equal(levelLinks.find(link => link.otherType === 'run').otherId, 'run:a:1')
})
