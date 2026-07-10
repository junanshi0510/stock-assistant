export function pct(value) {
  if (value == null) return '-'
  return `${value > 0 ? '+' : ''}${Number(value).toFixed(2)}%`
}

export function num(value, digits = 2) {
  if (value == null) return '-'
  return Number(value).toFixed(digits)
}

export function metricText(metric) {
  if (metric?.value == null) return '-'
  if (metric.unit === '只' || metric.unit === '组') return `${Number(metric.value).toFixed(0)}${metric.unit}`
  if (metric.unit === '%') return `${Number(metric.value).toFixed(2)}%`
  return `${num(metric.value)}${metric.unit || ''}`
}

export function deltaClass(value) {
  if (value > 0) return 'delta-pos'
  if (value < 0) return 'delta-neg'
  return 'delta-zero'
}
