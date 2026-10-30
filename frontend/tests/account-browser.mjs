// Real built UI and navigation; account responses are explicitly synthetic.
// This does not certify deployed Identity/CKA SSO or activate laboratories.
import { chromium } from 'playwright'
import { readFile, stat, mkdir } from 'node:fs/promises'
import path from 'node:path'
import assert from 'node:assert/strict'
const root=process.cwd(), IDP='https://id-staging.shadowops.dpdns.org', API='https://cka-api-staging.shadowops.dpdns.org', GAME='https://staging.cka.shadowops.dpdns.org'
const subject='11111111-1111-4111-8111-111111111111'
const browser=await chromium.launch({headless:true,...(process.env.CKA_CHROMIUM_EXECUTABLE?{executablePath:process.env.CKA_CHROMIUM_EXECUTABLE}:{})})
const output=path.join(root,'account-captures');await mkdir(output,{recursive:true})
const profile={subject,issuer:IDP,audience:'cka-simulator',labs_enabled:false}
async function staticFiles(route, directory){
  const base=path.join(root,directory), name=decodeURIComponent(new URL(route.request().url()).pathname)
  const file=path.resolve(base,name==='/'?'index.html':'.'+name)
  assert.ok(file.startsWith(base+'/'))
  try{assert.ok((await stat(file)).isFile())}catch{return route.fulfill({status:404,body:'not found'})}
  const types={'.html':'text/html','.js':'text/javascript','.css':'text/css','.json':'application/json'}
  return route.fulfill({status:200,contentType:types[path.extname(file)]||'application/octet-stream',body:await readFile(file)})
}
let lastPage
try{
  for(const [name,width,height,mobile] of [['desktop',1440,1000,false],['mobile',390,844,true]]){
    const context=await browser.newContext({viewport:{width,height},isMobile:mobile,hasTouch:mobile})
    let cookie=true, live=true, logoutFails=false, tokenCalls=0, accountCalls=0
    await context.route('**/*', async route=>{
      const req=route.request(), url=new URL(req.url())
      if(url.origin===GAME)return staticFiles(route,'dist-account-staging')
      if(url.origin==='https://cka.shadowops.dpdns.org')return staticFiles(route,'dist-public')
      if(url.origin===IDP){
        const headers={'Access-Control-Allow-Origin':GAME,'Access-Control-Allow-Credentials':'true','Access-Control-Allow-Headers':'Content-Type','Access-Control-Allow-Methods':'POST'}
        if(req.method()==='OPTIONS')return route.fulfill({status:204,headers})
        if(url.pathname==='/api/identity/logout'){
          if(logoutFails)return route.fulfill({status:503,json:{},headers})
          cookie=false;return route.fulfill({status:204,headers})
        }
        assert.equal(url.pathname,'/api/identity/token'); tokenCalls++
        assert.equal(req.postData(),'{}')
        return route.fulfill({status:cookie?200:401,json:cookie?{token:'synthetic-browser-transport'}:{},headers})
      }
      if(url.origin===API){
        const headers={'Access-Control-Allow-Origin':GAME,'Access-Control-Allow-Headers':'Authorization','Access-Control-Allow-Methods':'GET'}
        if(req.method()==='OPTIONS')return route.fulfill({status:204,headers})
        assert.equal(url.pathname,'/api/account/me');accountCalls++
        assert.equal(req.headers()['authorization'],'Bearer synthetic-browser-transport')
        return route.fulfill({status:live?200:401,json:live?profile:{},headers})
      }
      return route.abort()
    })
    const page=await context.newPage();lastPage=page;page.setDefaultTimeout(15000)
    await page.goto('https://cka.shadowops.dpdns.org/')
    assert.equal(await page.getByRole('region',{name:'Cuenta ShadowOps'}).count(),0)
    assert.equal(tokenCalls,0);assert.equal(accountCalls,0)
    await page.goto(GAME+'/?shadowops=central')
    await page.getByText('Cuenta ShadowOps conectada · laboratorios aún no disponibles',{exact:true}).waitFor()
    assert.equal(await page.locator('input[type=password]').count(),0)
    assert.ok(await page.getByRole('button',{name:'Prepare lab',exact:true}).isDisabled())
    assert.ok(await page.getByRole('button',{name:'Check solution',exact:true}).isDisabled())
    assert.equal(await page.evaluate(()=>localStorage.length+sessionStorage.length),0)
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false)
    await page.screenshot({path:path.join(output,name+'-authenticated.png'),fullPage:true})
    logoutFails=true;await page.getByRole('button',{name:'Cerrar sesión',exact:true}).click()
    await page.getByText(/No se pudo confirmar el cierre/).waitFor()
    assert.equal(await page.getByRole('link',{name:'Continuar con ShadowOps'}).count(),0)
    await page.screenshot({path:path.join(output,name+'-logout-failed.png'),fullPage:true})
    logoutFails=false;await page.getByRole('button',{name:'Cerrar sesión',exact:true}).click()
    await page.getByRole('link',{name:'Continuar con ShadowOps',exact:true}).waitFor()
    assert.equal(await page.getByRole('link',{name:'Continuar con ShadowOps'}).getAttribute('href'),IDP+'/login?app=cka-simulator')
    await page.reload();await page.getByText('No has iniciado sesión.',{exact:true}).waitFor()
    await page.screenshot({path:path.join(output,name+'-anonymous.png'),fullPage:true})
    cookie=true;live=false;await page.reload()
    await page.getByText(/No se pudo comprobar tu cuenta/).waitFor()
    await page.screenshot({path:path.join(output,name+'-revoked.png'),fullPage:true})
    await context.close();console.log(name+': account UI, static isolation, silent restore, confirmed logout, failure and revoked state passed')
  }
}catch(error){if(lastPage){await lastPage.screenshot({path:path.join(output,'failure.png'),fullPage:true});console.error('ACCOUNT_DOM '+await lastPage.locator('.account-access').innerText().catch(()=>'<absent>'))}throw error}finally{await browser.close()}
