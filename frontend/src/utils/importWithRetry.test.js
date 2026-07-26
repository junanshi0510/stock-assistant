import assert from 'node:assert/strict'
import test from 'node:test'
import { importWithRetry, isRetryableImportError } from './importWithRetry.js'

test('recognizes browser and bundler chunk transport failures', () => {
  assert.equal(
    isRetryableImportError(new TypeError('Failed to fetch dynamically imported module: /assets/lab.js')),
    true,
  )
  assert.equal(isRetryableImportError(new Error('Loading chunk 42 failed')), true)
  assert.equal(isRetryableImportError(new Error('component initialization failed')), false)
})

test('retries a transient import failure and returns the module', async () => {
  let calls = 0
  const module = await importWithRetry(
    async () => {
      calls += 1
      if (calls < 3) {
        throw new TypeError('Failed to fetch dynamically imported module: /assets/lab.js')
      }
      return { default: 'loaded' }
    },
    { attempts: 3, delays: [0, 0] },
  )

  assert.deepEqual(module, { default: 'loaded' })
  assert.equal(calls, 3)
})

test('does not retry application initialization errors', async () => {
  let calls = 0

  await assert.rejects(
    importWithRetry(
      async () => {
        calls += 1
        throw new Error('component initialization failed')
      },
      { attempts: 3, delays: [0, 0] },
    ),
    /component initialization failed/,
  )

  assert.equal(calls, 1)
})

test('preserves the final transient import failure after the retry budget', async () => {
  let calls = 0

  await assert.rejects(
    importWithRetry(
      async () => {
        calls += 1
        throw new TypeError('Importing a module script failed')
      },
      { attempts: 2, delays: [0] },
    ),
    /Importing a module script failed/,
  )

  assert.equal(calls, 2)
})
