import { readFileSync, renameSync, writeFileSync, readdirSync } from 'node:fs'

const dir = new URL('../dist-public/', import.meta.url)
const html = readFileSync(new URL('public-preview.html',dir),'utf8')
if(!html.includes('CKA // LAB') || !html.includes('/assets/')) throw Error('Invalid public preview')
renameSync(new URL('public-preview.html',dir),new URL('index.html',dir))
const sha=process.env.VITE_BUILD_SHA||'development'
if(sha!=='development' && !/^[a-f0-9]{40}$/.test(sha)) throw Error('Expected immutable source SHA')
writeFileSync(new URL('release.json',dir),JSON.stringify({application:'cka-exam-simulator',build:sha,mode:'static-readonly-preview'})+'\n')
const assets=readdirSync(new URL('assets/',dir)).filter(x=>x.endsWith('.js'))
if(!assets.length) throw Error('Missing JavaScript')
for (const file of assets){
  const js=readFileSync(new URL('assets/'+file,dir),'utf8')
  if(/\/api\/dev\/|\/api\/scenarios|\/api\/health/.test(js)) throw Error('Developer API leaked into public bundle')
}
console.log('PUBLIC_READONLY_PREVIEW_BUILD_OK')
