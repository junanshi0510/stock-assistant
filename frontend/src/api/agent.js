import { getJson } from './client'

export function createFundResearchRun(payload) {
  const idempotencyKey = globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random()}`
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
