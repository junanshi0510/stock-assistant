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

export function fetchHoldingFundAlternatives(holdingId, sort = '1y', limit = 3, months = 36) {
  const query = new URLSearchParams({
    sort,
    limit: String(limit),
    months: String(months),
  })
  return getJson(`/api/holdings/${encodeURIComponent(holdingId)}/fund-alternatives?${query.toString()}`)
}

export function createFundSwitchQuote(holdingId, payload) {
  return getJson(`/api/holdings/${encodeURIComponent(holdingId)}/fund-switch-quotes`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
}

export function fetchFundSwitchQuotes(holdingId) {
  return getJson(`/api/holdings/${encodeURIComponent(holdingId)}/fund-switch-quotes`)
}

export function fetchFundSwitchQuoteAudit(holdingId, candidateCode) {
  return getJson(`/api/holdings/${encodeURIComponent(holdingId)}/fund-switch-quotes/${encodeURIComponent(candidateCode)}/audit`)
}

export function createFundSwitchExecutionReview(holdingId, candidateCode, payload) {
  return getJson(`/api/holdings/${encodeURIComponent(holdingId)}/fund-switch-execution-reviews/${encodeURIComponent(candidateCode)}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
}

export function fetchFundSwitchExecutionReview(holdingId, candidateCode) {
  return getJson(`/api/holdings/${encodeURIComponent(holdingId)}/fund-switch-execution-reviews/${encodeURIComponent(candidateCode)}`)
}

export function fetchFundSwitchCandidateContext(holdingId, candidateCode) {
  return getJson(`/api/holdings/${encodeURIComponent(holdingId)}/fund-switch-cases/${encodeURIComponent(candidateCode)}`)
}

export function createFundSwitchSettlement(holdingId, candidateCode, payload) {
  return getJson(`/api/holdings/${encodeURIComponent(holdingId)}/fund-switch-cases/${encodeURIComponent(candidateCode)}/settlements`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
}

export function fetchFundSwitchCases(limit = 50) {
  return getJson(`/api/portfolio/fund-switch-cases?limit=${encodeURIComponent(limit)}`)
}

export function fetchFundSwitchCase(caseId) {
  return getJson(`/api/portfolio/fund-switch-cases/${encodeURIComponent(caseId)}`)
}

export function createFundSwitchPurchaseRequote(caseId, payload) {
  return getJson(`/api/portfolio/fund-switch-cases/${encodeURIComponent(caseId)}/purchase-requotes`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
}

export function recordFundSwitchPurchase(caseId, payload) {
  return getJson(`/api/portfolio/fund-switch-cases/${encodeURIComponent(caseId)}/purchases`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
}

export function reconcileFundSwitchCase(caseId) {
  return getJson(`/api/portfolio/fund-switch-cases/${encodeURIComponent(caseId)}/reconciliation`, {
    method: 'POST',
  })
}

export function createFundSwitchAttributionSnapshot(caseId) {
  return getJson(`/api/portfolio/fund-switch-cases/${encodeURIComponent(caseId)}/attribution-snapshots`, {
    method: 'POST',
  })
}

export function fetchHoldingsLevelRecurrence(months = 60) {
  return getJson(`/api/holdings/level-recurrence?months=${encodeURIComponent(months)}`)
}

export function fetchHoldingsInsights(maxFunds = 6) {
  return getJson(`/api/holdings/insights?max_funds=${encodeURIComponent(maxFunds)}`)
}

export function fetchHoldingsExposure(maxFunds = 6) {
  return getJson(`/api/holdings/exposure?max_funds=${encodeURIComponent(maxFunds)}`)
}

export function createHoldingsExposureSnapshot(targetCode = null) {
  return getJson('/api/holdings/exposure-snapshots', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ target_code: targetCode || null }),
  })
}

export function fetchHoldingsExposureSnapshots(limit = 20, targetCode = '') {
  const query = new URLSearchParams({ limit: String(limit) })
  if (targetCode) query.set('target_code', targetCode)
  return getJson(`/api/holdings/exposure-snapshots?${query.toString()}`)
}

export function fetchPortfolioTwinPresets() {
  return getJson('/api/portfolio/decision-twin/presets')
}

export function createPortfolioTwinRun(payload) {
  return getJson('/api/portfolio/decision-twin/runs', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
}

export function fetchPortfolioTwinRuns(limit = 20) {
  return getJson(`/api/portfolio/decision-twin/runs?limit=${encodeURIComponent(limit)}`)
}

export function fetchPortfolioTwinRun(runId) {
  return getJson(`/api/portfolio/decision-twin/runs/${encodeURIComponent(runId)}`)
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

export function createInvestmentProfileDraft(profile) {
  return getJson('/api/investment-profile/drafts', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(profile),
  })
}

export function activateInvestmentProfileVersion(versionId, payload) {
  return getJson(`/api/investment-profile/versions/${encodeURIComponent(versionId)}/activate`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
}

export function fetchInvestmentProfileVersions(limit = 20) {
  return getJson(`/api/investment-profile/versions?limit=${encodeURIComponent(limit)}`)
}

export function fetchInvestmentProfileAudit() {
  return getJson('/api/investment-profile/audit')
}

export function fetchDecisionCenter() {
  return getJson('/api/decision-center')
}

export function fetchPortfolioCapitalDecision() {
  return getJson('/api/portfolio/capital-decision')
}

export function freezePortfolioCapitalDecision() {
  return getJson('/api/portfolio/capital-decision/plans', {
    method: 'POST',
  })
}

export function fetchPortfolioCapitalDecisionPlans(limit = 30) {
  return getJson(`/api/portfolio/capital-decision/plans?limit=${encodeURIComponent(limit)}`)
}

export function fetchPortfolioCapitalDecisionPlan(planId) {
  return getJson(`/api/portfolio/capital-decision/plans/${encodeURIComponent(planId)}`)
}

export function fetchPortfolioCapitalLearning(limit = 50) {
  return getJson(`/api/portfolio/capital-decision/learning?limit=${encodeURIComponent(limit)}`)
}

export function fetchPortfolioCapitalPlanExecution(planId) {
  return getJson(`/api/portfolio/capital-decision/plans/${encodeURIComponent(planId)}/execution`)
}

export function createPortfolioCapitalExecutionEvent(planId, payload) {
  return getJson(`/api/portfolio/capital-decision/plans/${encodeURIComponent(planId)}/execution-events`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
}

export function reviewPortfolioCapitalExecutionDeviation(planId, payload) {
  return getJson(`/api/portfolio/capital-decision/plans/${encodeURIComponent(planId)}/execution-review`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
}

export function fetchPortfolioCapitalPlanOutcomes(planId, limit = 100) {
  return getJson(`/api/portfolio/capital-decision/plans/${encodeURIComponent(planId)}/outcomes?limit=${encodeURIComponent(limit)}`)
}

export function refreshPortfolioCapitalPlanOutcome(planId) {
  return getJson(`/api/portfolio/capital-decision/plans/${encodeURIComponent(planId)}/outcomes`, {
    method: 'POST',
  })
}

export function fetchPortfolioCapitalOutcomeJob(jobId) {
  return getJson(`/api/portfolio/capital-decision/outcome-jobs/${encodeURIComponent(jobId)}`)
}

export function fetchPortfolioCapitalOutcome(outcomeId) {
  return getJson(`/api/portfolio/capital-decision/outcomes/${encodeURIComponent(outcomeId)}`)
}

export function fetchDecisionTasks({ status = '', includeResolved = false, limit = 50 } = {}) {
  const query = new URLSearchParams({
    include_resolved: String(includeResolved),
    limit: String(limit),
  })
  if (status) query.set('status', status)
  return getJson(`/api/decision-tasks?${query.toString()}`)
}

export function fetchDecisionTaskSummary() {
  return getJson('/api/decision-tasks/summary')
}

export function fetchDecisionCheckSchedule(verifyAudit = false) {
  return getJson(`/api/decision-check-schedule?verify_audit=${String(verifyAudit)}`)
}

export function configureDecisionCheckSchedule({
  enabled,
  intervalHours,
  runImmediately = false,
  expectedRevision = null,
}) {
  const payload = {
    enabled,
    interval_hours: intervalHours,
    run_immediately: runImmediately,
  }
  if (expectedRevision != null) payload.expected_revision = expectedRevision
  return getJson('/api/decision-check-schedule', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
}

export function updateDecisionTask(taskId, status, expectedRevision, snoozeHours = null) {
  const payload = { status, expected_revision: expectedRevision }
  if (status === 'snoozed') payload.snooze_hours = snoozeHours
  return getJson(`/api/decision-tasks/${encodeURIComponent(taskId)}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
}

export function fetchDecisionTaskAudit(taskId) {
  return getJson(`/api/decision-tasks/${encodeURIComponent(taskId)}/audit`)
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

export function fetchHoldingTheses() {
  return getJson('/api/portfolio/theses')
}

export function saveHoldingThesis(payload) {
  return getJson('/api/portfolio/theses', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
}

export function fetchHoldingThesisHistory(assetType, market, code, limit = 20) {
  const query = new URLSearchParams({ market, limit: String(limit) })
  return getJson(`/api/portfolio/theses/${encodeURIComponent(assetType)}/${encodeURIComponent(code)}?${query.toString()}`)
}

export function fetchLatestPortfolioActionReport() {
  return getJson('/api/portfolio/action-reports/latest')
}

export function createPortfolioActionReport(maxFunds = 8) {
  return getJson('/api/portfolio/action-reports', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ max_funds: maxFunds }),
  })
}

export function fetchPortfolioActionReports(limit = 20) {
  return getJson(`/api/portfolio/action-reports?limit=${encodeURIComponent(limit)}`)
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

export function fetchLatestPortfolioValuation() {
  return getJson('/api/portfolio/valuations/latest')
}

export function refreshPortfolioValuation(force = true) {
  return getJson('/api/portfolio/valuations/refresh', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ force }),
  })
}

export function fetchPortfolioValuations(limit = 20) {
  return getJson(`/api/portfolio/valuations?limit=${encodeURIComponent(limit)}`)
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

export function fetchHoldingOcrJob(jobId) {
  return getJson(`/api/holdings/ocr-jobs/${encodeURIComponent(jobId)}`)
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
