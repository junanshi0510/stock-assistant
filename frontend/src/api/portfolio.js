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

export function fetchHoldingsExposure(maxFunds = 6) {
  return getJson(`/api/holdings/exposure?max_funds=${encodeURIComponent(maxFunds)}`)
}

export function fetchInvestmentProfile() {
  return getJson('/api/investment-profile')
}

export function saveInvestmentProfile(profile) {
  return getJson('/api/investment-profile', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(profile),
  })
}

export function fetchDecisionCenter() {
  return getJson('/api/decision-center')
}

export function fetchPortfolioTransactions() {
  return getJson('/api/portfolio/transactions')
}

export function createPortfolioTransaction(transaction) {
  return getJson('/api/portfolio/transactions', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(transaction),
  })
}

export function previewPortfolioTransactionCsv(file, assetType, market) {
  const form = new FormData()
  form.append('file', file)
  form.append('asset_type', assetType)
  form.append('market', market)
  return getJson('/api/portfolio/transactions/parse-csv', { method: 'POST', body: form })
}

export function importPortfolioTransactionCsv(items, fileSha256, filename = '') {
  return getJson('/api/portfolio/transactions/import-csv', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ items, file_sha256: fileSha256, filename }),
  })
}

export function deletePortfolioTransaction(id) {
  return getJson(`/api/portfolio/transactions/${id}`, { method: 'DELETE' })
}

export function fetchPortfolioLedger() {
  return getJson('/api/portfolio/ledger')
}

export function fetchPortfolioPerformance() {
  return getJson('/api/portfolio/performance')
}

export function fetchPortfolioBehavior() {
  return getJson('/api/portfolio/behavior')
}

export function fetchPortfolioAttribution() {
  return getJson('/api/portfolio/attribution')
}

export function fetchRebalanceReview() {
  return getJson('/api/portfolio/rebalance')
}

export function fetchPortfolioSnapshots(limit = 24) {
  return getJson(`/api/portfolio/snapshots?limit=${encodeURIComponent(limit)}`)
}

export function createPortfolioSnapshot(reason = 'manual') {
  return getJson('/api/portfolio/snapshots', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ reason }),
  })
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

export function previewHoldingsFile(file) {
  const form = new FormData()
  form.append('file', file)
  return getJson('/api/holdings/parse-file', { method: 'POST', body: form })
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
