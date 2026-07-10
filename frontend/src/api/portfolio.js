import { getJson } from './client'

export function fetchWatchlist() {
  return getJson('/api/watchlist')
}

export function addWatch(market, symbol, name = '') {
  return getJson('/api/watchlist', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ market, symbol, name }),
  })
}

export function removeWatch(market, symbol) {
  const query = new URLSearchParams({ market, symbol })
  return getJson(`/api/watchlist?${query.toString()}`, { method: 'DELETE' })
}

export function fetchHoldings() {
  return getJson('/api/holdings')
}

export function fetchHoldingsInsights(maxFunds = 6) {
  return getJson(`/api/holdings/insights?max_funds=${encodeURIComponent(maxFunds)}`)
}

export function saveHoldings(items) {
  return getJson('/api/holdings', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ items }),
  })
}

export function deleteHolding(id) {
  return getJson(`/api/holdings/${id}`, { method: 'DELETE' })
}

export function parseHoldingsText(text) {
  return getJson('/api/holdings/parse-text', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ text }),
  })
}

export function uploadHoldingScreenshot(file) {
  const form = new FormData()
  form.append('file', file)
  return getJson('/api/holdings/ocr-upload', { method: 'POST', body: form })
}

export function fetchAlerts(limit = 50) {
  return getJson(`/api/alerts?limit=${limit}`)
}

export function clearAlerts() {
  return getJson('/api/alerts', { method: 'DELETE' })
}

export function triggerScan() {
  return getJson('/api/alerts/scan', { method: 'POST' })
}
