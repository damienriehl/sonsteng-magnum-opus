/* ============================================================================
   verify-spikes.js — drives the two editor spikes through their exact races and
   asserts the interaction rules PASS/FAIL. DISPLAY=:0 puppeteer (headful) on the
   home box's Xwayland, snap chromium, --no-sandbox. Screenshots + reads them back
   are left to the caller; this script logs a machine-checkable assertion table.
   Run: DISPLAY=:0 node verify-spikes.js
   ============================================================================ */
const puppeteer = require('/home/damienriehl/.npm/_npx/7d92d9a2d2ccc630/node_modules/puppeteer');
const path = require('path');

const DIR = __dirname;
const OUT = process.env.HOME;
const results = [];
function assert(rule, cond, detail){
  results.push({rule, pass: !!cond, detail: detail||''});
  console.log((cond?'PASS':'FAIL')+'  '+rule+(detail?'  — '+detail:''));
}

async function run(){
  const browser = await puppeteer.launch({
    executablePath: '/snap/bin/chromium',
    headless: false,
    args: ['--no-sandbox','--disable-dev-shm-usage','--window-size=1280,1400']
  });

  /* ========================== SPIKE 1 — blur/Save ========================= */
  {
    const page = await browser.newPage();
    await page.setViewport({width:1280, height:1400});
    page.on('console', m => { const t=m.text(); if(t.startsWith('__A__')) console.log('   '+t); });
    await page.goto('file://'+path.join(DIR,'spike-blur-save.html'), {waitUntil:'load'});
    await page.waitForFunction(()=>window.SpikeBlur);

    const R1 = 'platform/pitch:1', R2 = 'platform/pitch:2';

    // --- R1 + R6: blur is inert (flush-to-draft only; never commit) ---------
    await page.evaluate((ref)=>{ SpikeBlur.setSlow(false); SpikeBlur.focusBlock(ref);
      SpikeBlur.typeInto(ref,'EDITED opening line about the practicum.'); }, R1);
    // click ANOTHER block mid-edit (blur R1) — must NOT commit R1
    await page.evaluate((ref2)=>{ SpikeBlur.blurBlock('platform/pitch:1');
      SpikeBlur.focusBlock(ref2); }, R2);
    let srv = await page.evaluate(()=>SpikeBlur.server());
    let s1 = await page.evaluate((r)=>SpikeBlur.session(r), R1);
    assert('R1/R6 blur is inert (no commit on blur or block-switch)',
      srv.count===0 && s1.dirty===true,
      'server.count='+srv.count+' block1.dirty='+s1.dirty);
    assert('R1 blur flushed a DRAFT (snapshot preserved across blur)',
      /EDITED opening/.test(s1.snapshot), 'snapshot="'+s1.snapshot.slice(0,24)+'…"');

    // --- R2: suggestion_id minted once, stable across edits -----------------
    let idA = s1.suggestionId;
    await page.evaluate((ref)=>{ SpikeBlur.focusBlock(ref);
      SpikeBlur.typeInto(ref,'EDITED opening line — revised again.'); }, R1);
    let s1b = await page.evaluate((r)=>SpikeBlur.session(r), R1);
    assert('R2 suggestion_id minted ONCE per edit-session (stable across edits)',
      idA && s1b.suggestionId===idA, 'id '+(idA||'').slice(0,8)+' stable='+(s1b.suggestionId===idA));

    // --- R3 + R4: triple-click Save (slow net) => ONE logical suggestion ----
    await page.evaluate(()=>{ SpikeBlur.setSlow(true,500); });
    await page.evaluate((ref)=>{ SpikeBlur.tripleClickSave(ref); }, R1);
    // during the in-flight window the state must be SAVING and Save disabled
    let midState = await page.evaluate((r)=>SpikeBlur.session(r).state, R1);
    await page.waitForFunction((r)=>SpikeBlur.session(r).state==='IDLE', {timeout:4000}, R1);
    srv = await page.evaluate(()=>SpikeBlur.server());
    assert('R3 Save disabled synchronously (state=SAVING during await)',
      midState==='SAVING', 'mid-flight state='+midState);
    assert('R4 triple-click Save => ONE logical suggestion (dedupe by id)',
      srv.count===1 && srv.calls===1, 'server.count='+srv.count+' network.calls='+srv.calls);
    let s1c = await page.evaluate((r)=>SpikeBlur.session(r), R1);
    assert('R2 id cleared on Sent ✓ (draft purged)',
      s1c.suggestionId===null && s1c.dirty===false, 'id='+s1c.suggestionId+' dirty='+s1c.dirty);

    // --- R5: 401 preserves draft + id, offers friendly re-auth --------------
    await page.evaluate((ref)=>{ SpikeBlur.setSlow(true,300); SpikeBlur.setFail401(true);
      SpikeBlur.focusBlock(ref); SpikeBlur.typeInto(ref,'A tiered-facts sentence, edited by John.'); }, R2);
    let idB = (await page.evaluate((r)=>SpikeBlur.session(r), R2)).suggestionId;
    await page.evaluate((ref)=>{ SpikeBlur.clickSave(ref); }, R2);
    await page.waitForFunction((r)=>SpikeBlur.session(r).state!=='SAVING', {timeout:4000}, R2);
    let s2 = await page.evaluate((r)=>SpikeBlur.session(r), R2);
    let reauthShown = await page.$eval('.reauth.show', ()=>true).catch(()=>false);
    // this block's suggestion (idB) must NOT be on the server yet (401 blocked it).
    let idBonServer = await page.evaluate((id)=>SpikeBlur.server().ids.indexOf(id)!==-1, idB);
    assert('R5 401 preserves draft + id (recoverable, not committed)',
      s2.suggestionId===idB && /edited by John/.test(s2.snapshot) && idBonServer===false,
      'id preserved='+(s2.suggestionId===idB)+' idB-on-server='+idBonServer);
    assert('R5 friendly re-auth affordance shown (never a raw 4xx)', reauthShown, 'reauth visible='+reauthShown);
    // resend after re-auth reuses the SAME id => one logical suggestion
    await page.evaluate((ref)=>{ SpikeBlur.clickSave(ref); }, R2);
    await page.waitForFunction((r)=>SpikeBlur.session(r).state==='IDLE', {timeout:4000}, R2);
    srv = await page.evaluate(()=>SpikeBlur.server());
    assert('R5 resend after re-auth reuses same id (idempotent)',
      srv.ids.indexOf(idB)!==-1 && srv.suggestions[idB], 'server has id '+(idB||'').slice(0,8));

    // --- R7: origin scroll-spy/toggle neutralized inside edit region --------
    let spyBefore = await page.evaluate(()=>SpikeBlur.spyHits());
    await page.evaluate((ref)=>{ SpikeBlur.setFail401(false); SpikeBlur.focusBlock(ref); }, R1);
    // dispatch events the origin handlers listen for, INSIDE the edit region
    let swallowed = await page.evaluate((ref)=>{
      const eb = document.querySelector('.eb[data-ref="'+ref.replace(/([^a-zA-Z0-9_-])/g,'\\$1')+'"]');
      let sawScrollSpy = false;
      const probe = ()=>{ sawScrollSpy = true; };
      window.addEventListener('scroll', probe, {passive:true});
      // a wheel/mousedown inside the region should be stopped at capture phase
      eb.dispatchEvent(new MouseEvent('mousedown',{bubbles:true,cancelable:true}));
      eb.dispatchEvent(new WheelEvent('wheel',{bubbles:true,cancelable:true}));
      window.removeEventListener('scroll', probe);
      // the toggle handler must not have flipped type-size from an edit-region event
      return {toggleFontSize: document.documentElement.style.fontSize};
    }, R1);
    assert('R7 origin handlers neutralized in edit region (capture-phase stop)',
      swallowed.toggleFontSize==='' , 'doc.fontSize="'+swallowed.toggleFontSize+'" (unchanged)');

    // --- R8: hash moves => draft discarded with a gentle note ---------------
    await page.evaluate((ref)=>{ SpikeBlur.setSlow(false); SpikeBlur.focusBlock(ref);
      SpikeBlur.typeInto(ref,'Assessment sentence edited — pending a hash move.'); }, 'platform/pitch:3');
    let before3 = await page.evaluate(()=>SpikeBlur.session('platform/pitch:3'));
    await page.evaluate(()=>{ SpikeBlur.bumpHash('platform/pitch:3'); });
    let after3 = await page.evaluate(()=>SpikeBlur.session('platform/pitch:3'));
    let noteShown = await page.$eval('.note.show', el=>el.textContent).catch(()=>null);
    assert('R8 hash move discards the stale draft (keyed to source_ref+hash)',
      before3.dirty===true && after3.dirty===false && after3.suggestionId===null,
      'before.dirty='+before3.dirty+' after.dirty='+after3.dirty);
    assert('R8 gentle note shown on discard (nothing lost silently)',
      !!noteShown && /set aside|updated/.test(noteShown), noteShown?('“'+noteShown.slice(0,40)+'…”'):'no note');

    await page.screenshot({path: path.join(OUT,'spike1-blur-save.png')});
    console.log('   [screenshot] '+path.join(OUT,'spike1-blur-save.png'));
    await page.close();
  }

  /* ==================== SPIKE 2 — selection commenting ==================== */
  {
    const page = await browser.newPage();
    await page.setViewport({width:1280, height:1200});
    await page.goto('file://'+path.join(DIR,'spike-selection-comment.html'), {waitUntil:'load'});
    await page.waitForFunction(()=>window.SpikeSel);

    const RF1='platform/m03/facts:1', RF2='platform/m03/facts:2';

    // --- C1 + C2: select => Comment anchors to EXACT range ------------------
    // select the phrase "four feet inside the recorded boundary line"
    const full = await page.evaluate((r)=>document.querySelector('.cblock[data-ref="'+r.replace(/([^a-zA-Z0-9_-])/g,'\\$1')+'"] .ctext').textContent, RF1);
    const startChar = full.indexOf('four feet');
    const endChar = full.indexOf('boundary line')+'boundary line'.length;
    await page.evaluate((r,a,b)=>SpikeSel.dragSelect(r,a,b), RF1, startChar, endChar);
    let cap = await page.evaluate(()=>SpikeSel.captured());
    let floatVis = await page.evaluate(()=>SpikeSel.floatVisible());
    assert('C1 selection captured at rest (stored range text = selection)',
      cap && /four feet inside the recorded boundary line/.test(cap.text) && cap.source_ref===RF1,
      'captured="'+(cap?cap.text:'null')+'"');
    assert('C1 floating Comment button appears on non-empty selection', floatVis, 'floatVisible='+floatVis);
    // click the floating button (mousedown+preventDefault) — must anchor to the range
    await page.evaluate(()=>SpikeSel.clickFloat());
    let bub = await page.evaluate(()=>SpikeSel.bubbleOpen());
    assert('C2 Comment click anchors to EXACT range (not a collapsed caret)',
      bub && /four feet inside the recorded boundary line/.test(bub.anchor) && bub.ref===RF1,
      'bubble anchor="'+(bub?bub.anchor:'null')+'"');
    await page.evaluate(()=>SpikeSel.sendBubble('Confirm the 4ft figure against the 2019 survey.'));
    let srv = await page.evaluate(()=>SpikeSel.server());
    assert('C2 comment persisted with the exact anchor text',
      srv.count===1 && /four feet inside the recorded boundary line/.test(srv.comments[0].anchor_text),
      'server.count='+srv.count);

    // --- C3: tremor flicker — last stable range survives a collapsed caret --
    const startC = full.indexOf('survey stakes');
    const endC = full.indexOf('2019')+4;
    await page.evaluate((r,a,b)=>SpikeSel.tremorFlicker(r,a,b), RF1, startC, endC);
    let capT = await page.evaluate(()=>SpikeSel.captured());
    assert('C3 tremor: last stable non-empty range preserved through jitter',
      capT && /survey stakes/.test(capT.text), 'captured after flicker="'+(capT?capT.text:'null')+'"');
    // and the button still commits that range
    await page.evaluate(()=>SpikeSel.clickFloat());
    let bubT = await page.evaluate(()=>SpikeSel.bubbleOpen());
    assert('C3 stable range still commentable after the tremor',
      bubT && /survey stakes/.test(bubT.anchor), 'anchor="'+(bubT?bubT.anchor:'null')+'"');
    await page.evaluate(()=>SpikeSel.sendBubble(''));

    // --- C4: cross-block selection clamps to START block --------------------
    await page.evaluate((r1,r2)=>{
      const t1=document.querySelector('.cblock[data-ref="'+r1.replace(/([^a-zA-Z0-9_-])/g,'\\$1')+'"] .ctext').textContent;
      return SpikeSel.dragSelectAcross(r1, t1.indexOf('survey stakes'), r2, 20);
    }, RF1, RF2);
    let capX = await page.evaluate(()=>SpikeSel.captured());
    assert('C4 cross-block selection clamps to the START block (prose only, no button label)',
      capX && capX.source_ref===RF1 && !/adverse possession/.test(capX.text||'')
        && !/Comment on this paragraph/.test(capX.text||'') && /parcel\.?$/.test((capX.text||'').trim()),
      'ref='+(capX?capX.source_ref:'null')+' tail="'+(capX?capX.text.trim().slice(-18):'')+'"');

    // --- C5: block-affordance fallback (one tap, no selection) --------------
    await page.evaluate(()=>{ try{window.getSelection().removeAllRanges();}catch(e){} });
    await page.evaluate((r)=>SpikeSel.clickBlockComment(r), RF2);
    let bubF = await page.evaluate(()=>SpikeSel.bubbleOpen());
    assert('C5 per-block fallback works with NO selection (one tap)',
      bubF && bubF.ref===RF2 && /adverse possession/.test(bubF.anchor),
      'fallback anchor ref='+(bubF?bubF.ref:'null'));
    await page.evaluate(()=>SpikeSel.sendBubble('Whole-paragraph note via fallback.'));
    srv = await page.evaluate(()=>SpikeSel.server());
    assert('C5 fallback comment persisted', srv.count===3, 'server.count='+srv.count);

    await page.screenshot({path: path.join(OUT,'spike2-selection-comment.png')});
    console.log('   [screenshot] '+path.join(OUT,'spike2-selection-comment.png'));
    await page.close();
  }

  await browser.close();

  const passed = results.filter(r=>r.pass).length;
  console.log('\n===== ASSERTION SUMMARY: '+passed+'/'+results.length+' PASS =====');
  if(passed!==results.length){ process.exitCode = 1;
    console.log('FAILURES:'); results.filter(r=>!r.pass).forEach(r=>console.log('  - '+r.rule+' :: '+r.detail)); }
}

run().catch(e=>{ console.error('HARNESS ERROR', e); process.exitCode = 2; });
