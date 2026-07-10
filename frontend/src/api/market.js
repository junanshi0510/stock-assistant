import { getJson } from './client'

export function fetchMarkets() {
  return getJson('/api/markets')
}

export function fetchPresets() {
  return getJson('/api/presets')
}

export function analyze(market, symbol, months) {
  const query = new URLSearchParams({ market, symbol, months: String(months) })
  return getJson(`/api/analyze?${query.toString()}`)
}

export function runBacktest(market, symbol, horizon) {
  const query = new URLSearchParams({ market, symbol, horizon: String(horizon) })
  return getJson(`/api/backtest?${query.toString()}`)
}

export function scan(market, symbols, months) {
  return getJson('/api/scan', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ market, symbols, months }),
  })
}

export function fetchFundamentals(market, symbol) {
  const query = new URLSearchParams({ market, symbol })
  return getJson(`/api/fundamentals?${query.toString()}`)
}

export function fetchQuote(market, symbol) {
  const query = new URLSearchParams({ market, symbol })
  return getJson(`/api/quote?${query.toString()}`)
}

export function fetchMl(market, symbol, horizon = 10) {
  const query = new URLSearchParams({ market, symbol, horizon: String(horizon) })
  return getJson(`/api/ml?${query.toString()}`)
}

export function fetchNews(market, symbol) {
  const query = new URLSearchParams({ market, symbol })
  return getJson(`/api/news?${query.toString()}`)
}

export function fetchCompare(market, symbol, months = 12) {
  const query = new URLSearchParams({ market, symbol, months: String(months) })
  return getJson(`/api/compare?${query.toString()}`)
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

export function fetchHot(market, period = '1d', type = 'gainers', limit = 50) {
  const query = new URLSearchParams({ market, period, type, limit: String(limit) })
  return getJson(`/api/hot?${query.toString()}`)
}

export function fetchSectors(market = 'A股', sectorLimit = 12, stockLimit = 8, includeConcepts = true) {
  const query = new URLSearchParams({
    market,
    sector_limit: String(sectorLimit),
    stock_limit: String(stockLimit),
    include_concepts: String(includeConcepts),
  })
  return getJson(`/api/sectors?${query.toString()}`)
}

export function fetchMarketDaily(risk = 'balanced', fundLimit = 4) {
  const query = new URLSearchParams({ risk, fund_limit: String(fundLimit) })
  return getJson(`/api/market/daily?${query.toString()}`)
}
