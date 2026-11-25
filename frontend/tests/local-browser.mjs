import { chromium } from 'playwright'
import assert from 'node:assert/strict'
const browser=await chromium.launch({headless:true})
const page=await browser.newPage({viewport:{width:1440,height:1000}})
const foreign=[]
const editorOpenStatuses=[]
page.on('request',request=>{if(new URL(request.url()).origin!=='http://127.0.0.1:8042')foreign.push(request.url())})
page.on('response',response=>{
  if(new URL(response.url()).pathname==='/api/labs/edit-open')editorOpenStatuses.push(response.status())
})
try {
  await page.goto('http://127.0.0.1:8042')
  // The edit exercise is explicit: do not depend on catalog ordering.
  await page.getByRole('button',{name:/Servicio de catálogo sin backends/}).click()
  const prepare=page.getByRole('button',{name:'Preparar laboratorio',exact:true})
  // Observe the actual request, not just a downstream button. The server
  // permits temporary 409 contention while its serialized reaper runs.
  const deadline=Date.now()+15000
  while (true) {
    const [response]=await Promise.all([
      page.waitForResponse(r=>new URL(r.url()).pathname==='/api/labs/prepare' && r.request().method()==='POST',{timeout:80000}),
      prepare.click(),
    ])
    const status=response.status()
    console.log('CKA_LOCAL_PREPARE_HTTP '+status)
    if (status===409 && Date.now()<deadline) {
      // Wait for the UI to release its busy guard before retrying.
      await page.waitForFunction(()=>Array.from(document.querySelectorAll('button')).some(b=>b.textContent?.trim()==='Preparar laboratorio' && !b.disabled),{},{timeout:15000})
      continue
    }
    assert.equal(response.ok(),true,'Local prepare returned HTTP '+status)
    break
  }
  const finish=page.getByRole('button',{name:'Finalizar intento',exact:true})
  await finish.waitFor()
  assert.equal(await finish.getAttribute('tabindex'),'-1')
  await finish.focus();await page.keyboard.press('Enter')
  assert.equal(await page.locator('dialog[open]').count(),0)
  // The attempt may be visible before prepare() releases the busy guard.
  // Wait while the action bar is visible: maximizing hides it by design.
  await page.locator('.action-left').filter({hasText:'LABORATORIO ACTIVO'}).waitFor()
  await page.getByRole('button',{name:'Maximizar terminal',exact:true}).click()
  const input=page.locator('.terminal-screen .xterm-helper-textarea')
  // Wait for xterm's prompt and real keyboard focus, not merely the outer
  // React terminal node. Chromium can otherwise send the first keys during
  // the terminal's asynchronous layout/activation.
  await page.waitForFunction(() => document.querySelector('.terminal-screen .xterm-rows')?.textContent?.includes('cka@lab'))
  await input.focus()
  assert.equal(await input.evaluate(el => document.activeElement === el), true)
  // Real keyboard path (not synthetic insertText). Wait on the app's
  // observable busy/active state, not the internal xterm DOM renderer:
  // terminal rows can be reflowed/recycled without matching literal text.
  await page.keyboard.type('k edit svc/catalog-api-svc',{delay:5})
  await input.press('Enter')
  try {
    await page.locator('[data-editor-active="true"]').waitFor()
  } catch (error) {
    // No terminal contents, tokens, or editor buffers in CI logs.
    const state=await page.evaluate(() => ({
      terminalVisible: Boolean(document.querySelector('.terminal-screen .xterm-rows')),
      keyboardFocused: document.activeElement?.classList.contains('xterm-helper-textarea') === true,
      editorActive: Boolean(document.querySelector('[data-editor-active="true"]')),
      editOpenStatuses,
      labBusy: document.querySelector('.action-left')?.textContent?.includes('Editando con nano') === true,
      hasUiError: Boolean(document.querySelector('.lab .error')),
    }))
    console.error('CKA_LOCAL_NANO_START_DIAGNOSTIC '+JSON.stringify(state))
    throw error
  }
  await page.waitForFunction(()=>document.querySelector('.terminal-screen')?.textContent?.includes('GNU nano'))
  await input.focus();await page.keyboard.press('Control+x')
  await page.locator('[data-editor-active="true"]').waitFor({state:'hidden'})
  await page.getByRole('button',{name:'Restaurar terminal',exact:true}).click()
  await finish.click()
  const confirm=page.getByRole('button',{name:'Sí, finalizar intento',exact:true})
  await confirm.focus();await page.keyboard.press('Enter')
  assert.equal(await page.locator('dialog[open]').count(),1)
  await confirm.click()
  await page.getByRole('button',{name:'Preparar laboratorio',exact:true}).waitFor()
  assert.deepEqual(foreign,[])
  console.log('CKA_LOCAL_BROWSER_REAL_NANO_NO_CLOUD_OK')
} finally {await browser.close()}
