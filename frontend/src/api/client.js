// Shared transport only. Domain modules own endpoint names and request payloads.
const API_BASE = (import.meta.env.VITE_API_BASE_URL || '').replace(/\/$/, '')
let csrfToken = ''

export function clearCsrfToken() {
  csrfToken = ''
}

export function setCsrfToken(value) {
  csrfToken = typeof value === 'string' ? value : ''
}

export async function getJson(url, options) {
  const request = { credentials: 'include', ...(options || {}) }
  const method = String(request.method || 'GET').toUpperCase()
  const headers = new Headers(request.headers || {})
  if (csrfToken && ['POST', 'PUT', 'PATCH', 'DELETE'].includes(method)) {
    headers.set('X-CSRF-Token', csrfToken)
  }
  request.headers = headers
  const response = await fetch(`${API_BASE}${url}`, request)
  const data = await response.json().catch(() => ({}))
  if (typeof data.csrf_token === 'string') setCsrfToken(data.csrf_token)
  if (!response.ok) {
    const nested = data.detail && typeof data.detail === 'object' ? data.detail : null
    const message = nested?.message || data.detail || data.message || `请求失败 (${response.status})`
    const error = new Error(message)
    error.status = response.status
    error.code = nested?.code || data.code || 'request_failed'
    if (response.status === 401 && error.code === 'authentication_required') {
      clearCsrfToken()
      globalThis.dispatchEvent(new CustomEvent('stock-assistant:unauthorized'))
    }
    throw error
  }
  return data
}
