import { test } from 'node:test'
import assert from 'node:assert/strict'
import { LabSession, LAB_API } from '../src/labSession.ts'
import { CKA_ORIGIN, IDENTITY } from '../src/identitySession.ts'
const json = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status })
const typed = (callback: (...args: Parameters<typeof fetch>) => Promise<Response>) => callback as typeof fetch

test('each operation uses a fresh central exchange and a fixed bearer-only lab endpoint', async () => {
  const calls: [string, RequestInit | undefined][] = []
  let sequence = 0
  const client = new LabSession(typed(async (url, options) => {
    calls.push([String(url), options])
    if (String(url).startsWith(IDENTITY)) return json({ token: 'temporary-' + ++sequence })
    assert.equal((options!.headers as Record<string,string>).Authorization, 'Bearer temporary-' + sequence)
    return json({ output: 'scoped result' })
  }), CKA_ORIGIN)
  await client.request('command', { id: 'a'.repeat(32), command: 'kubectl get pods' })
  await client.request('grade', { id: 'a'.repeat(32) })
  assert.equal(sequence, 2)
  assert.equal(calls[0][1]?.credentials, 'include'); assert.equal(calls[0][1]?.body, '{}')
  assert.equal(calls[1][0], LAB_API + '/api/labs/command')
  assert.equal(calls[1][1]?.credentials, 'omit'); assert.equal(calls[1][1]?.cache, 'no-store')
  assert.deepEqual(JSON.parse(calls[1][1]!.body as string), { id: 'a'.repeat(32), command: 'kubectl get pods' })
  assert.equal(calls.some(([url]) => url.includes('temporary-')), false)
  client.dispose()
})
test('anonymous, malformed and revoked exchanges cannot invoke the lab', async () => {
  for (const result of [json({}, 401), json({token:''}), json({token: 7}), json({token:'x'.repeat(4097)})]) {
    let calls = 0
    const client = new LabSession(typed(async () => { calls++; return result }), CKA_ORIGIN)
    await assert.rejects(client.request('prepare', {slug:'scale-payment-api'})); assert.equal(calls,1)
    client.dispose()
  }
})
test('logout or unmount prevents a late token from launching a lab request', async () => {
  let resolve!: (response: Response) => void, calls = 0
  const client = new LabSession(typed(async () => { calls++; return new Promise(r => { resolve = r }) }), CKA_ORIGIN)
  const pending = client.request('prepare', {slug:'scale-payment-api'})
  client.dispose(); resolve(json({token:'late-token'}))
  await assert.rejects(pending); assert.equal(calls,1)
  await assert.rejects(client.request('capabilities')); assert.equal(calls,1)
})
test('parallel operations and nonstaging origins are rejected', async () => {
  assert.throws(() => new LabSession(fetch, 'https://cka.shadowops.dpdns.org'))
  let resolve!: (response: Response) => void
  const client = new LabSession(typed(async () => new Promise(r => { resolve = r })), CKA_ORIGIN)
  const pending = client.request('capabilities')
  await assert.rejects(client.request('prepare', {slug:'scale-payment-api'}))
  client.dispose(); resolve(json({},401)); await assert.rejects(pending)
})
