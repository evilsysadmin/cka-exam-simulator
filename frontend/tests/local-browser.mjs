import { chromium } from 'playwright'
import assert from 'node:assert/strict'
const browser=await chromium.launch({headless:true})
const page=await browser.newPage({viewport:{width:1440,height:1000}})
const foreign=[]
page.on('request',request=>{if(new URL(request.url()).origin!=='http://127.0.0.1:8042')foreign.push(request.url())})
try {
  await page.goto('http://127.0.0.1:8042')
  // The edit exercise is explicit: do not depend on catalog ordering.
  await page.getByRole('button',{name:/Servicio de catálogo sin backends/}).click()
  await page.getByRole('button',{name:'Preparar laboratorio',exact:true}).click()
  const finish=page.getByRole('button',{name:'Finalizar intento',exact:true})
  await finish.waitFor()
  assert.equal(await finish.getAttribute('tabindex'),'-1')
  await finish.focus();await page.keyboard.press('Enter')
  assert.equal(await page.locator('dialog[open]').count(),0)
  await page.getByRole('button',{name:'Maximizar terminal',exact:true}).click()
  const input=page.locator('.terminal-screen .xterm-helper-textarea')
  // Wait for xterm's prompt and real keyboard focus, not merely the outer
  // React terminal node. Chromium can otherwise send the first keys during
  // the terminal's asynchronous layout/activation.
  await page.waitForFunction(() => document.querySelector('.terminal-screen .xterm-rows')?.textContent?.includes('cka@lab'))
  await input.focus()
  assert.equal(await input.evaluate(el => document.activeElement === el), true)
  // Exercise xterm through real key events and confirm that the complete
  // command reached its terminal buffer before Enter. insertText may only
  // dispatch an input event, which is not a reliable xterm keyboard handshake.
  const editCommand='k edit svc/catalog-api-svc'
  await page.keyboard.type(editCommand,{delay:5})
  await page.waitForFunction(command =>
    [...document.querySelectorAll('.terminal-screen .xterm-rows .xterm-row')].some(
      row => row.textContent?.replace(/\s/g, ' ').includes(command)),editCommand,{timeout:5000})
  await input.press('Enter')
  try {
    await page.locator('[data-editor-active="true"]').waitFor()
  } catch (error) {
    // No terminal contents, tokens, or editor buffers in CI logs.
    const state=await page.evaluate(() => ({
      terminalVisible: Boolean(document.querySelector('.terminal-screen .xterm-rows')),
      keyboardFocused: document.activeElement?.classList.contains('xterm-helper-textarea') === true,
      editorActive: Boolean(document.querySelector('[data-editor-active="true"]')),
      commandEchoed: [...document.querySelectorAll('.terminal-screen .xterm-rows .xterm-row')].some(
        row => row.textContent?.replace(/\s/g, ' ').includes('k edit svc/catalog-api-svc')),
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
