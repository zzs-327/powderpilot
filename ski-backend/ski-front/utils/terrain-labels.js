// 标签碰撞与边界判断独立于 WebGL，便于离线验证不同屏幕尺寸。
export function labelBox(x, y, width, height) {
	return { x1: x - width / 2, y1: y - height / 2, x2: x + width / 2, y2: y + height / 2 }
}

export function canPlaceLabel(box, placed, width, height, gap = 4) {
	if (box.x1 < gap || box.y1 < gap || box.x2 > width - gap || box.y2 > height - gap) return false
	return !placed.some((other) => box.x1 < other.x2 + gap && box.x2 + gap > other.x1
		&& box.y1 < other.y2 + gap && box.y2 + gap > other.y1)
}
