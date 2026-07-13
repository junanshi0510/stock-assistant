import { clearCsrfToken, getJson } from './client'

export function fetchAuthSession() {
  return getJson('/api/auth/session')
}

export function loginAccount(username, password) {
  return getJson('/api/auth/login', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username, password }),
  })
}

export function registerAccount(username, password) {
  return getJson('/api/auth/register', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username, password }),
  })
}

export async function logoutAccount() {
  try {
    return await getJson('/api/auth/logout', { method: 'POST' })
  } finally {
    clearCsrfToken()
  }
}

export async function changeAccountPassword(currentPassword, newPassword) {
  try {
    return await getJson('/api/auth/change-password', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ current_password: currentPassword, new_password: newPassword }),
    })
  } finally {
    clearCsrfToken()
  }
}

export function fetchAdminOverview() {
  return getJson('/api/admin/overview')
}

export function fetchAdminUsers() {
  return getJson('/api/admin/users')
}

export function createAdminUser(payload) {
  return getJson('/api/admin/users', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
}

export function updateAdminUser(userId, payload) {
  return getJson(`/api/admin/users/${encodeURIComponent(userId)}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
}

export function resetAdminUserPassword(userId, temporaryPassword) {
  return getJson(`/api/admin/users/${encodeURIComponent(userId)}/reset-password`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ temporary_password: temporaryPassword }),
  })
}

export function fetchAdminAuthAudit(limit = 50) {
  return getJson(`/api/admin/auth-audit?limit=${encodeURIComponent(limit)}`)
}
