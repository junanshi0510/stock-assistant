import { getJson } from './client'

export function fetchHotFunds(category = 'all', limit = 30, sort = '1y') {
  const query = new URLSearchParams({ category, limit: String(limit), sort })
  return getJson(`/api/funds/hot?${query.toString()}`)
}

export function fetchFundCategories() {
  return getJson('/api/funds/categories')
}

export function fetchFundOpportunities(risk = 'balanced', limit = 5) {
  const query = new URLSearchParams({ risk, limit: String(limit) })
  return getJson(`/api/funds/opportunities?${query.toString()}`)
}

export function searchFunds(keyword, limit = 20) {
  const query = new URLSearchParams({ keyword, limit: String(limit) })
  return getJson(`/api/funds/search?${query.toString()}`)
}

export function analyzeFund(code, months = 36) {
  const query = new URLSearchParams({ code, months: String(months) })
  return getJson(`/api/funds/analyze?${query.toString()}`)
}

export function fetchFundPortfolio(code, year = '') {
  const query = new URLSearchParams({ code })
  if (year) query.set('year', year)
  return getJson(`/api/funds/portfolio?${query.toString()}`)
}

export function fetchFundPeers(code, sort = '1y', limit = 1000) {
  const query = new URLSearchParams({ code, sort, limit: String(limit) })
  return getJson(`/api/funds/peers?${query.toString()}`)
}

export function fetchFundAlternatives(code, sort = '1y', limit = 5, months = 36) {
  const query = new URLSearchParams({ code, sort, limit: String(limit), months: String(months) })
  return getJson(`/api/funds/alternatives?${query.toString()}`)
}

export function fetchFundDividends(code) {
  return getJson(`/api/funds/dividends?code=${encodeURIComponent(code)}`)
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
