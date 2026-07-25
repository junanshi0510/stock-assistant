import { getJson } from './client'

export function fetchQuantSelectionOverview(limit = 30) {
  return getJson(`/api/v1/quant-selection/overview?limit=${encodeURIComponent(limit)}`)
}

export function createQuantSelectionRun(policy) {
  return getJson('/api/v1/quant-selection/runs', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(policy),
  })
}

export function fetchQuantSelectionRun(runId) {
  return getJson(`/api/v1/quant-selection/runs/${encodeURIComponent(runId)}`)
}

export function createQuantSelectionShadowMandate(runId, expectedResultSha256) {
  return getJson(`/api/v1/quant-selection/runs/${encodeURIComponent(runId)}/shadow-mandates`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      acknowledged: true,
      expected_result_sha256: expectedResultSha256,
    }),
  })
}
