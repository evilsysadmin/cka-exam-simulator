import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync, readdirSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
const dir=fileURLToPath(new URL('../dist-public/',import.meta.url))
test('public build never bundles privileged lab API',()=>{
  const html=readFileSync(dir+'index.html','utf8')
  const release=JSON.parse(readFileSync(dir+'release.json','utf8'))
  assert.match(html,/CKA \/\/ LAB/)
  assert.equal(release.mode,'static-readonly-preview')
  const js=readdirSync(dir+'assets/').filter(x=>x.endsWith('.js'))
    .map(x=>readFileSync(dir+'assets/'+x,'utf8')).join('\n')
  assert.match(js,/Demostraci.n p.blica|Terminal desactivada/)
  assert.doesNotMatch(js,/\/api\/dev\/|\/api\/scenarios|\/api\/health/)
})
