import { useEffect, useRef, useState } from 'react'
import TerminalPanel from './Terminal'
import { LabRequestError, LabSession } from './labSession'

type Scenario = { slug: string; title: string; story: string; domain: string; difficulty: string;
  minutes: number; statement: string; namespace: string }
type Attempt = { scenario: Scenario; session: { id: string; scenario_slug: string;
  phase: 'active' | 'expired' | 'closed'; remaining_seconds: number; expires_at: string } }
type Capabilities = { labs_enabled: true; capacity: 1; scenarios: Scenario[]; current: Attempt | null }
type Grade = { passed: boolean; score: number; checks: { name: string; passed: boolean }[] }

function validScenario(value: Scenario) {
  return value && ['slug','title','story','domain','difficulty','statement','namespace'].every(
    key => typeof value[key as keyof Scenario] === 'string' && String(value[key as keyof Scenario]).length <= 600)
    && /^[a-z][a-z0-9-]{1,43}$/.test(value.slug) && Number.isInteger(value.minutes) && value.minutes >= 3 && value.minutes <= 20
}
function validAttempt(value: Attempt | null) {
  return value === null || (value && validScenario(value.scenario) && value.session &&
    /^[a-f0-9]{32}$/.test(value.session.id) && value.session.scenario_slug === value.scenario.slug &&
    /^cka-lab-[a-f0-9]{32}$/.test(value.scenario.namespace) &&
    ['active','expired','closed'].includes(value.session.phase) && Number.isInteger(value.session.remaining_seconds) &&
    value.session.remaining_seconds >= 0 && value.session.remaining_seconds <= 1200)
}

