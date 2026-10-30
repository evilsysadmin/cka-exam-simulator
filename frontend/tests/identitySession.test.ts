import { test } from 'node:test'
import assert from 'node:assert/strict'
import { AccountSession, accountEnabled, IDENTITY, ACCOUNT_API, CKA_ORIGIN, type AccountState } from '../src/identitySession.ts'
const profile = { subject: '11111111-1111-4111-8111-111111111111', issuer: IDENTITY, audience: 'cka-simulator', labs_enabled: false }
const json = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status })
const typed = (f: (...args: Parameters<typeof fetch>) => Promise<Response>) => f as typeof fetch

test('requires exact staging origin and explicit build flag', () => {
  assert.equal(accountEnabled('central-staging', CKA_ORIGIN), true)
  for (const origin of ['https://cka.shadowops.dpdns.org', 'http://localhost:4173', 'https://staging.cka.shadowops.dpdns.org.evil.invalid']) assert.equal(accountEnabled('central-staging', origin), false)
  assert.equal(accountEnabled(undefined, CKA_ORIGIN), false)
})
test('exchanges only central cookie; account endpoint independently authenticates each check', async () => {
  const states: AccountState[] = [], calls: [string, RequestInit | undefined][] = []
  const session = new AccountSession(typed(async (url, options) => {
    calls.push([String(url), options])
    return String(url).startsWith(IDENTITY) ? json({ token: 'opaque-transport-token' }) : json(profile)
  }), s => states.push(s))
  await session.check(); await session.check()
  assert.equal(calls.length, 4)
  assert.deepEqual(calls[0][1]?.headers, { 'Content-Type': 'application/json' })
  assert.equal(calls[0][1]?.credentials, 'include'); assert.equal(calls[0][1]?.body, '{}')
  assert.equal(calls[1][0], ACCOUNT_API + '/api/account/me')
  assert.equal(calls[1][1]?.credentials, 'omit')
  assert.deepEqual(calls[1][1]?.headers, { Authorization: 'Bearer opaque-transport-token' })
  assert.deepEqual(states.at(-1), { phase: 'authenticated', subject: profile.subject })
  assert.equal(JSON.stringify(states).includes('opaque-transport-token'), false)
  session.dispose()
})
test('anonymous session never contacts the account API', async () => {
  let count = 0; const states: AccountState[] = []
  const session = new AccountSession(typed(async () => { count++; return json({}, 401) }), s => states.push(s))
  await session.check(); assert.equal(count, 1); assert.equal(states.at(-1)?.phase, 'anonymous'); session.dispose()
})
test('authority outage, malformed account and wrong audience cannot create a session', async () => {
  for (const response of [json({}, 503), json({ ...profile, audience: 'chronicles' }), json({ ...profile, labs_enabled: true }), json({ ...profile, subject: 'alice' })]) {
    const states: AccountState[] = []
    const session = new AccountSession(typed(async url => String(url).startsWith(IDENTITY) ? json({ token: 'transport' }) : response), s => states.push(s))
    await session.check(); assert.equal(states.at(-1)?.phase, 'unavailable'); session.dispose()
  }
})
test('logout prevents a late token/account response from resurrecting identity', async () => {
  const states: AccountState[] = []; let resolve!: (r: Response) => void
  const session = new AccountSession(typed(async url => String(url).endsWith('/logout') ? new Response(null, { status: 204 }) : new Promise(r => { resolve = r })), s => states.push(s))
  const pending = session.check(); await session.logout(); resolve(json({ token: 'late-token' })); await pending
  assert.equal(states.at(-1)?.phase, 'anonymous')
  assert.equal(states.some(s => s.phase === 'authenticated'), false)
  session.dispose()
})
test('failed logout stays explicit and suppresses silent reauthentication until retry', async () => {
  const states: AccountState[] = []; let calls = 0
  const session = new AccountSession(typed(async () => { calls++; return calls === 1 ? json({}, 503) : new Response(null, { status: 204 }) }), s => states.push(s))
  await session.logout(); assert.equal(states.at(-1)?.phase, 'logout-failed')
  await session.check(); assert.equal(calls, 1)
  await session.logout(); assert.equal(states.at(-1)?.phase, 'anonymous'); session.dispose()
})
test('unmount prevents a late response from changing account state', async () => {
  const states: AccountState[] = []; let resolve!: (r: Response) => void
  const session = new AccountSession(typed(async () => new Promise(r => { resolve = r })), s => states.push(s))
  const pending = session.check(); session.dispose(); resolve(json({}, 401)); await pending
  assert.deepEqual(states, [{ phase: 'checking' }])
})
