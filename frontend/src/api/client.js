// Shared transport only. Domain modules own endpoint names and request payloads.
const API_BASE = (import.meta.env.VITE_API_BASE_URL || '').replace(/\/$/, '')

export async function getJson(url, options) {
  const response = await fetch(`${API_BASE}${url}`, options)
  const data = await response.json().catch(() => ({}))
  if (!response.ok) {
    throw new Error(data.detail || `请求失败 (${response.status})`)
  }
  return data
}
