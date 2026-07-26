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

export function fetchQuantSelectionForwardValidations(limit = 100) {
  return getJson(`/api/v1/quant-selection/forward-validations?limit=${encodeURIComponent(limit)}`)
}

export function createQuantSelectionForwardValidation(mandateId, expectedSnapshotSha256) {
  return getJson(`/api/v1/quant-selection/shadow-mandates/${encodeURIComponent(mandateId)}/forward-validations`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      acknowledged: true,
      expected_snapshot_sha256: expectedSnapshotSha256,
    }),
  })
}

export function observeQuantSelectionForwardValidation(validationId) {
  return getJson(`/api/v1/quant-selection/forward-validations/${encodeURIComponent(validationId)}/observations`, {
    method: 'POST',
  })
}

export function fetchQuantResearchPrograms(limit = 30) {
  return getJson(`/api/v1/quant-selection/research-programs?limit=${encodeURIComponent(limit)}`)
}

export function createQuantResearchProgram(payload) {
  return getJson('/api/v1/quant-selection/research-programs', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
}

export function reconcileQuantResearchProgram(programId) {
  return getJson(`/api/v1/quant-selection/research-programs/${encodeURIComponent(programId)}/reconcile`, {
    method: 'POST',
  })
}

export function retireQuantResearchProgram(programId, reason) {
  return getJson(`/api/v1/quant-selection/research-programs/${encodeURIComponent(programId)}/retire`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ reason }),
  })
}
