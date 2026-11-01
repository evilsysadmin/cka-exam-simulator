import { CKA_ORIGIN, IDENTITY } from './identitySession.ts'

export const LAB_API = 'https://cka-labs-staging.shadowops.dpdns.org'
export type LabAction = 'capabilities' | 'prepare' | 'status' | 'command' | 'grade' | 'finish'
export class LabRequestError extends Error {
  status: number
  constructor(status: number) {
    super(status === 401 ? 'Sesión o acceso al laboratorio no disponible.' :
      status === 429 ? 'Demasiadas operaciones. Espera un minuto para continuar.' :
      status === 409 ? 'Hay un intento u operación en curso. Espera o termina el intento activo.' :
      status === 400 ? 'Ese comando no está permitido en este laboratorio.' :
      'El laboratorio no está disponible. Reintenta en unos instantes.')
    this.status = status
  }
}

// No token storage, decoded browser claims, owner UUID or arbitrary API URL.
// Every operation exchanges the central cookie again; the server independently
// checks its signature, audience and exact live account/session/JTI state.
export class LabSession {
  private pending: AbortController | null = null
  private disposed = false
  private epoch = 0
  private fetcher: typeof fetch
  constructor(fetcher: typeof fetch, origin: string) {
    if (origin !== CKA_ORIGIN) throw new LabRequestError(403)
    this.fetcher = (input, options) => fetcher.call(globalThis, input, options)
  }
  dispose() { this.disposed = true; this.epoch++; this.pending?.abort() }
  async request<T>(action: LabAction, data: Record<string, string> = {}): Promise<T> {
    if (this.disposed) throw new LabRequestError(401)
    if (this.pending) throw new LabRequestError(409)
    const epoch = ++this.epoch, controller = new AbortController()
    this.pending = controller
    const timer = setTimeout(() => controller.abort(), action === 'prepare' || action === 'finish' ? 75000 : 20000)
    const current = () => {
      if (this.disposed || epoch !== this.epoch || controller.signal.aborted) throw new LabRequestError(401)
    }
    try {
      const exchanged = await this.fetcher(IDENTITY + '/api/identity/token', {
        method: 'POST', credentials: 'include', headers: { 'Content-Type': 'application/json' },
        body: '{}', signal: controller.signal,
      })
      current()
      if (!exchanged.ok) throw new LabRequestError(exchanged.status)
      const token = await exchanged.json() as { token?: unknown }
      current()
      if (typeof token.token !== 'string' || !token.token || token.token.length > 4096) throw new LabRequestError(503)
      const response = await this.fetcher(LAB_API + '/api/labs/' + action, {
        method: 'POST', credentials: 'omit', cache: 'no-store',
        headers: { 'Content-Type': 'application/json', Authorization: 'Bearer ' + token.token },
        body: JSON.stringify(data), signal: controller.signal,
      })
      current()
      if (!response.ok) throw new LabRequestError(response.status)
      const raw = await response.text()
      current()
      if (raw.length > 262144) throw new LabRequestError(503)
      return JSON.parse(raw) as T
    } catch (error) {
      if (error instanceof LabRequestError) throw error
      throw new LabRequestError(503)
    } finally {
      clearTimeout(timer)
      if (this.pending === controller) this.pending = null
    }
  }
}
