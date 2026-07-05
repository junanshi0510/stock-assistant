// 与后端 FastAPI 通信的封装。
// 默认请求同域 /api；部署到 GitHub Pages 等静态托管时，可用 VITE_API_BASE_URL 指向后端域名。

const API_BASE = (import.meta.env.VITE_API_BASE_URL || '').replace(/\/$/, '')

async function getJson(url, options) {
  const res = await fetch(`${API_BASE}${url}`, options)
  const data = await res.json().catch(() => ({}))
  if (!res.ok) {
    throw new Error(data.detail || `请求失败 (${res.status})`)
  }
  return data
}

export function fetchMarkets() {
  return getJson('/api/markets')
}

export function fetchPresets() {
  return getJson('/api/presets')
}

export function analyze(market, symbol, months) {
  const q = new URLSearchParams({ market, symbol, months: String(months) })
  return getJson(`/api/analyze?${q.toString()}`)
}

export function runBacktest(market, symbol, horizon) {
  const q = new URLSearchParams({ market, symbol, horizon: String(horizon) })
  return getJson(`/api/backtest?${q.toString()}`)
}

export function scan(market, symbols, months) {
  return getJson('/api/scan', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ market, symbols, months }),
  })
}

export function fetchFundamentals(market, symbol) {
  const q = new URLSearchParams({ market, symbol })
  return getJson(`/api/fundamentals?${q.toString()}`)
}

export function fetchQuote(market, symbol) {
  const q = new URLSearchParams({ market, symbol })
  return getJson(`/api/quote?${q.toString()}`)
}

export function fetchMl(market, symbol, horizon = 10) {
  const q = new URLSearchParams({ market, symbol, horizon: String(horizon) })
  return getJson(`/api/ml?${q.toString()}`)
}

export function fetchNews(market, symbol) {
  const q = new URLSearchParams({ market, symbol })
  return getJson(`/api/news?${q.toString()}`)
}

export function fetchCompare(market, symbol, months = 12) {
  const q = new URLSearchParams({ market, symbol, months: String(months) })
  return getJson(`/api/compare?${q.toString()}`)
}

export function multiCompare(market, symbols, months = 12, includeFundamentals = false) {
  return getJson('/api/multi_compare', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ market, symbols, months, include_fundamentals: includeFundamentals }),
  })
}

export function searchUs(keyword) {
  return getJson(`/api/search_us?keyword=${encodeURIComponent(keyword)}`)
}

/* ===== 自选股(本地持久化)===== */
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
  const q = new URLSearchParams({ market, symbol })
  return getJson(`/api/watchlist?${q.toString()}`, { method: 'DELETE' })
}

/* ===== 我的持仓 / 截图导入 ===== */
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
  return getJson('/api/holdings/ocr-upload', {
    method: 'POST',
    body: form,
  })
}

/* ===== 提醒(打分变化监控)===== */
export function fetchAlerts(limit = 50) {
  return getJson(`/api/alerts?limit=${limit}`)
}

export function clearAlerts() {
  return getJson('/api/alerts', { method: 'DELETE' })
}

export function triggerScan() {
  return getJson('/api/alerts/scan', { method: 'POST' })
}

/* ===== 热门股/涨跌幅榜 ===== */
export function fetchHot(market, period = '1d', type = 'gainers', limit = 50) {
  const q = new URLSearchParams({ market, period, type, limit: String(limit) })
  return getJson(`/api/hot?${q.toString()}`)
}

export function fetchSectors(market = 'A股', sectorLimit = 12, stockLimit = 8, includeConcepts = true) {
  const q = new URLSearchParams({
    market,
    sector_limit: String(sectorLimit),
    stock_limit: String(stockLimit),
    include_concepts: String(includeConcepts),
  })
  return getJson(`/api/sectors?${q.toString()}`)
}

/* ===== 基金分析 ===== */
export function fetchHotFunds(category = 'all', limit = 30, sort = '1y') {
  const q = new URLSearchParams({ category, limit: String(limit), sort })
  return getJson(`/api/funds/hot?${q.toString()}`)
}

export function fetchFundCategories() {
  return getJson('/api/funds/categories')
}

export function searchFunds(keyword, limit = 20) {
  const q = new URLSearchParams({ keyword, limit: String(limit) })
  return getJson(`/api/funds/search?${q.toString()}`)
}

export function analyzeFund(code, months = 36) {
  const q = new URLSearchParams({ code, months: String(months) })
  return getJson(`/api/funds/analyze?${q.toString()}`)
}

export function fetchFundPortfolio(code, year = '') {
  const q = new URLSearchParams({ code })
  if (year) q.set('year', year)
  return getJson(`/api/funds/portfolio?${q.toString()}`)
}

export function fetchFundPeers(code, sort = '1y', limit = 1000) {
  const q = new URLSearchParams({ code, sort, limit: String(limit) })
  return getJson(`/api/funds/peers?${q.toString()}`)
}

export function fetchFundDividends(code) {
  const q = new URLSearchParams({ code })
  return getJson(`/api/funds/dividends?${q.toString()}`)
}

export function compareFunds(codes, months = 36) {
  return getJson('/api/funds/compare', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ codes, months }),
  })
}

export function analyzeFundOverlap(codes) {
  return getJson('/api/funds/overlap', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ codes }),
  })
}
