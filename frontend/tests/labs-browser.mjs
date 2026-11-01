// Actual built UI and terminal input. HTTP/Identity responses are synthetic;
// Kubernetes repairs and revocation are independently verified by backend CI.
import { chromium } from 'playwright'
import { readFile, stat, mkdir } from 'node:fs/promises'
import path from 'node:path'
import assert from 'node:assert/strict'
const root=process.cwd(), GAME='https://staging.cka.shadowops.dpdns.org', IDP='https://id-staging.shadowops.dpdns.org'
const ACCOUNT='https://cka-api-staging.shadowops.dpdns.org', LABS='https://cka-labs-staging.shadowops.dpdns.org'
const subject='11111111-1111-4111-8111-111111111111'
const scenarios=[
  {slug:'scale-payment-api',title:'Caída de capacidad en pagos',domain:'workloads',difficulty:'easy',minutes:6,namespace:'cka-payments',story:'Recupera la capacidad de la API de pagos.',statement:'Ajusta payment-api a tres réplicas disponibles.'},
  {slug:'fix-catalog-service',title:'Servicio de catálogo sin backends',domain:'networking',difficulty:'medium',minutes:9,namespace:'cka-catalog',story:'Repara el enrutamiento del catálogo.',statement:'Repara catalog-api-svc para seleccionar sus pods.'},
  {slug:'recover-scheduler-capacity',title:'Pods bloqueados por CPU excesiva',domain:'workloads',difficulty:'medium',minutes:12,namespace:'cka-scheduling',story:'Recupera los pods que no pueden programarse.',statement:'Ajusta las peticiones y límites de CPU.'},
]
const commands=[
  'kubectl scale deployment/payment-api --replicas=3',
  `kubectl patch service/catalog-api-svc --type=merge -p '{"spec":{"selector":{"app":"catalog"}}}'`,
  'kubectl set resources deployment/scheduler-api --requests=cpu=10m --limits=cpu=250m',
]
const builtRelease=JSON.parse(await readFile(path.join(root,'dist-lab-staging/release.json'),'utf8'))
const output=path.join(root,'lab-captures');await mkdir(output,{recursive:true})
const browser=await chromium.launch({headless:true,...(process.env.CKA_CHROMIUM_EXECUTABLE?{executablePath:process.env.CKA_CHROMIUM_EXECUTABLE}:{})})
let last
try{
  for(const [name,width,height,mobile] of [['desktop',1440,1000,false],['mobile',390,844,true]]){
    const context=await browser.newContext({viewport:{width,height},isMobile:mobile,hasTouch:mobile})
    let publishedBuild=builtRelease.build
    let editorInputs='', editorResizes=0, editorOpen=0, editorSaves=0
    let logged=true,current=null, repaired=false,sequence=0,labCalls=0,commandCalls=0,readCalls=0,capabilityFailure=null,otherUserBusy=true
    await context.routeWebSocket('wss://cka-labs-staging.shadowops.dpdns.org/api/labs/editor',socket=>{
      socket.onMessage(raw=>{
        const frame=JSON.parse(String(raw))
        if(frame.type==='auth'){
          assert.ok(frame.token.startsWith('synthetic-fresh-'))
          if(frame.id){assert.equal(frame.id,current.session.id);assert.equal(frame.editor,'e'.repeat(32));socket.send(JSON.stringify({type:'output',data:'\x1b[?1049h\x1b[H GNU nano · resource.yaml\r\n'}))}
        }else if(frame.type==='input'){
          editorInputs+=frame.data
          if(frame.data.includes('\x18')){socket.send(JSON.stringify({type:'output',data:'\x1b[?1049l'}));socket.send(JSON.stringify({type:'exit',code:0}))}
        }else if(frame.type==='resize'){editorResizes++;assert.ok(frame.columns>=20);assert.ok(frame.rows>=6)}
        else throw Error('Unexpected editor frame')
      })
    })
    await context.route('**/*',async route=>{
      const req=route.request(),url=new URL(req.url())
      if(url.origin===GAME){
        if(url.pathname==='/release.json')return route.fulfill({status:200,json:{...builtRelease,build:publishedBuild}})
        const base=path.join(root,'dist-lab-staging'),name=decodeURIComponent(url.pathname)
        const file=path.resolve(base,name==='/'?'index.html':'.'+name)
        assert.ok(file.startsWith(base+'/'))
        try{assert.ok((await stat(file)).isFile())}catch{return route.fulfill({status:404,body:'missing'})}
        return route.fulfill({contentType:({'.html':'text/html','.js':'text/javascript','.css':'text/css','.json':'application/json'})[path.extname(file)]||'application/octet-stream',body:await readFile(file)})
      }
      const headers={'Access-Control-Allow-Origin':GAME,'Access-Control-Allow-Headers':url.origin===IDP?'Content-Type':'Authorization, Content-Type','Access-Control-Allow-Methods':url.origin===ACCOUNT?'GET':'POST',...(url.origin===IDP?{'Access-Control-Allow-Credentials':'true'}:{})}
      if(req.method()==='OPTIONS')return route.fulfill({status:204,headers})
      if(url.origin===IDP){
        if(url.pathname==='/api/identity/logout'){logged=false;return route.fulfill({status:204,headers})}
        assert.equal(url.pathname,'/api/identity/token');assert.equal(req.postData(),'{}')
        return route.fulfill({status:logged?200:401,json:logged?{token:'synthetic-fresh-'+ ++sequence}:{},headers})
      }
      if(url.origin===ACCOUNT)return route.fulfill({status:logged?200:401,json:{subject,issuer:IDP,audience:'cka-simulator',labs_enabled:false},headers})
      if(url.origin===LABS){
        labCalls++;assert.ok(req.headers().authorization.startsWith('Bearer synthetic-fresh-'))
        assert.equal(req.headers().cookie,undefined);assert.equal(url.search,'')
        const data=req.postDataJSON();assert.equal(data.owner,undefined)
        const action=url.pathname.split('/').at(-1)
        if(!logged)return route.fulfill({status:401,json:{},headers})
        let result
        if(action==='capabilities' && capabilityFailure){
          const status=capabilityFailure; capabilityFailure=null
          return route.fulfill({status,json:{},headers})
        }
        if(action==='capabilities')result={labs_enabled:true,capacity:1,available:!current && !otherUserBusy,scenarios,current}
        else if(action==='prepare'){
          assert.equal(current,null)
          const scenario=scenarios.find(s=>s.slug===data.slug),index=scenarios.indexOf(scenario)
          const suffix=String(index+1).padStart(32,'0')
          current={session:{id:suffix,scenario_slug:scenario.slug,phase:'active',remaining_seconds:300,expires_at:'2099-01-01T00:00:00Z'},scenario:{...scenario,namespace:'cka-lab-'+suffix}}
          repaired=false;result=current
        }else{
          assert.equal(data.id,current.session.id)
          if(action==='edit-open'){assert.equal(data.command,'k edit svc/catalog-api-svc');editorOpen++;result={editor:'e'.repeat(32)}}
          else if(action==='edit-save'){assert.equal(data.editor,'e'.repeat(32));editorSaves++;repaired=true;result={output:'Cambios guardados en Kubernetes.'}}
          else if(action==='edit-cancel'){result={status:'closed'}}
          else if(action==='command'){
            if(data.command==='kubectl get pods'){readCalls++;result={output:'pod/ready'}}
            else {assert.equal(data.command,commands[scenarios.findIndex(s=>s.slug===current.scenario.slug)]);commandCalls++;repaired=true;result={output:'workload actualizado'}}
          }
          else if(action==='grade')result={passed:repaired,score:repaired?100:0,checks:[{name:'Estado real del ejercicio',passed:repaired}]}
          else if(action==='finish'){current=null;result={status:'closed'}}
          else throw Error('unexpected action '+action)
        }
        return route.fulfill({status:200,json:result,headers})
      }
      return route.abort()
    })
    const page=await context.newPage();last=page;page.setDefaultTimeout(15000)
    await page.goto(GAME)
    await page.getByRole('button',{name:'Preparar laboratorio',exact:true}).waitFor()
    await page.getByText('El laboratorio está ocupado',{exact:true}).waitFor()
    assert.equal(await page.getByRole('button',{name:'Preparar laboratorio',exact:true}).isDisabled(),true)
    assert.equal(await page.locator('.terminal-screen').count(),0)
    await page.waitForFunction(()=>!document.querySelector('.action-left')?.textContent?.includes('Comprobando'))
    otherUserBusy=false
    await page.evaluate(()=>document.dispatchEvent(new Event('visibilitychange')))
    await page.waitForFunction(()=>[...document.querySelectorAll('button')].some(b=>b.textContent==='Preparar laboratorio' && !b.disabled))
    for(let index=0;index<scenarios.length;index++){
      await page.getByRole('button',{name:new RegExp(scenarios[index].title)}).click()
      await page.getByRole('button',{name:'Preparar laboratorio',exact:true}).click()
      await page.getByRole('button',{name:'Finalizar intento',exact:true}).waitFor()
      if(index===0){
        const terminal=await page.locator('.terminal-screen').elementHandle()
        assert.ok(terminal)
        const finishButton=page.getByRole('button',{name:'Finalizar intento',exact:true})
        assert.equal(await finishButton.evaluate(button=>button.tabIndex),-1)
        await finishButton.evaluate(button=>button.focus())
        await page.keyboard.press('Enter')
        assert.equal(await page.getByRole('dialog').count(),0,'Enter on Finish must not even open confirmation')
        assert.ok(current,'Enter must never finish the attempt')
        await finishButton.click()
        await page.getByRole('dialog').waitFor()
        assert.equal(await page.getByRole('button',{name:'Cancelar',exact:true}).evaluate(button=>button===document.activeElement),true)
        await page.keyboard.press('Enter')
        await page.getByRole('dialog').waitFor({state:'detached'})
        assert.ok(current,'Enter on default Cancel must keep the lease')
        await finishButton.click()
        const destructive=page.getByRole('button',{name:'Sí, finalizar intento',exact:true})
        assert.equal(await destructive.evaluate(button=>button.tabIndex),-1)
        await destructive.evaluate(button=>button.focus())
        await page.keyboard.press('Enter')
        assert.ok(current,'Enter on the destructive confirmation must not finish')
        await page.getByRole('dialog').waitFor()
        await page.keyboard.press('Escape')
        await page.getByRole('dialog').waitFor({state:'detached'})
        assert.ok(current,'Escape must keep the lease')
        // Maximize/restore must preserve the actual xterm and its scrollback.
        await page.getByRole('button',{name:'Maximizar terminal',exact:true}).click()
        assert.equal(await page.evaluate(node=>document.querySelector('.terminal-screen')===node,terminal),true)
        const height=await page.locator('.console-area').evaluate(element=>element.getBoundingClientRect().height)
        assert.ok(height>500,'Maximized terminal should use the viewport')
        assert.equal(await page.locator('.terminal-maximized').evaluate(element=>{const r=element.getBoundingClientRect();return r.top<=12 && r.bottom>=innerHeight-12}),true)
        const objective=page.getByRole('button',{name:'Objetivo',exact:true})
        if(mobile)await objective.click();else await objective.hover()
        await page.getByRole('complementary',{name:'Objetivo del laboratorio'}).waitFor()
        await page.screenshot({path:path.join(output,name+'-terminal-maximized.png'),fullPage:false})
        await page.getByRole('button',{name:'Restaurar terminal',exact:true}).click()
        assert.equal(await page.evaluate(node=>document.querySelector('.terminal-screen')===node,terminal),true)
        if(/^[a-f0-9]{40}$/.test(builtRelease.build)){
          publishedBuild='not-a-release'
          const invalidCheck=page.waitForResponse(response=>response.url().startsWith(GAME+'/release.json?'))
          const invalidCapabilities=page.waitForResponse(response=>response.url()===LABS+'/api/labs/capabilities')
          await page.evaluate(()=>document.dispatchEvent(new Event('visibilitychange')))
          await Promise.all([(await invalidCheck).finished(),(await invalidCapabilities).finished()])
          await page.evaluate(()=>new Promise(requestAnimationFrame))
          assert.equal(await page.getByText('Nueva versión disponible',{exact:true}).count(),0)
          publishedBuild=builtRelease.build==='d'.repeat(40)?'e'.repeat(40):'d'.repeat(40)
          const newCheck=page.waitForResponse(response=>response.url().startsWith(GAME+'/release.json?'))
          const newCapabilities=page.waitForResponse(response=>response.url()===LABS+'/api/labs/capabilities')
          await page.evaluate(()=>document.dispatchEvent(new Event('visibilitychange')))
          await Promise.all([(await newCheck).finished(),(await newCapabilities).finished()])
          await page.evaluate(()=>new Promise(requestAnimationFrame))
          await page.getByText('Nueva versión disponible',{exact:true}).waitFor()
          assert.equal(await page.evaluate(node=>document.querySelector('.terminal-screen')===node,terminal),true,'Update check must not reload a live lab')
        }
        // A transient failed background check must preserve the actual xterm
        // node (and thus scrollback/input), even while a real lease is active.
        for(const status of [503,409,429]){
          capabilityFailure=status
          await page.evaluate(()=>document.dispatchEvent(new Event('visibilitychange')))
          await page.getByRole('alert').waitFor()
          await page.waitForFunction(()=>!document.querySelector('.action-left')?.textContent?.includes('Comprobando'))
          assert.equal(await page.evaluate(node=>document.querySelector('.terminal-screen')===node,terminal),true)
          await page.evaluate(()=>document.dispatchEvent(new Event('visibilitychange')))
          await page.getByRole('alert').waitFor({state:'hidden'})
        }
        // Authentication denial, unlike transient outage, must remove the
        // active terminal immediately and allow a fresh server-side recovery.
        capabilityFailure=401
        await page.evaluate(()=>document.dispatchEvent(new Event('visibilitychange')))
        await page.locator('.terminal-screen').waitFor({state:'detached'})
        await page.getByRole('region',{name:'Laboratorios CKA'}).getByText('Sesión o acceso al laboratorio no disponible.').waitFor()
        await page.evaluate(()=>document.dispatchEvent(new Event('visibilitychange')))
        await page.getByRole('button',{name:'Finalizar intento',exact:true}).waitFor()
        await page.reload()
        await page.getByRole('button',{name:'Finalizar intento',exact:true}).waitFor()
      }
      await page.getByRole('button',{name:'Comprobar resultado',exact:true}).click()
      await page.getByText(/Todavía no está resuelto/).waitFor()
      if(index===1){
        const input=page.locator('.xterm-helper-textarea');await input.focus()
        await page.keyboard.type('k edit svc/catalog-api-svc');await page.keyboard.press('Enter')
        await page.locator('.terminal-screen[data-editor-active="true"]').waitFor()
        assert.equal(await page.getByRole('button',{name:'Recargar',exact:true}).isDisabled(),true)
        assert.equal(await page.getByRole('button',{name:'Finalizar intento',exact:true}).isDisabled(),true)
        await page.getByRole('button',{name:'Maximizar terminal',exact:true}).click()
        await input.focus();await page.keyboard.press('Tab');await page.keyboard.press('Control+o');await page.keyboard.press('Enter');await page.keyboard.press('Control+x')
        await page.locator('.terminal-screen[data-editor-active="true"]').waitFor({state:'hidden'})
        await page.getByRole('status').filter({hasText:'LABORATORIO ACTIVO'}).waitFor()
        assert.ok(editorInputs.includes('\t'));assert.ok(editorInputs.includes('\x0f'));assert.ok(editorInputs.includes('\x18'))
        assert.equal(editorOpen,1);assert.equal(editorSaves,1);assert.ok(editorResizes>0)
        await page.getByRole('button',{name:'Restaurar terminal',exact:true}).click()
        assert.equal(await page.getByRole('button',{name:'Recargar',exact:true}).isDisabled(),false)
      }
      const input=page.locator('.xterm-helper-textarea');await input.focus()
      await page.keyboard.type(commands[index]);await page.keyboard.press('Enter')
      await page.getByRole('status').filter({hasText:'LABORATORIO ACTIVO'}).waitFor()
      if(index===0){
        // Real xterm keyboard handling: history must recall the submitted
        // kubectl command without writing literal arrow escape fragments.
        const repeated=page.waitForRequest(request=>request.url()===LABS+'/api/labs/command' &&
          request.postDataJSON()?.command===commands[0])
        await page.keyboard.press('ArrowUp')
        await page.keyboard.press('Enter')
        await repeated
        await page.getByRole('status').filter({hasText:'LABORATORIO ACTIVO'}).waitFor()
        assert.equal(commandCalls,2,'Up + Enter should repeat the actual command once')
        // Walking up and then down must restore a partially typed draft.
        const draftRequest=page.waitForRequest(request=>request.url()===LABS+'/api/labs/command' &&
          request.postDataJSON()?.command==='kubectl get pods')
        await page.keyboard.type('kubectl get pods')
        await page.keyboard.press('ArrowUp')
        await page.keyboard.press('ArrowDown')
        await page.keyboard.press('Enter')
        await draftRequest
        await page.getByRole('status').filter({hasText:'LABORATORIO ACTIVO'}).waitFor()
        assert.equal(readCalls,1,'Down should restore the unfinished draft')
      }
      await page.getByRole('button',{name:'Comprobar resultado',exact:true}).click()
      await page.getByText(/Ejercicio resuelto/).waitFor()
      const layout=await page.locator('.lab').evaluate(lab=>{
        const bounds=lab.getBoundingClientRect()
        const elements=[...lab.querySelectorAll('.clock, .clock strong, .action-buttons button')]
        return {count:elements.length,clipped:elements.some(element=>{
          const rect=element.getBoundingClientRect()
          return rect.left<bounds.left-1 || rect.right>bounds.right+1 || rect.width<=0
        })}
      })
      assert.equal(layout.count,4)
      assert.equal(layout.clipped,false,'Timer or action buttons were clipped inside the workspace')
      assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false)
      assert.equal(await page.evaluate(()=>localStorage.length+sessionStorage.length),0)
      await page.screenshot({path:path.join(output,name+'-'+scenarios[index].slug+'.png'),fullPage:true})
      await page.getByRole('button',{name:'Finalizar intento',exact:true}).click()
      await page.getByRole('button',{name:'Sí, finalizar intento',exact:true}).click()
      await page.getByRole('button',{name:'Preparar laboratorio',exact:true}).waitFor()
    }
    assert.equal(commandCalls,4)
    assert.equal(readCalls,1)
    await page.getByRole('button',{name:'Cerrar sesión',exact:true}).click()
    await page.getByRole('link',{name:'Continuar con ShadowOps',exact:true}).waitFor()
    assert.equal(await page.getByRole('region',{name:'Laboratorios CKA'}).count(),0)
    const before=labCalls;await page.reload();await page.getByText('No has iniciado sesión.',{exact:true}).waitFor()
    assert.equal(labCalls,before)
    await context.close();console.log(name+': three exercises, terminal, grades, reload, finish, logout and storage checks passed')
  }
}catch(error){if(last)await last.screenshot({path:path.join(output,'failure.png'),fullPage:true});throw error}finally{await browser.close()}
