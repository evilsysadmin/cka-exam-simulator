import { useEffect, useState } from 'react'
import { checkRelease, RELEASE_CHECK_MS } from './releaseUpdate'

export function ReleaseUpdate({ editing = false }: { editing?: boolean }) {
  const [latest, setLatest] = useState<string | null>(null)
  useEffect(() => {
    const current = import.meta.env.VITE_BUILD_SHA || 'development'
    if (!/^[a-f0-9]{40}$/.test(current)) return
    let pending: AbortController | null = null
    let disposed = false
    const check = async () => {
      if (pending || document.visibilityState !== 'visible') return
      const controller = new AbortController(); pending = controller
      const timer = setTimeout(() => controller.abort(), 7000)
      try {
        const release = await checkRelease(fetch, current, controller.signal)
        if (!disposed && release) setLatest(release)
      } finally { clearTimeout(timer); if (pending === controller) pending = null }
    }
    void check()
    const interval = setInterval(() => void check(), RELEASE_CHECK_MS)
    const visible = () => void check()
    document.addEventListener('visibilitychange', visible)
    return () => { disposed = true; clearInterval(interval); pending?.abort(); document.removeEventListener('visibilitychange', visible) }
  }, [])
  if (!latest) return null
  return <div className="release-update" role="status">
    <div><strong>Nueva versión disponible</strong><span>{editing ? 'Sal del editor para recargar.' : 'Recarga cuando quieras para usar la actualización.'}</span></div>
    <button className="button ghost" type="button" disabled={editing} onClick={() => { if (!editing && !document.querySelector('[data-editor-active="true"]')) location.reload() }}>Recargar</button>
  </div>
}
