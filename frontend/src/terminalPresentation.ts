/** Frontend presentation only. The backend still controls every executable command. */
export type TerminalPresentation = Readonly<{
  banner: string
  user: string
  host: string
  cwd: string
  ariaLabel: string
  instructions: Readonly<{ connected: string; preview: string }>
  examples: Readonly<{ connected: string; preview: string }>
}>

/** Legacy CKA defaults are intentionally unchanged while the lab runner is generalized. */
export const CKA_TERMINAL: TerminalPresentation = Object.freeze({
  banner: 'CKA // LAB',
  user: 'cka',
  host: 'lab',
  cwd: '~',
  ariaLabel: 'Terminal Kubernetes',
  instructions: {
    connected: 'kubectl o k · kubectl edit con nano · filtra la salida con | grep',
    preview: 'Allowed: kubectl get / describe / scale / patch / rollout / explain / set resources',
  },
  examples: {
    connected: 'Prueba: kubectl get pods',
    preview: 'Try: kubectl get pods -A',
  },
})

export function terminalPrompt(presentation: TerminalPresentation): string {
  return '\x1b[32m' + presentation.user + '@' + presentation.host +
    '\x1b[0m:\x1b[34m' + presentation.cwd + '\x1b[0m$ '
}

/** Visible character count, excluding ANSI escapes, for wrapped input repaint. */
export function terminalPromptWidth(presentation: TerminalPresentation): number {
  return presentation.user.length + presentation.host.length + presentation.cwd.length + 4
}

export function terminalWelcome(
  presentation: TerminalPresentation,
  scenario: string,
  mode: { local: boolean; staging: boolean },
): string[] {
  return [
    '\x1b[1m' + presentation.banner + '\x1b[0m  |  ' +
      (mode.local ? 'LOCAL' : mode.staging ? 'STAGING' : 'LOCAL PREVIEW'),
    'Scenario: ' + scenario,
    mode.staging ? presentation.instructions.connected : presentation.instructions.preview,
    mode.staging ? presentation.examples.connected : presentation.examples.preview,
  ]
}
