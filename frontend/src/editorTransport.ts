import { IDENTITY } from './identitySession.ts'
import { LabRequestError } from './labSession.ts'

export type EditorIO = {
  write: (value: string) => void
  size: () => { columns: number; rows: number }
  input: (handler: (value: string) => void) => () => void
  resize: (handler: () => void) => () => void
}

// The URL is fixed and carries no credential. Tokens travel only in TLS frames.
export class EditorTransport {
  private socket: WebSocket | null = null
  private abort = new AbortController()
  private stop: ((error?: Error) => void) | null = null
  private fetcher: typeof fetch
  private io: EditorIO
  private alive: () => boolean
  constructor(fetcher: typeof fetch, io: EditorIO, alive: () => boolean) { this.fetcher=fetcher; this.io=io; this.alive=alive }
  close() {
    this.abort.abort()
    this.stop?.(new LabRequestError(401))
    this.socket?.close()
  }
  async run(id: string, editor: string): Promise<void> {
    if (!this.alive() || !/^[a-f0-9]{32}$/.test(id) || !/^[a-f0-9]{32}$/.test(editor)) throw new LabRequestError(401)
    const socket = new WebSocket('wss://cka-labs-staging.shadowops.dpdns.org/api/labs/editor')
    this.socket = socket
    let finished = false, authenticating = false
    let removeInput = () => {}, removeResize = () => {}
    let heartbeat: ReturnType<typeof setInterval> | undefined
    const size = () => {
      const value = this.io.size()
      return { columns: Math.max(20,Math.min(300,value.columns)), rows: Math.max(6,Math.min(100,value.rows)) }
    }
    const initialization = setTimeout(() => this.stop?.(new LabRequestError(503)),15000)
    try {
      await new Promise<void>((resolve,reject) => {
        this.stop = (error?: Error) => { if (finished) return; finished=true; if (error) reject(error); else resolve() }
        const authenticate = async (first = false) => {
          if (authenticating || finished) return
          authenticating = true
          const timer = setTimeout(() => this.abort.abort(),7000)
          try {
            const response = await this.fetcher(IDENTITY+'/api/identity/token', {
              method:'POST', credentials:'include', headers:{'Content-Type':'application/json'}, body:'{}', signal:this.abort.signal,
            })
            if (!response.ok) throw new LabRequestError(response.status)
            const data = await response.json() as {token?: unknown}
            if (finished || !this.alive() || this.abort.signal.aborted || socket.readyState !== WebSocket.OPEN) throw new LabRequestError(401)
            if (typeof data.token !== 'string' || !data.token || data.token.length>4096) throw new LabRequestError(503)
            socket.send(JSON.stringify(first ? {type:'auth',id,editor,token:data.token,...size()} : {type:'auth',token:data.token}))
            if (first) {
              clearTimeout(initialization)
              removeInput = this.io.input(value => {
                if (finished || socket.readyState !== WebSocket.OPEN) return
                if (value.length>65536) { this.stop?.(new LabRequestError(400,true)); return }
                let part='', bytes=0
                for (const character of value) {
                  const count = new TextEncoder().encode(character).length
                  if (bytes+count>1024) { socket.send(JSON.stringify({type:'input',data:part})); part=''; bytes=0 }
                  part+=character; bytes+=count
                }
                if (part) socket.send(JSON.stringify({type:'input',data:part}))
              })
              removeResize = this.io.resize(() => {
                if (!finished && socket.readyState === WebSocket.OPEN) socket.send(JSON.stringify({type:'resize',...size()}))
              })
              heartbeat=setInterval(() => void authenticate(),5000)
            }
          } catch (error) { this.stop?.(error instanceof Error ? error : new LabRequestError(503)) }
          finally { clearTimeout(timer); authenticating=false }
        }
        socket.onopen=()=>{ void authenticate(true) }
        socket.onmessage=event=>{
          try {
            if (typeof event.data !== 'string' || event.data.length>131072) throw Error()
            const data=JSON.parse(event.data)
            if (data.type==='output' && typeof data.data==='string' && data.data.length<=65536) this.io.write(data.data)
            else if (data.type==='exit' && data.code===0) this.stop?.()
            else throw Error()
          } catch { this.stop?.(new LabRequestError(503)) }
        }
        socket.onerror=()=>this.stop?.(new LabRequestError(503))
        socket.onclose=()=>{ if (!finished) this.stop?.(new LabRequestError(this.alive()?503:401)) }
      })
    } finally {
      finished=true; clearTimeout(initialization); clearInterval(heartbeat)
      this.abort.abort(); removeInput(); removeResize(); socket.close(); this.socket=null; this.stop=null
    }
  }
}