export function PilotLabs() {
  const transport = useRef<LabSession | null>(null)
  const busyRef = useRef(false)
  const [capabilities, setCapabilities] = useState<Capabilities | null>(null)
  const [attempt, setAttempt] = useState<Attempt | null>(null)
  const [selected, setSelected] = useState('')
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')
  const [deadline, setDeadline] = useState<number | null>(null)
  const [remaining, setRemaining] = useState(0)
  const [grade, setGrade] = useState<Grade | null>(null)

  function restore(value: Attempt | null) {
    if (!validAttempt(value)) throw Error('Respuesta de sesión no válida')
    setAttempt(value)
    setDeadline(value ? Date.now() + value.session.remaining_seconds * 1000 : null)
    setRemaining(value?.session.remaining_seconds ?? 0)
    if (value) setSelected(value.scenario.slug)
  }
  async function refresh(client: LabSession) {
    const result = await client.request<Capabilities>('capabilities')
    if (result.labs_enabled !== true || result.capacity !== 1 || !Array.isArray(result.scenarios) ||
      !result.scenarios.length || result.scenarios.length > 16 || !result.scenarios.every(validScenario) ||
      !validAttempt(result.current)) throw Error('Laboratorio no disponible')
    setCapabilities(result)
    restore(result.current)
    setSelected(previous => result.current?.scenario.slug || previous || result.scenarios[0].slug)
  }
  useEffect(() => {
    const client = new LabSession(fetch, location.origin)
    transport.current = client
    let alive = true
    const check = async () => {
      if (busyRef.current) return
      busyRef.current = true; setBusy('Comprobando acceso…')
      try { await refresh(client); if (alive) setError('') }
      catch (e) {
        if (alive) {
          setError(e instanceof Error ? e.message : 'Laboratorio no disponible')
          // A temporary 409/503 on the background poll must not destroy a
          // still-live terminal, its scrollback or the local countdown. The
          // server still authorizes every command against fresh Identity state.
          // Auth denial or malformed data must immediately remove the view.
          if (!(e instanceof LabRequestError && [409, 503].includes(e.status))) {
            setCapabilities(null); restore(null); setGrade(null)
          }
        }
      }
      finally { if (alive) { busyRef.current = false; setBusy('') } }
    }
    void check()
    const timer = setInterval(() => { if (document.visibilityState === 'visible') void check() }, 20000)
    const visible = () => { if (document.visibilityState === 'visible') void check() }
    document.addEventListener('visibilitychange', visible)
    return () => { alive = false; busyRef.current = false; clearInterval(timer); document.removeEventListener('visibilitychange', visible); client.dispose() }
  }, [])
  useEffect(() => {
    if (deadline === null) return
    const tick = () => setRemaining(Math.max(0, Math.ceil((deadline - Date.now()) / 1000)))
    tick(); const timer = setInterval(tick, 1000)
    return () => clearInterval(timer)
  }, [deadline])

  async function perform<T>(label: string, action: () => Promise<T>): Promise<T> {
    if (busyRef.current) throw Error('Espera a que termine la operación en curso.')
    busyRef.current = true; setBusy(label); setError('')
    try { return await action() }
    catch (e) { setError(e instanceof Error ? e.message : 'No se pudo completar la operación'); throw e }
    finally { busyRef.current = false; setBusy('') }
  }
  const current = attempt?.scenario || capabilities?.scenarios.find(s => s.slug === selected)
  const active = attempt?.session.phase === 'active' && remaining > 12
  async function prepare() {
    if (!current || !transport.current) return
    await perform('Preparando laboratorio…', async () => {
      const result = await transport.current!.request<Attempt>('prepare', { slug: current.slug })
      restore(result); setGrade(null)
    }).catch(() => {})
  }
  async function finish() {
    if (!attempt || !transport.current) return
    await perform('Limpiando el intento…', async () => {
      const result = await transport.current!.request<{status: string}>('finish', { id: attempt.session.id })
      if (result.status !== 'closed') throw Error('Limpieza aún no confirmada')
      restore(null); setGrade(null)
    }).catch(() => {})
  }
  async function checkGrade() {
    if (!attempt || !transport.current) return
    await perform('Comprobando el resultado…', async () => {
      const result = await transport.current!.request<Grade>('grade', { id: attempt.session.id })
      if (typeof result.passed !== 'boolean' || ![0,100].includes(result.score) || !Array.isArray(result.checks) ||
        result.checks.length > 10 || result.checks.some(c => typeof c.name !== 'string' || typeof c.passed !== 'boolean')) throw Error('Resultado no disponible')
      setGrade(result)
    }).catch(() => {})
  }
  async function command(line: string) {
    if (!attempt || !active || !transport.current) throw Error('El intento ha terminado.')
    return perform('Ejecutando comando…', async () => {
      const result = await transport.current!.request<{output: string}>('command', { id: attempt.session.id, command: line })
      if (typeof result.output !== 'string' || result.output.length > 131072) throw Error('Salida no disponible')
      return result.output || '(comando completado sin salida)'
    })
  }

  if (!capabilities || !current) return <section className="account-access" aria-label="Laboratorios CKA">
    <div className="account-copy"><h2>Laboratorios de staging</h2><p role="status">{error || 'Comprobando acceso a los laboratorios…'}</p></div>
  </section>
  return <section className="workspace" aria-label="Laboratorios CKA">
    <aside className="sidebar"><div className="section-heading"><span>EJERCICIOS</span><span className="count">{capabilities.scenarios.length}</span></div>
      {capabilities.scenarios.map((s, index) => <button type="button" key={s.slug}
        disabled={Boolean(attempt) || Boolean(busy)} aria-pressed={current.slug === s.slug}
        className={'scenario-choice ' + (current.slug === s.slug ? 'selected' : '')}
        onClick={() => { setSelected(s.slug); setGrade(null); setError('') }}>
        <span className="scenario-number">{String(index + 1).padStart(2,'0')}</span>
        <span className="scenario-copy"><strong>{s.title}</strong><small>{s.domain} · {s.minutes} min</small></span>
      </button>)}
      <div className="sidebar-footer"><span className="green-dot" /> UN INTENTO ACTIVO
        <p>Termina tu intento para cambiar de ejercicio. Al agotarse el tiempo, el entorno se limpia automáticamente.</p></div>
    </aside>
    <div className="lab"><div className="lab-header"><div className="lab-title">
      <span className="lab-kicker">TU MISIÓN / {current.domain.toUpperCase()}</span><h2>{current.title}</h2>
      <div className="tags"><span>{current.difficulty}</span><span>{current.namespace}</span></div>
    </div><div className={'clock ' + (attempt && remaining < 60 ? 'urgent' : '')}>
      <small>TIEMPO RESTANTE</small><strong>{String(Math.floor((attempt ? remaining : current.minutes*60)/60)).padStart(2,'0')}:{String((attempt ? remaining : current.minutes*60)%60).padStart(2,'0')}</strong>
      <span>{active ? '● EN CURSO' : attempt ? '○ FINALIZANDO' : '○ PREPARADO'}</span></div></div>
      <div className="mission"><div className="mission-icon">!</div><div><strong>OBJETIVO</strong>
        <p>{current.story}</p><p className="goal">{current.statement}</p></div></div>
      <div className="console-header"><div><span className="console-dot"/><span className="console-dot"/><span className="console-dot"/>
        <span className="console-label"> TERMINAL / {attempt ? current.namespace : 'en espera'}</span></div></div>
      <div className="console-area">{active ? <TerminalPanel key={attempt!.session.id} staging scenario={current.slug} onCommand={command}/>
        : <div className="console-empty"><span className="console-placeholder">&gt;_</span>
          <strong>{attempt ? 'El intento está terminando' : 'Prepara el ejercicio para empezar'}</strong>
          <small>{attempt ? 'Puedes finalizarlo y comprobar de nuevo el acceso cuando termine la limpieza.' : 'Trabajarás con recursos reales de Kubernetes.'}</small></div>}</div>
      <div className="action-bar"><div className="action-left" role="status">{busy || (active ? 'LABORATORIO ACTIVO' : 'EN ESPERA')}</div>
        <div className="action-buttons"><button className="button ghost" disabled={!active || Boolean(busy)} onClick={() => void checkGrade()}>Comprobar resultado</button>
          {attempt ? <button className="button primary" disabled={Boolean(busy)} onClick={() => void finish()}>Finalizar intento</button>
            : <button className="button primary" disabled={Boolean(busy)} onClick={() => void prepare()}>Preparar laboratorio</button>}</div></div>
      {grade && <div className={'grade ' + (grade.passed ? 'passed' : 'failed')} role="status">
        <strong>{grade.passed ? '✓ Ejercicio resuelto' : 'Todavía no está resuelto'} · {grade.score}/100</strong>
        {grade.checks.map(c => <div key={c.name}>{c.passed ? '✓' : '✕'} {c.name}</div>)}</div>}
      {error && <div className="error" role="alert">{error}</div>}
    </div>
  </section>
}
