import { readFileSync, renameSync, writeFileSync, readdirSync } from 'node:fs'

const labs = process.argv[2] === 'lab-staging'
const staging = labs || process.argv[2] === 'account-staging'
if (process.argv[2] && !staging) throw Error('Unknown public build mode')
const dir = new URL(labs ? '../dist-lab-staging/' : staging ? '../dist-account-staging/' : '../dist-public/', import.meta.url)
const html = readFileSync(new URL('public-preview.html',dir),'utf8')
if(!html.includes('CKA // LAB') || !html.includes('/assets/')) throw Error('Invalid public preview')
renameSync(new URL('public-preview.html',dir),new URL('index.html',dir))
const sha=process.env.VITE_BUILD_SHA||'development'
if(sha!=='development' && !/^[a-f0-9]{40}$/.test(sha)) throw Error('Expected immutable source SHA')
writeFileSync(new URL('release.json',dir),JSON.stringify({application:'cka-exam-simulator',build:sha,mode:labs?'restricted-lab-staging':staging?'account-staging-preview':'static-readonly-preview'})+'\n')
const assets=readdirSync(new URL('assets/',dir)).filter(x=>x.endsWith('.js'))
if(!assets.length) throw Error('Missing JavaScript')
for (const file of assets){
  const js=readFileSync(new URL('assets/'+file,dir),'utf8')
  if(/\/api\/dev\/|\/api\/scenarios|\/api\/health/.test(js)) throw Error('Developer API leaked into public bundle')
}
console.log(labs ? 'RESTRICTED_LAB_STAGING_BUILD_OK' : 'PUBLIC_READONLY_PREVIEW_BUILD_OK')

if(staging){
  const headers=new URL('_headers',dir)
  writeFileSync(headers,readFileSync(headers,'utf8')+`\n/*\n  Content-Security-Policy: default-src 'self'; script-src 'self'; connect-src 'self' https://id-staging.shadowops.dpdns.org https://cka-api-staging.shadowops.dpdns.org${labs ? ' https://cka-labs-staging.shadowops.dpdns.org wss://cka-labs-staging.shadowops.dpdns.org' : ''}; img-src 'self' data:; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; font-src 'self' https://fonts.gstatic.com; object-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'\n\n/\n  Cache-Control: no-store\n`)
}

