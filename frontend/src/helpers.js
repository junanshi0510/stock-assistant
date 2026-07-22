// 方向 -> 样式类
export function dirClass(d) {
  if (d === '看涨' || d === '技术偏强') return 'up'
  if (d === '看跌' || d === '技术偏弱') return 'down'
  return 'neutral'
}

// 分数 -> 颜色(红涨绿跌,中间黄)
export function scoreColor(score) {
  if (score >= 65) return '#c63b4a'
  if (score <= 35) return '#087f70'
  return '#9a6800'
}
