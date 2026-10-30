import { useEffect, useRef, useState } from 'react'
import { AccountSession, LOGIN_URL, type AccountState } from './identitySession'

export function AccountAccess() {
  const [state, setState] = useState<AccountState>({ phase: 'checking' })
  const session = useRef<AccountSession | null>(null)
  useEffect(() => {
    const current = new AccountSession(fetch, setState)
    session.current = current
    void current.check()
    const timer = setInterval(() => { if (document.visibilityState === 'visible') void current.check() }, 60000)
    const visible = () => { if (document.visibilityState === 'visible') void current.check() }
    document.addEventListener('visibilitychange', visible)
    return () => { clearInterval(timer); document.removeEventListener('visibilitychange', visible); current.dispose() }
  }, [])
  return <section className="account-access" aria-label="Cuenta ShadowOps">
    <div className="account-copy"><span className="eyebrow">SHADOWOPS ID</span>
      <h2>{state.phase === 'authenticated' ? 'Tu cuenta, también en CKA.' : 'Una cuenta para ShadowOps.'}</h2>
      <p>{state.phase === 'authenticated' ? 'Sesión verificada. Puedes usar esta misma cuenta en Chronicles.' : 'Entra con la misma cuenta que usas en Chronicles, o créala una sola vez en ShadowOps.'}</p>
      <p className="account-status" role="status" aria-live="polite">{
        state.phase === 'checking' ? 'Comprobando tu sesión…' :
        state.phase === 'signing-out' ? 'Cerrando sesión en ShadowOps…' :
        state.phase === 'authenticated' ? 'Cuenta ShadowOps conectada · laboratorios aún no disponibles' :
        state.error || 'No has iniciado sesión.'
      }</p>
    </div>
    <div className="account-actions">
      {(state.phase === 'anonymous' || state.phase === 'unavailable') && <a className="button primary" href={LOGIN_URL}>Continuar con ShadowOps</a>}
      {state.phase === 'unavailable' && <button className="button ghost" onClick={() => void session.current?.check()}>Reintentar</button>}
      {(state.phase === 'authenticated' || state.phase === 'unavailable' || state.phase === 'logout-failed') && <button className="button ghost" onClick={() => void session.current?.logout()}>Cerrar sesión</button>}
    </div>
  </section>
}
