import React, { useState } from 'react'
import { createRoot } from 'react-dom/client'
import './style.css'

const cases = [
  { slug: 'scale-payment-api', title: 'Caída de capacidad en pagos', domain: 'workloads', minutes: 6,
    namespace: 'cka-payments', difficulty: 'easy',
    story: 'La API de pagos necesita recuperar capacidad sin sustituir el Deployment.',
    task: 'Ajusta payment-api a tres réplicas disponibles.' },
  { slug: 'fix-catalog-service', title: 'Servicio de catálogo sin backends', domain: 'networking', minutes: 9,
    namespace: 'cka-catalog', difficulty: 'medium',
    story: 'Los pods del catálogo están sanos, pero el Service no encuentra destinos.',
    task: 'Repara catalog-api-svc para que seleccione los pods correctamente.' },
] as const

function Preview() {
  const [selected, setSelected] = useState(0)
  const current = cases[selected]
  return <div className="app">
    <header className="topbar">
      <div className="brand"><div className="brand-mark">K<span>8</span>S</div>
        <div><div className="brand-heading">CKA <span>// LAB</span></div>
          <div className="brand-subtitle">KUBERNETES · HANDS-ON TRAINING</div></div></div>
      <div className="topbar-end"><span className="environment-dot"/>
        <span className="environment">PUBLIC PREVIEW · READ ONLY</span><span className="version">ALPHA</span></div>
    </header>
    <main className="main">
      <section className="intro"><div><div className="eyebrow">THE PRACTICE GROUND</div>
        <h1>Learn by <em>doing.</em></h1>
        <p>Simulador CKA con laboratorios reales y corrección automática, actualmente en desarrollo.</p>
      </div><div className="run-overview"><span>PREVIEW</span><strong>CKA</strong><small>Scenarios</small></div></section>
      <div role="status" style={{ border:'1px solid #397965',background:'#12372d',padding:16,
        borderRadius:10, marginBottom:18, fontSize:13, lineHeight:1.65 }}>
        <strong>Demostración pública de solo lectura.</strong> Puedes explorar los ejercicios, pero
        no hay Kubernetes ni consola ejecutable en este dominio. Los laboratorios se habilitarán
        cuando existan autenticación y aislamiento seguro por sesión.
      </div>
      <div className="workspace">
        <aside className="sidebar">
          <div className="section-heading"><span>SCENARIO BANK</span><span className="count">02</span></div>
          {cases.map((s,i)=><button key={s.slug} type="button" aria-pressed={selected===i}
            className={'scenario-choice '+(selected===i?'selected':'')} onClick={()=>setSelected(i)}>
            <span className="scenario-number">{String(i+1).padStart(2,'0')}</span>
            <span className="scenario-copy"><strong>{s.title}</strong><small>{s.domain} · {s.minutes} min</small></span>
            <span className="choice-arrow">›</span></button>)}
          <div className="sidebar-footer"><span className="green-dot"/> SCENARIOS VERIFIED IN KIND
            <p>Estos escenarios tienen pruebas de integración en Kubernetes real. Esta vista no ejecuta nada.</p></div>
        </aside>
        <section className="lab">
          <div className="lab-header"><div className="lab-title">
            <span className="lab-kicker">MISSION BRIEFING / {current.domain.toUpperCase()}</span>
            <h2>{current.title}</h2><div className="tags"><span>{current.difficulty}</span><span>{current.namespace}</span></div>
          </div><div className="clock"><small>ESTIMATED TIME</small>
            <strong>{String(current.minutes).padStart(2,'0')}:00</strong><span>○ PREVIEW</span></div></div>
          <div className="mission"><div className="mission-icon">!</div><div>
            <strong>YOUR OBJECTIVE</strong><p>{current.story}</p><p className="goal">{current.task}</p></div></div>
          <div className="console-header"><div><span className="console-dot"/>
            <span className="console-dot"/><span className="console-dot"/>
            <span className="console-label"> TERMINAL / preview</span></div>
            <span className="console-kind">offline · no kubectl</span></div>
          <div className="console-area" style={{ padding:22, fontFamily:'monospace', color:'#8aa9b7', lineHeight:2 }}>
            <div><span style={{color:'#76dfb3'}}>cka@lab:~$ </span>kubectl get pods -n {current.namespace}</div>
            <div>[ Terminal desactivada en la preview pública ]</div>
          </div>
          <div className="action-bar"><div className="action-left"><span className="green-dot"/> READ-ONLY DEMO</div>
            <div className="action-buttons"><button className="button ghost" disabled>Check solution</button>
              <button className="button primary" disabled>Prepare lab</button></div></div>
        </section>
      </div>
      <footer className="footer">CKA // LAB · SHADOWOPS · STATIC PREVIEW · NO CLUSTER ACCESS</footer>
    </main>
  </div>
}
createRoot(document.getElementById('root')!).render(<React.StrictMode><Preview/></React.StrictMode>)
