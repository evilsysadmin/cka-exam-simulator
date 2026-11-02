import assert from 'node:assert/strict'
import { EditorTransport } from '../src/editorTransport.ts'
import type { EditorIO } from '../src/editorTransport.ts'

const original=globalThis.WebSocket
class Socket {
  static OPEN=1
  static sockets: Socket[]=[]
  readyState=0
  sent: string[]=[]
  onopen: (()=>void)|null=null
  onmessage: ((event:{data: string})=>void)|null=null
  onclose: (()=>void)|null=null
  onerror: (()=>void)|null=null
  url: string
  constructor(url: string) { this.url=url; Socket.sockets.push(this) }
  open(){this.readyState=1;this.onopen?.()}
  send(value:string){this.sent.push(value)}
  close(){this.readyState=3;this.onclose?.()}
  message(value:unknown){this.onmessage?.({data:JSON.stringify(value)})}
}
globalThis.WebSocket=Socket as unknown as typeof WebSocket
const tick=()=>new Promise(resolve=>setTimeout(resolve,0))
try {
  let input: ((text:string)=>void)|null=null, resize: (()=>void)|null=null, exchanges=0
  const output:string[]=[]
  const io:EditorIO={write:text=>output.push(text),size:()=>({columns:100,rows:30}),
    input:handler=>{input=handler;return()=>{input=null}},resize:handler=>{resize=handler;return()=>{resize=null}}}
  const fetcher=(async(url,options)=>{
    assert.equal(url,'https://id-staging.shadowops.dpdns.org/api/identity/token')
    assert.equal(options?.credentials,'include');assert.equal(options?.body,'{}')
    return new Response(JSON.stringify({token:'synthetic-token-'+ ++exchanges}))
  }) as typeof fetch
  const transport=new EditorTransport(fetcher,io,()=>true)
  const running=transport.run('a'.repeat(32),'b'.repeat(32))
  const socket=Socket.sockets.at(-1)!
  assert.equal(socket.url,'wss://cka-labs-staging.shadowops.dpdns.org/api/labs/editor')
  assert.ok(!socket.url.includes('?'))
  socket.open();await tick()
  const auth=JSON.parse(socket.sent[0]);assert.equal(auth.type,'auth');assert.equal(auth.token,'synthetic-token-1')
  assert.equal(auth.columns,100);assert.equal(auth.rows,30)
  assert.ok(input);assert.ok(resize)
  const text='á'.repeat(2000)
  ;(input as (text:string)=>void)(text)
  const parts=socket.sent.slice(1).map(value=>JSON.parse(value))
  assert.ok(parts.every(frame=>frame.type==='input' && new TextEncoder().encode(frame.data).length<=1024))
  assert.equal(parts.map(frame=>frame.data).join(''),text)
  ;(resize as ()=>void)();assert.equal(JSON.parse(socket.sent.at(-1)!).type,'resize')
  socket.message({type:'output',data:'\x1b[?1049hGNU nano'});assert.deepEqual(output,['\x1b[?1049hGNU nano'])
  socket.message({type:'exit',code:0});await running
  assert.equal(input,null);assert.equal(resize,null);assert.equal(socket.readyState,3)
  // A late central exchange must never send its token after dispose/logout.
  let release: ((response:Response)=>void)|undefined
  const waiting=new EditorTransport((()=>new Promise<Response>(resolve=>{release=resolve})) as typeof fetch,io,()=>false)
  await assert.rejects(waiting.run('a'.repeat(32),'b'.repeat(32)))
  let alive=true
  const late=new EditorTransport((()=>new Promise<Response>(resolve=>{release=resolve})) as typeof fetch,io,()=>alive)
  const blocked=late.run('a'.repeat(32),'b'.repeat(32));const connection=Socket.sockets.at(-1)!
  connection.open();await tick();alive=false;late.close()
  await assert.rejects(blocked)
  release?.(new Response(JSON.stringify({token:'must-not-be-sent'})));await tick()
  assert.equal(connection.sent.length,0)
  console.log('Editor transport: fixed credential-free URL, fresh token frame, raw PTY input/output, resize and logout cancellation passed')
} finally {globalThis.WebSocket=original}
