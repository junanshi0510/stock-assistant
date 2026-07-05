// 方向 -> 样式类
export function dirClass(d) {
  if (d === '看涨') return 'up'
  if (d === '看跌') return 'down'
  return 'neutral'
}

// 分数 -> 颜色(红涨绿跌,中间黄)
export function scoreColor(score) {
  if (score >= 65) return '#ff4d5e'
  if (score <= 35) return '#1fd286'
  return '#f5b942'
}

// 概率 -> 颜色渐变
export function probColor(p) {
  if (p >= 60) return 'linear-gradient(90deg,#f5b942,#ff4d5e)'
  if (p <= 40) return 'linear-gradient(90deg,#1fd286,#3fb58a)'
  return 'linear-gradient(90deg,#1fd286,#f5b942)'
}
