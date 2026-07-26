const RETRYABLE_IMPORT_PATTERNS = [
  /failed to fetch dynamically imported module/i,
  /error loading dynamically imported module/i,
  /importing a module script failed/i,
  /loading chunk [\w-]+ failed/i,
  /chunkloaderror/i,
  /networkerror.*import/i,
]

export function isRetryableImportError(error) {
  const message = String(error?.message || error || '')
  return RETRYABLE_IMPORT_PATTERNS.some((pattern) => pattern.test(message))
}

function wait(delayMs) {
  return new Promise((resolve) => globalThis.setTimeout(resolve, Math.max(0, delayMs)))
}

export async function importWithRetry(
  loader,
  {
    attempts = 3,
    delays = [350, 1200],
  } = {},
) {
  if (typeof loader !== 'function') {
    throw new TypeError('loader must be a function')
  }

  const maxAttempts = Math.max(1, Math.trunc(Number(attempts) || 1))
  let lastError

  for (let attempt = 0; attempt < maxAttempts; attempt += 1) {
    try {
      return await loader()
    } catch (error) {
      lastError = error
      const hasNextAttempt = attempt + 1 < maxAttempts
      if (!hasNextAttempt || !isRetryableImportError(error)) {
        throw error
      }
      await wait(delays[Math.min(attempt, Math.max(0, delays.length - 1))] || 0)
    }
  }

  throw lastError
}
