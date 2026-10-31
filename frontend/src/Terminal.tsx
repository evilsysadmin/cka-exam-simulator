import { useEffect, useRef } from 'react'
import { Terminal } from '@xterm/xterm'
import { FitAddon } from '@xterm/addon-fit'
import '@xterm/xterm/css/xterm.css'

type Props = { onCommand: (command: string) => Promise<string>; scenario: string; staging?: boolean }

export default function TerminalPanel({ onCommand, scenario, staging = false }: Props) {
  const mount = useRef<HTMLDivElement | null>(null)
  const onCommandRef = useRef(onCommand)
  onCommandRef.current = onCommand

  useEffect(() => {
    const root = mount.current
    if (!root) return
    const terminal = new Terminal({
      cursorBlink: true,
      fontFamily: 'ui-monospace, SFMono-Regular, Consolas, monospace',
      fontSize: 13,
      lineHeight: 1.4,
      theme: {
        background: '#0b131c', foreground: '#d8e7f1',
        cursor: '#6ceab3', selectionBackground: '#2c5368',
      },
      allowTransparency: false,
      scrollback: 2000,
    })
    const fit = new FitAddon()
    terminal.loadAddon(fit)
    terminal.open(root)
    const resize = () => { try { fit.fit() } catch { /* unmounted */ } }
    resize()
    window.addEventListener('resize', resize)
    const observer = new ResizeObserver(resize)
    observer.observe(root)

    let input = ''
    let busy = false
    let disposed = false
    const prompt = () => terminal.write('\r\n\x1b[32mcka@lab\x1b[0m:\x1b[34m~\x1b[0m$ ')
    terminal.writeln('\x1b[1mCKA // LAB\x1b[0m  |  ' + (staging ? 'STAGING' : 'LOCAL PREVIEW'))
    terminal.writeln('Scenario: ' + scenario)
    terminal.writeln('Allowed: kubectl get / describe / scale / patch / rollout / explain / set resources')
    terminal.writeln(staging ? 'Prueba: kubectl get pods' : 'Try: kubectl get pods -A')
    prompt()

    const handle = async (line: string) => {
      busy = true
      try {
        const output = await onCommandRef.current(line)
        if (!disposed) terminal.writeln('\r\n' + output.replace(/\n/g, '\r\n'))
      } catch (error) {
        if (!disposed) terminal.writeln('\r\n\x1b[31m' +
          (error instanceof Error ? error.message : 'Command failed') + '\x1b[0m')
      } finally {
        if (!disposed) { busy = false; prompt() }
      }
    }

    const subscription = terminal.onData((chunk) => {
      if (busy) return
      for (const key of chunk) {
        if (key === '\r') {
          const line = input.trim()
          input = ''
          if (line) { void handle(line) }
          else prompt()
          break
        }
        if (key === '\u007f') {
          if (input.length) { input = input.slice(0, -1); terminal.write('\b \b') }
          continue
        }
        if (key >= ' ' && key !== '\u007f' && key !== '\x1b') {
          if (input.length >= 600) continue
          input += key
          terminal.write(key)
        }
      }
    })
    return () => {
      disposed = true
      subscription.dispose()
      observer.disconnect()
      window.removeEventListener('resize', resize)
      terminal.dispose()
    }
  }, [scenario, staging])

  return <div ref={mount} className="terminal-screen" aria-label={staging ? 'Terminal Kubernetes' : 'Terminal Kubernetes local'} />
}
