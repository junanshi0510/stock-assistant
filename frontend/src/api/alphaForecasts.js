import { getJson } from './client'

export function fetchAlphaForecastOverview(limit = 30) {
  return getJson(`/api/v1/alpha-forecasts/overview?limit=${encodeURIComponent(limit)}`)
}

export function createAlphaForecastProgram(payload) {
  return getJson('/api/v1/alpha-forecasts/programs', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ ...payload, acknowledged: true }),
  })
}

export function fetchAlphaForecastRun(runId) {
  return getJson(`/api/v1/alpha-forecasts/runs/${encodeURIComponent(runId)}`)
}

export function runAlphaForecastProgram(programId) {
  return getJson(`/api/v1/alpha-forecasts/programs/${encodeURIComponent(programId)}/runs`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ acknowledged: true }),
  })
}

export function updateAlphaForecastProgram(programId, action) {
  return getJson(`/api/v1/alpha-forecasts/programs/${encodeURIComponent(programId)}/actions`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ action }),
  })
}

export function settleAlphaForecastProgram(programId) {
  return getJson(`/api/v1/alpha-forecasts/programs/${encodeURIComponent(programId)}/settle`, {
    method: 'POST',
  })
}
