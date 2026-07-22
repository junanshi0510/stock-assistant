import { getJson } from './client'

export function fetchOpportunityTemplates() {
  return getJson('/api/v1/opportunities/templates')
}

export function fetchOpportunityOverview() {
  return getJson('/api/v1/opportunities/overview')
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
