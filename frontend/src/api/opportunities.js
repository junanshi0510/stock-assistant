import { getJson } from './client'

export function fetchOpportunityTemplates() {
  return getJson('/api/v1/opportunities/templates')
}

export function fetchOpportunityOverview() {
  return getJson('/api/v1/opportunities/overview')
}

export function fetchOpportunityProfitLab() {
  return getJson('/api/v1/opportunities/profit-lab')
}

export function fetchOpportunityCommittee() {
  return getJson('/api/v1/opportunities/committee')
}

export function freezeOpportunityCommittee() {
  return getJson('/api/v1/opportunities/committee/mandates', {
    method: 'POST',
  })
}

export function fetchOpportunityCommitteeMandates(limit = 20) {
  return getJson(`/api/v1/opportunities/committee/mandates?limit=${encodeURIComponent(limit)}`)
}

export function fetchOpportunityProfitPolicy(strategyId) {
  return getJson(`/api/v1/opportunities/strategies/${encodeURIComponent(strategyId)}/profit-policy`)
}

export function createOpportunityProfitPolicy(strategyId, policy) {
  return getJson(`/api/v1/opportunities/strategies/${encodeURIComponent(strategyId)}/profit-policy/versions`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(policy),
  })
}

export function createOpportunityProfitScorecard(strategyId) {
  return getJson(`/api/v1/opportunities/strategies/${encodeURIComponent(strategyId)}/profit-scorecards`, {
    method: 'POST',
  })
}

export function fetchOpportunityProfitScorecards(strategyId, limit = 20) {
  const search = new URLSearchParams({ limit: String(limit) })
  if (strategyId) search.set('strategy_id', strategyId)
  return getJson(`/api/v1/opportunities/profit-scorecards?${search.toString()}`)
}

export function createOpportunityStrategy(definition) {
  return getJson('/api/v1/opportunities/strategies', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(definition),
  })
}

export function createOpportunityStrategyVersion(strategyId, definition) {
  return getJson(`/api/v1/opportunities/strategies/${encodeURIComponent(strategyId)}/versions`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(definition),
  })
}

export function archiveOpportunityStrategy(strategyId) {
  return getJson(`/api/v1/opportunities/strategies/${encodeURIComponent(strategyId)}`, {
    method: 'DELETE',
  })
}

export function startOpportunityRun(strategyId) {
  return getJson('/api/v1/opportunities/runs', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ strategy_id: strategyId }),
  })
}

export function fetchOpportunityRun(runId) {
  return getJson(`/api/v1/opportunities/runs/${encodeURIComponent(runId)}`)
}

export function createOpportunityPaperBasket(runId) {
  return getJson(`/api/v1/opportunities/runs/${encodeURIComponent(runId)}/paper-baskets`, {
    method: 'POST',
  })
}

export function fetchOpportunityPaperBasket(basketId) {
  return getJson(`/api/v1/opportunities/paper-baskets/${encodeURIComponent(basketId)}`)
}

export function observeOpportunityPaperBasket(basketId) {
  return getJson(`/api/v1/opportunities/paper-baskets/${encodeURIComponent(basketId)}/observations`, {
    method: 'POST',
  })
}
