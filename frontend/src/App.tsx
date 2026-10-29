import { useEffect, useMemo, useState } from 'react'
import TerminalPanel from './Terminal'

type Scenario = {
  slug: string
  title: string
  story: string
  difficulty: 'easy' | 'medium' | 'hard'
  domain: string
  minutes: number
  statement: string
  namespace: string
}
type Grade = {
  passed: boolean
  score: number
  checks: { name: string; passed: boolean }[]
}
type Phase = 'idle' | 'preparing' | 'active' | 'expired'
const HEADER = { 'X-CKA-Lab-Request': '1' }

async function post<T>(path: string, payload?: object): Promise<T> {
  const response = await fetch(path, {
    method: 'POST',
    headers: { ...HEADER, ...(payload ? { 'Content-Type': 'application/json' } : {}) },
    body: payload ? JSON.stringify(payload) : undefined,
  })
  if (!response.ok) {
    const body: unknown = await response.json().catch(() => ({}))
    const message = body && typeof body === 'object' && 'detail' in body
      ? String(body.detail)
      : 'HTTP ' + response.status
    throw new Error(message)
  }
  return response.json() as Promise<T>
}

function prettyTime(seconds: number) {
  const minutes = Math.floor(seconds / 60).toString().padStart(2, '0')
  const secs = (seconds % 60).toString().padStart(2, '0')
  return minutes + ':' + secs
}

