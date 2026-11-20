import assert from 'node:assert/strict'
import { CKA_TERMINAL, terminalPrompt, terminalPromptWidth, terminalWelcome } from '../src/terminalPresentation.ts'
import type { TerminalPresentation } from '../src/terminalPresentation.ts'

assert.equal(terminalPrompt(CKA_TERMINAL), '\x1b[32mcka@lab\x1b[0m:\x1b[34m~\x1b[0m$ ')
assert.equal(terminalPromptWidth(CKA_TERMINAL), 11)
assert.deepEqual(terminalWelcome(CKA_TERMINAL, 'scale-payment-api', {local:true,staging:true}), [
  '\x1b[1mCKA // LAB\x1b[0m  |  LOCAL',
  'Scenario: scale-payment-api',
  'kubectl o k · kubectl edit con nano · filtra la salida con | grep',
  'Prueba: kubectl get pods',
])
assert.equal(terminalWelcome(CKA_TERMINAL, 'test', {local:false,staging:false})[3], 'Try: kubectl get pods -A')

const linux: TerminalPresentation = {
  banner: 'GAPFORGE // LINUX',
  user: 'engineer', host: 'sandbox', cwd: '/srv',
  ariaLabel: 'Terminal Linux aislada',
  instructions: {connected: 'Investiga el incidente con comandos Linux.', preview: 'Sesión aún no iniciada.'},
  examples: {connected: 'Prueba: journalctl -u demo', preview: 'Prepara la misión.'},
}
assert.equal(terminalPromptWidth(linux), 20)
assert.ok(terminalPrompt(linux).includes('engineer@sandbox'))
assert.deepEqual(terminalWelcome(linux, 'disk-full', {local:true,staging:true}).slice(1), [
  'Scenario: disk-full', 'Investiga el incidente con comandos Linux.', 'Prueba: journalctl -u demo',
])
console.log('Terminal presentation profiles: OK')
