import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
const source = await readFile(new URL('../utils/terrain-labels.js', import.meta.url), 'utf8')
const { labelBox, canPlaceLabel } = await import('data:text/javascript;base64,' + Buffer.from(source).toString('base64'))

test('按真实尺寸计算长标签碰撞，不使用固定宽度', () => {
  const long = labelBox(100, 100, 110, 32)
  assert.equal(canPlaceLabel(labelBox(185, 100, 80, 32), [long], 400, 430), false)
  assert.equal(canPlaceLabel(labelBox(220, 100, 80, 32), [long], 400, 430), true)
})
test('手机44像素点击区域不得重叠', () => {
  const first = labelBox(120, 100, 90, 44)
  assert.equal(canPlaceLabel(labelBox(120, 143, 90, 44), [first], 360, 430), false)
  assert.equal(canPlaceLabel(labelBox(120, 150, 90, 44), [first], 360, 430), true)
})
test('标签不可被屏幕边缘截断，桌面和手机均适用', () => {
  for (const width of [320, 360, 1280]) {
    assert.equal(canPlaceLabel(labelBox(8, 100, 96, 44), [], width, 430), false)
    assert.equal(canPlaceLabel(labelBox(width - 8, 100, 96, 44), [], width, 430), false)
    assert.equal(canPlaceLabel(labelBox(width / 2, 100, 96, 44), [], width, 430), true)
  }
})
