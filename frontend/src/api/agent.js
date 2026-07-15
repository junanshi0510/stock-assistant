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

export function createFundResearchBatch(payload) {
  const idempotencyKey = newIdempotencyKey()
  return getJson('/api/v1/agent/batches', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'Idempotency-Key': idempotencyKey,
    },
    body: JSON.stringify({ intent: 'fund_deep_research', ...payload }),
  })
}

export function fetchAgentBatch(batchId) {
  return getJson(`/api/v1/agent/batches/${encodeURIComponent(batchId)}`)
}

export function fetchAgentBatches({ limit = 6 } = {}) {
  return getJson(`/api/v1/agent/batches?limit=${encodeURIComponent(limit)}`)
}

export function cancelAgentBatch(batchId) {
  return getJson(`/api/v1/agent/batches/${encodeURIComponent(batchId)}/cancel`, { method: 'POST' })
}

export function createAgentBatchAllocation(batchId, expectedBatchInputHash) {
  return getJson(`/api/v1/agent/batches/${encodeURIComponent(batchId)}/allocation`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ expected_batch_input_hash: expectedBatchInputHash }),
  })
}

export function createAgentBatchPurchasePreflight(batchId, payload) {
  return getJson(`/api/v1/agent/batches/${encodeURIComponent(batchId)}/purchase-preflight`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
}

export function recordAgentBatchPurchaseExecution(batchId, payload) {
  return getJson(`/api/v1/agent/batches/${encodeURIComponent(batchId)}/purchase-execution`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
}

export function reconcileAgentBatchPurchaseHoldings(batchId, payload) {
  return getJson(`/api/v1/agent/batches/${encodeURIComponent(batchId)}/purchase-reconciliation`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
}

export function createAgentBatchPurchaseAttribution(batchId, payload) {
  return getJson(`/api/v1/agent/batches/${encodeURIComponent(batchId)}/purchase-attribution`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
}

export function fetchAgentModelStatus() {
  return getJson('/api/v1/agent/model/status')
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

export function fetchAgentOutcomeSchedule(runId) {
  return getJson(`/api/v1/agent/runs/${encodeURIComponent(runId)}/outcome-schedule`)
}

export function fetchAgentStrategyShadowOutcome(runId) {
  return getJson(`/api/v1/agent/runs/${encodeURIComponent(runId)}/strategy-shadow-outcome`)
}

export function configureAgentOutcomeSchedule(runId, payload) {
  return getJson(`/api/v1/agent/runs/${encodeURIComponent(runId)}/outcome-schedule`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
}
