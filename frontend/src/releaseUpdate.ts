export const RELEASE_CHECK_MS = 60000
export function newerRelease(value: unknown, current: string): string | null {
  if (!/^[a-f0-9]{40}$/.test(current) || !value || typeof value !== 'object') return null
  const release = value as Record<string, unknown>
  return release.application === 'cka-exam-simulator' &&
    ['restricted-lab-staging', 'account-staging-preview', 'static-readonly-preview'].includes(String(release.mode)) &&
    typeof release.build === 'string' && /^[a-f0-9]{40}$/.test(release.build) && release.build !== current ? release.build : null
}
export async function checkRelease(fetcher: typeof fetch, current: string, signal: AbortSignal): Promise<string | null> {
  try {
    const response = await fetcher('/release.json?ts=' + Date.now(), {
      method: 'GET', cache: 'no-store', credentials: 'omit', redirect: 'error', signal,
      headers: { Accept: 'application/json' },
    })
    if (!response.ok) return null
    const raw = await response.text()
    return raw.length <= 4096 ? newerRelease(JSON.parse(raw), current) : null
  } catch { return null }
}
