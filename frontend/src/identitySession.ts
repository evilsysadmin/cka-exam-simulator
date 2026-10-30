export const IDENTITY = 'https://id-staging.shadowops.dpdns.org'
export const CKA_ORIGIN = 'https://staging.cka.shadowops.dpdns.org'
export const ACCOUNT_API = 'https://cka-api-staging.shadowops.dpdns.org'
export const LOGIN_URL = IDENTITY + '/login?app=cka-simulator'
export type AccountState = { phase: 'checking' | 'anonymous' | 'authenticated' | 'unavailable' | 'signing-out' | 'logout-failed'; subject?: string; error?: string }
export function accountEnabled(flag: string | undefined, origin: string) {
  return flag === 'central-staging' && origin === CKA_ORIGIN
}

// Tokens stay inside a single bounded check, never in storage or a URL. Only
// the account API can authenticate the UUID; no browser claim grants a lab.
export class AccountSession {
  private epoch = 0
  private pending: AbortController | null = null
  private leaving = false
  private fetcher: typeof fetch
  private changed: (state: AccountState) => void
  constructor(fetcher: typeof fetch, changed: (state: AccountState) => void) {
    this.fetcher = (input, options) => fetcher.call(globalThis, input, options)
    this.changed = changed
  }
  dispose() { this.epoch++; this.pending?.abort() }
  async check() {
    if (this.leaving) return
    const epoch = ++this.epoch
    this.pending?.abort()
    const controller = new AbortController()
    this.pending = controller
    const timer = setTimeout(() => controller.abort(), 8000)
    const commit = (state: AccountState) => { if (epoch === this.epoch) this.changed(state) }
    commit({ phase: 'checking' })
    try {
      const token = await this.fetcher(IDENTITY + '/api/identity/token', {
        method: 'POST', credentials: 'include', headers: { 'Content-Type': 'application/json' },
        body: '{}', signal: controller.signal,
      })
      if (epoch !== this.epoch) return
      if (controller.signal.aborted) throw Error('late token')
      if (token.status === 401) { commit({ phase: 'anonymous' }); return }
      if (!token.ok) throw Error('identity unavailable')
      const payload = await token.json()
      if (typeof payload.token !== 'string' || payload.token.length > 4096 || !payload.token) throw Error('invalid token transport')
      const me = await this.fetcher(ACCOUNT_API + '/api/account/me', {
        credentials: 'omit', headers: { Authorization: 'Bearer ' + payload.token }, signal: controller.signal,
      })
      if (!me.ok) throw Error('account unavailable')
      const profile = await me.json()
      if (profile.issuer !== IDENTITY || profile.audience !== 'cka-simulator' || profile.labs_enabled !== false ||
        typeof profile.subject !== 'string' || !/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/.test(profile.subject)) throw Error('invalid account response')
      if (controller.signal.aborted) throw Error('late check')
      commit({ phase: 'authenticated', subject: profile.subject })
    } catch {
      commit({ phase: 'unavailable', error: 'No se pudo comprobar tu cuenta. Reintenta en unos instantes.' })
    } finally { clearTimeout(timer) }
  }
  async logout() {
    this.leaving = true
    const epoch = ++this.epoch
    this.pending?.abort()
    const controller = new AbortController()
    this.pending = controller
    const timer = setTimeout(() => controller.abort(), 8000)
    this.changed({ phase: 'signing-out' })
    try {
      const response = await this.fetcher(IDENTITY + '/api/identity/logout', {
        method: 'POST', credentials: 'include', headers: { 'Content-Type': 'application/json' },
        body: '{}', signal: controller.signal, keepalive: true,
      })
      if (response.status !== 204 || controller.signal.aborted) throw Error('logout unconfirmed')
      if (epoch === this.epoch) this.changed({ phase: 'anonymous' })
    } catch {
      if (epoch === this.epoch) this.changed({ phase: 'logout-failed', error: 'No se pudo confirmar el cierre de sesión. Vuelve a pulsar Cerrar sesión.' })
    } finally { clearTimeout(timer) }
  }
}
