import { useEffect, useRef } from 'react'
import { Terminal } from '@xterm/xterm'
import { FitAddon } from '@xterm/addon-fit'
import '@xterm/xterm/css/xterm.css'
import type { EditorIO } from './editorTransport'

type Props = { onCommand: (command: string) => Promise<string>; scenario: string; staging?: boolean; onEdit?: (command: string, io: EditorIO) => Promise<string> }

export default function TerminalPanel({ onCommand, onEdit, scenario, staging = false }: Props) {
  const mount = useRef<HTMLDivElement | null>(null)
  const onCommandRef = useRef(onCommand)
  onCommandRef.current = onCommand
  const onEditRef = useRef(onEdit)
  onEditRef.current = onEdit

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

    let editorInput: ((value: string) => void) | null = null
    let editorResize: (() => void) | null = null
    const editorSize = terminal.onResize(() => editorResize?.())
    terminal.attachCustomKeyEventHandler(event => { if (editorInput && (event.ctrlKey || event.key==='Tab')) event.preventDefault(); return true })
    const editorIO: EditorIO = {
      write: value => terminal.write(value),
      size: () => ({columns:terminal.cols,rows:terminal.rows}),
      input: handler => { editorInput=handler; root.dataset.editorActive='true'; return () => { editorInput=null; delete root.dataset.editorActive } },
      resize: handler => { editorResize=handler; return () => { editorResize=null } },
    }
    let input = ''
    let busy = false
    let disposed = false
    let escape = ''
    const history: string[] = []
    let historyIndex = -1
    let draft = ''
    const promptText = '\x1b[32mcka@lab\x1b[0m:\x1b[34m~\x1b[0m$ '
    const prompt = () => terminal.write('\r\n' + promptText)
    // Repaint only the pending command, including its wrapped physical rows.
    // Do not clear the xterm buffer: previous output is valuable during an exam.
    const replaceInput = (next: string) => {
      const rows = Math.floor((11 + input.length - 1) / Math.max(1, terminal.cols))
      let erase = '\r\x1b[2K'
      for (let row = 0; row < rows; row++) erase += '\x1b[1A\r\x1b[2K'
      terminal.write(erase + '\x1b[0J' + promptText + next)
      input = next
    }
    const navigate = (direction: -1 | 1) => {
      if (!history.length) return
      if (direction === -1) {
        if (historyIndex === -1) { draft = input; historyIndex = history.length }
        historyIndex = Math.max(0, historyIndex - 1)
        replaceInput(history[historyIndex])
      } else if (historyIndex !== -1) {
        historyIndex++
        if (historyIndex === history.length) {
          historyIndex = -1
          replaceInput(draft)
          draft = ''
        } else replaceInput(history[historyIndex])
      }
    }
    terminal.writeln('\x1b[1mCKA // LAB\x1b[0m  |  ' + (staging ? 'STAGING' : 'LOCAL PREVIEW'))
    terminal.writeln('Scenario: ' + scenario)
    terminal.writeln(staging ? 'kubectl o k · kubectl edit con nano · filtra la salida con | grep' : 'Allowed: kubectl get / describe / scale / patch / rollout / explain / set resources')
    terminal.writeln(staging ? 'Prueba: kubectl get pods' : 'Try: kubectl get pods -A')
    prompt()

    const handle = async (line: string) => {
      busy = true
      try {
        const output = onEditRef.current && /^(?:k(?:ubectl)?\s+edit(?:\s|$)|nano(?:\s|$))/.test(line)
          ? await onEditRef.current(line,editorIO) : await onCommandRef.current(line)
        if (!disposed) terminal.writeln('\r\n' + output.replace(/\n/g, '\r\n'))
      } catch (error) {
        if (!disposed) terminal.writeln('\r\n\x1b[31m' +
          (error instanceof Error ? error.message : 'Command failed') + '\x1b[0m')
      } finally {
        if (!disposed) { busy = false; prompt() }
      }
    }

    const subscription = terminal.onData((chunk) => {
      if (editorInput) { editorInput(chunk); return }
      if (busy) return
      for (const key of chunk) {
        // xterm sends cursor keys as escape sequences, sometimes split across
        // chunks. Unknown sequences must never leak "[A" into kubectl input.
        if (escape) {
          escape += key
          if (escape === '\x1b[' || escape === '\x1bO' ||
              (/^\x1b\[[0-9;]*$/.test(escape) && escape.length < 16)) continue
          if (escape === '\x1b[A' || escape === '\x1bOA') navigate(-1)
          if (escape === '\x1b[B' || escape === '\x1bOB') navigate(1)
          escape = ''
          continue
        }
        if (key === '\x1b') { escape = key; continue }
        if (key === '\u0003') {
          // Ctrl+C cancels unsubmitted text, never an in-flight lab command.
          terminal.write('^C')
          input = ''; historyIndex = -1; draft = ''
          prompt()
          continue
        }
        if (key === '\r') {
          const line = input.trim()
          input = ''; historyIndex = -1; draft = ''
          if (line) {
            if (history.at(-1) !== line) history.push(line)
            if (history.length > 40) history.shift()
            void handle(line)
          } else prompt()
          break
        }
        if (key === '\u007f') {
          if (input.length) {
            if (historyIndex !== -1) { historyIndex = -1; draft = '' }
            replaceInput(input.slice(0, -1))
          }
          continue
        }
        if (key >= ' ' && key !== '\u007f') {
          if (input.length >= 600) continue
          if (historyIndex !== -1) { historyIndex = -1; draft = '' }
          input += key
          terminal.write(key)
        }
      }
    })
    return () => {
      disposed = true
      subscription.dispose()
      editorSize.dispose()
      observer.disconnect()
      window.removeEventListener('resize', resize)
      terminal.dispose()
    }
  }, [scenario, staging])

  return <div ref={mount} className="terminal-screen" aria-label={staging ? 'Terminal Kubernetes' : 'Terminal Kubernetes local'} />
}
