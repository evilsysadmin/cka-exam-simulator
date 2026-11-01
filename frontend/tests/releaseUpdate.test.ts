import assert from 'node:assert/strict'
import { newerRelease, checkRelease } from '../src/releaseUpdate.ts'
const current='a'.repeat(40), latest='b'.repeat(40)
const valid={application:'cka-exam-simulator',build:latest,mode:'restricted-lab-staging'}
assert.equal(newerRelease(valid,current),latest)
for(const value of [null,{}, {...valid,build:current},{...valid,build:'<script>'},{...valid,application:'other'},{...valid,mode:'arbitrary'}])assert.equal(newerRelease(value,current),null)
assert.equal(newerRelease(valid,'development'),null)
const signal=new AbortController().signal
let called=false
const fetcher=(async function(url,options){assert.equal(this,globalThis);called=true;assert.match(String(url),/^\/release.json\?ts=\d+$/);assert.equal(options?.credentials,'omit');assert.equal(options?.cache,'no-store');assert.equal(options?.redirect,'error');return new Response(JSON.stringify(valid))}) as typeof fetch
assert.equal(await checkRelease(fetcher,current,signal),latest);assert.ok(called)
assert.equal(await checkRelease((async()=>{throw Error('offline')}) as typeof fetch,current,signal),null)
assert.equal(await checkRelease((async()=>new Response('x'.repeat(5000))) as typeof fetch,current,signal),null)
console.log('Release update: valid identity, no auto reload, bounded anonymous fetch, offline resilience passed')