export default function App() {
  const [scenarios, setScenarios] = useState<Scenario[]>([])
  const [selectedSlug, setSelectedSlug] = useState('')
  const [phase, setPhase] = useState<Phase>('idle')
  const [deadline, setDeadline] = useState<number | null>(null)
  const [remaining, setRemaining] = useState<number | null>(null)
  const [grade, setGrade] = useState<Grade | null>(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)
  const [grading, setGrading] = useState(false)

  useEffect(() => {
    let alive = true
    fetch('/api/scenarios')
      .then(async (r) => {
        if (!r.ok) throw new Error('API unavailable (HTTP ' + r.status + ')')
        return r.json() as Promise<Scenario[]>
      })
      .then((data) => {
        if (!alive) return
        setScenarios(data)
        if (data.length) setSelectedSlug(data[0].slug)
      })
      .catch((e: unknown) => {
        if (alive) setError(e instanceof Error ? e.message : 'Unable to load scenarios')
      })
      .finally(() => { if (alive) setLoading(false) })
    return () => { alive = false }
  }, [])

  const current = useMemo(
    () => scenarios.find((scenario) => scenario.slug === selectedSlug),
    [scenarios, selectedSlug],
  )

  useEffect(() => {
    if (deadline === null) return
    const tick = () => {
      const left = Math.max(0, Math.ceil((deadline - Date.now()) / 1000))
      setRemaining(left)
      if (left === 0) setPhase((previous) => previous === 'active' ? 'expired' : previous)
    }
    tick()
    const timer = window.setInterval(tick, 1000)
    return () => window.clearInterval(timer)
  }, [deadline])

  async function prepare() {
    if (!current || phase === 'preparing') return
    setError('')
    setGrade(null)
    setPhase('preparing')
    setDeadline(null)
    setRemaining(null)
    try {
      await post('/api/dev/scenarios/' + current.slug + '/setup')
      const until = Date.now() + current.minutes * 60_000
      setDeadline(until)
      setRemaining(current.minutes * 60)
      setPhase('active')
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not prepare lab')
      setPhase('idle')
    }
  }

  async function checkGrade() {
    if (!current) return
    setGrading(true)
    setError('')
    try {
      const result = await post<Grade>('/api/dev/scenarios/' + current.slug + '/grade')
      setGrade(result)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Grading failed')
    } finally {
      setGrading(false)
    }
  }

  async function runCommand(command: string): Promise<string> {
    const result = await post<{ output: string }>('/api/dev/terminal/command', { command })
    return result.output || '(command completed without output)'
  }

  function selectScenario(slug: string) {
    setSelectedSlug(slug)
    setPhase('idle')
    setDeadline(null)
    setRemaining(null)
    setGrade(null)
    setError('')
  }

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <div className="brand-mark">K<span>8</span>S</div>
          <div><div className="brand-heading">CKA <span>// LAB</span></div>
            <div className="brand-subtitle">KUBERNETES · HANDS-ON TRAINING</div></div>
        </div>
        <div className="topbar-end">
          <span className="environment-dot" /> <span className="environment">LOCAL DEV PREVIEW</span>
          <span className="version">v0.3</span>
        </div>
      </header>

      <main className="main">
        <section className="intro">
          <div>
            <div className="eyebrow">THE PRACTICE GROUND</div>
            <h1>Learn by <em>doing.</em></h1>
            <p>Real Kubernetes. Real incidents. No multiple-choice shortcuts.</p>
          </div>
          <div className="run-overview">
            <span>EXAM MODE</span><strong>CKA</strong><small>Scenario-driven labs</small>
          </div>
        </section>

        <div className="workspace">
          <aside className="sidebar">
            <div className="section-heading"><span>SCENARIO BANK</span><span className="count">{scenarios.length.toString().padStart(2, '0')}</span></div>
            {loading && <p className="muted">Loading challenges…</p>}
            {scenarios.map((scenario, index) => (
              <button key={scenario.slug}
                className={'scenario-choice ' + (selectedSlug === scenario.slug ? 'selected' : '')}
                onClick={() => selectScenario(scenario.slug)}>
                <span className="scenario-number">{String(index + 1).padStart(2, '0')}</span>
                <span className="scenario-copy"><strong>{scenario.title}</strong><small>{scenario.domain} · {scenario.minutes} min</small></span>
                <span className="choice-arrow">›</span>
              </button>
            ))}
            <div className="sidebar-footer">
              <span className="green-dot" /> <span>CURATED SCENARIOS</span>
              <p>AI-generated proposals require validation before appearing here.</p>
            </div>
          </aside>

          <section className="lab">
            {current ? (
              <>
                <div className="lab-header">
                  <div className="lab-title">
                    <span className="lab-kicker">MISSION BRIEFING <span className="separator">/</span> {current.domain.toUpperCase()}</span>
                    <h2>{current.title}</h2>
                    <div className="tags"><span>{current.difficulty}</span><span>{current.namespace}</span></div>
                  </div>
                  <div className={'clock ' + (remaining !== null && remaining < 60 ? 'urgent' : '')}>
                    <small>TIME REMAINING</small>
                    <strong>{prettyTime(remaining ?? current.minutes * 60)}</strong>
                    <span>{phase === 'active' ? '● RUNNING' : phase === 'expired' ? '● TIME UP' : '○ STANDBY'}</span>
                  </div>
                </div>

                <div className="mission">
                  <div className="mission-icon">!</div>
                  <div><strong>YOUR OBJECTIVE</strong><p>{current.story}</p><p className="goal">{current.statement}</p></div>
                </div>

                <div className="console-header">
                  <div><span className="console-dot" /><span className="console-dot" /><span className="console-dot" />
                    <span className="console-label"> TERMINAL / kind-cka-dev</span></div>
                  <span className="console-kind">kubectl · restricted preview</span>
                </div>

                <div className="console-area">
                  {phase === 'active' || phase === 'expired'
                    ? <TerminalPanel key={current.slug} scenario={current.slug} onCommand={runCommand} />
                    : <div className="console-empty"><span className="console-placeholder">&gt;_</span>
                        <strong>Lab environment is on standby</strong><small>Prepare the scenario to activate the console.</small>
                      </div>}
                </div>

                <div className="action-bar">
                  <div className="action-left"><span className="green-dot" />
                    {phase === 'active' ? 'LAB ACTIVE' : phase === 'preparing' ? 'PROVISIONING…' : 'STANDBY'}
                  </div>
                  <div className="action-buttons">
                    <button className="button ghost" disabled={grading || (phase !== 'active' && phase !== 'expired')}
                      onClick={() => { void checkGrade() }}>{grading ? 'Checking…' : 'Check solution'}</button>
                    <button className="button primary" disabled={phase === 'preparing'}
                      onClick={() => { void prepare() }}>{phase === 'preparing' ? 'Preparing…' : 'Prepare lab'}</button>
                  </div>
                </div>

                {grade && <div className={'grade ' + (grade.passed ? 'passed' : 'failed')}>
                  <strong>{grade.passed ? '✓ All checks passed' : '✕ Not solved yet'} · {grade.score}/100</strong>
                  {grade.checks.map((check) => <div key={check.name}>{check.passed ? '✓' : '✕'} {check.name}</div>)}
                </div>}
              </>
            ) : (
              <div className="no-scenario">No scenario selected. Start the backend API to load the curated bank.</div>
            )}
            {error && <div className="error" role="alert">{error}</div>}
          </section>
        </div>
        <footer className="footer">CKA // LAB <span>•</span> DEVELOPER PROTOTYPE — NOT A SECURE PUBLIC EXAM SERVICE</footer>
      </main>
    </div>
  )
}
