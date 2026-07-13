import { getJson } from './client'

function newIdempotencyKey() {
  return globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random()}`
}

export function createFundResearchRun(payload) {
  const idempotencyKey = newIdempotencyKey()
  return getJson('/api/v1/agent/runs', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'Idempotency-Key': idempotencyKey,
    },
    body: JSON.stringify({ intent: 'fund_deep_research', ...payload }),
  })
}

export function fetchAgentRun(runId) {
  return getJson(`/api/v1/agent/runs/${encodeURIComponent(runId)}`)
}

export function fetchAgentRunComparison(runId) {
  return getJson(`/api/v1/agent/runs/${encodeURIComponent(runId)}/comparison`)
}

export function rerunAgentRun(runId) {
  return getJson(`/api/v1/agent/runs/${encodeURIComponent(runId)}/rerun`, {
    method: 'POST',
    headers: { 'Idempotency-Key': newIdempotencyKey() },
  })
}

export function fetchAgentRuns({ limit = 8, cursor = '', status = '', code = '' } = {}) {
  const params = new URLSearchParams({ limit: String(limit) })
  if (cursor) params.set('cursor', cursor)
  if (status) params.set('status', status)
  if (code) params.set('code', code)
  return getJson(`/api/v1/agent/runs?${params.toString()}`)
}

export function cancelAgentRun(runId) {
  return getJson(`/api/v1/agent/runs/${encodeURIComponent(runId)}/cancel`, { method: 'POST' })
}

export function fetchAgentEvidence(runId, evidenceId) {
  return getJson(`/api/v1/agent/runs/${encodeURIComponent(runId)}/evidence/${encodeURIComponent(evidenceId)}`)
}

export function fetchAgentAudit(runId) {
  return getJson(`/api/v1/agent/runs/${encodeURIComponent(runId)}/audit`)
}

export function fetchAgentRunEvaluations(runId) {
  return getJson(`/api/v1/agent/runs/${encodeURIComponent(runId)}/evaluations`)
}

export function evaluateAgentRun(runId) {
  return getJson(`/api/v1/agent/runs/${encodeURIComponent(runId)}/evaluate`, { method: 'POST' })
}
