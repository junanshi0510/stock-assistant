import { getJson } from './client'

export function fetchPlatformAvailability() {
  return getJson('/api/platform/availability')
}

export function fetchAdminAvailability(historyLimit = 288) {
  return getJson(`/api/admin/availability?history_limit=${encodeURIComponent(historyLimit)}`)
}

export function runAdminAvailabilityProbe(mode = 'standard') {
  return getJson('/api/admin/availability/probes', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ mode }),
  })
}
