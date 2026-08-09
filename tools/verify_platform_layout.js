/* Release gate for responsive hierarchy, overflow, overlap, Large Type, and print. */
'use strict';
const fs = require('fs');
const path = require('path');
const REPO = path.resolve(__dirname, '..');
const SITE = path.join(REPO, 'site', 'platform');
const MATRIX_PATH = path.join(__dirname, 'platform_browser_matrix.json');

function loadPuppeteer() {
  const candidates = [process.env.PUP_DIR, 'puppeteer', '/home/damienriehl/.npm/_npx/7d92d9a2d2ccc630/node_modules/puppeteer'].filter(Boolean);
  for (const candidate of candidates) { try { return require(candidate); } catch (_) {} }
  throw new Error('Puppeteer unavailable (set PUP_DIR or install puppeteer)');
}
function walk(dir) {
  return fs.readdirSync(dir, {withFileTypes:true}).flatMap((e) => e.isDirectory() ? walk(path.join(dir,e.name)) : [path.join(dir,e.name)]);
}
function relUrl(p) { return 'file://' + path.join(SITE, p); }
function matrixErrors(m) {
  const found = new Set(m.pages.map((p) => p.family));
  const errors = m.requiredFamilies.filter((f) => !found.has(f)).map((f) => `matrix missing required family: ${f}`);
  for (const w of [1280,960,959,672,671,480,390]) if (!m.viewports.some((v) => v.width === w)) errors.push(`matrix missing required viewport: ${w}`);
  for (const mode of ['baseline','large']) if (!m.typeModes.includes(mode)) errors.push(`matrix missing type mode: ${mode}`);
  return errors;
}
async function setMode(page, mode) {
  await page.evaluateOnNewDocument((large) => localStorage.setItem('sonsteng-type-lg', large ? '1' : '0'), mode === 'large');
}
async function inspect(page, hierarchy) {
  return page.evaluate((map) => {
    const visible = (el) => { if (!el) return false; const s=getComputedStyle(el),r=el.getBoundingClientRect(); return s.display!=='none'&&s.visibility!=='hidden'&&+s.opacity!==0&&r.width>0&&r.height>0; };
    const box = (el) => { const r=el.getBoundingClientRect(); return {l:r.left,t:r.top,r:r.right,b:r.bottom,w:r.width,h:r.height}; };
    const overlaps = (a,b) => a.l < b.r-1 && a.r > b.l+1 && a.t < b.b-1 && a.b > b.t+1;
    const headings=[...document.querySelectorAll('h1,h2,h3,h4,h5,h6')].filter(visible);
    let prior=0; const jumps=[]; headings.forEach((h)=>{const level=+h.tagName[1];if(prior&&level>prior+1)jumps.push(`h${prior}->h${level}: ${(h.textContent||'').trim().slice(0,50)}`);prior=level;});
    const regions=[...document.querySelectorAll('.masthead,nav,.card,.doc-card,.viz-card,main button,main input,main textarea,main select')]
      .filter(visible).map((el)=>({el,box:box(el)}));
    const collisions=[];
    for(let i=0;i<regions.length;i++) for(let j=i+1;j<regions.length;j++) {
      const a=regions[i],b=regions[j]; if(a.el.contains(b.el)||b.el.contains(a.el))continue;
      if(overlaps(a.box,b.box)) collisions.push(`${a.el.className||a.el.tagName} <> ${b.el.className||b.el.tagName}`);
      if(collisions.length>=8)break;
    }
    const metrics={};
    for(const [role,selector] of Object.entries(map||{})){const el=[...document.querySelectorAll(selector)].find(visible);if(el){const s=getComputedStyle(el),r=el.getBoundingClientRect();metrics[role]={selector,fontSize:+parseFloat(s.fontSize).toFixed(2),fontWeight:s.fontWeight,lineHeight:s.lineHeight,top:+r.top.toFixed(1),marginBottom:s.marginBottom,text:(el.textContent||'').trim().slice(0,60)};}}
    const hierarchyErrors=[];
    if(metrics.primary&&metrics.support&&metrics.primary.fontSize<=metrics.support.fontSize)hierarchyErrors.push(`primary ${metrics.primary.fontSize}px <= support ${metrics.support.fontSize}px`);
    if(metrics.section&&metrics.metadata&&metrics.section.fontSize<=metrics.metadata.fontSize)hierarchyErrors.push(`section ${metrics.section.fontSize}px <= metadata ${metrics.metadata.fontSize}px`);
    const focusables=[...document.querySelectorAll('a[href],button,input,textarea,select')].filter(visible);
    const inaccessible=focusables.filter((el)=>!(el.getAttribute('aria-label')||el.getAttribute('title')||(el.textContent||'').trim()||el.labels&&el.labels.length)).slice(0,8).map((el)=>el.outerHTML.slice(0,100));
    return {title:document.title,large:document.documentElement.classList.contains('type-lg'),h1:headings.filter((h)=>h.tagName==='H1').length,jumps,overflow:document.documentElement.scrollWidth-document.documentElement.clientWidth,collisions,metrics,hierarchyErrors,inaccessible};
  }, hierarchy || {});
}
async function run() {
  const printOnly=process.argv.includes('--print');
  const m=JSON.parse(fs.readFileSync(MATRIX_PATH,'utf8')); const initial=matrixErrors(m);
  if(initial.length){initial.forEach((x)=>console.error('FAIL matrix:',x));return 1;}
  const pup=loadPuppeteer(); const browser=await pup.launch({executablePath:process.env.CHROME_BIN||process.env.CHROMIUM_PATH||'/snap/bin/chromium',headless:process.env.HEADLESS==='1',userDataDir:path.join('/tmp',`sonsteng-layout-${process.pid}`),args:['--no-sandbox','--disable-dev-shm-usage','--disable-crash-reporter','--disable-breakpad']});
  let fails=0, checks=0; const report=[];
  const pages=printOnly?m.pages.filter((p)=>p.print):m.pages.filter((p)=>!p.interactive);
  const viewports=printOnly?[m.viewports.find((v)=>v.width===1280)]:m.viewports;
  for(const spec of pages) for(const vp of viewports) for(const mode of m.typeModes){
    const page=await browser.newPage(); await page.setViewport(vp); await setMode(page,mode);
    try { await page.goto(relUrl(spec.path),{waitUntil:'domcontentloaded',timeout:30000}); if(printOnly)await page.emulateMediaType('print');
      const got=await inspect(page,spec.hierarchy); const errors=[];
      if(got.large!==(mode==='large'))errors.push(`type mode mismatch: expected ${mode}, class type-lg=${got.large}`);
      if(got.h1!==1)errors.push(`visible H1 count ${got.h1}`); if(got.jumps.length)errors.push(`heading jumps ${got.jumps.join(', ')}`);
      for(const role of Object.keys(spec.hierarchy||{}))if(!got.metrics[role])errors.push(`hierarchy role not rendered: ${role} (${spec.hierarchy[role]})`);
      if(got.overflow>1)errors.push(`horizontal overflow ${got.overflow}px`); if(got.collisions.length)errors.push(`overlap ${got.collisions.join('; ')}`);
      if(got.inaccessible.length)errors.push(`unnamed controls ${got.inaccessible.join('; ')}`); errors.push(...got.hierarchyErrors);
      if(printOnly){const state=await page.evaluate(()=>({main:!![...document.querySelectorAll('main')].find((e)=>{const r=e.getBoundingClientRect();return getComputedStyle(e).display!=='none'&&r.height>0}),chrome:[...document.querySelectorAll('.masthead,nav,button,.segmented-toggle')].filter((e)=>getComputedStyle(e).display!=='none'&&e.getBoundingClientRect().height>0).length}));if(!state.main)errors.push('print main content hidden');if(state.chrome)errors.push(`print interactive chrome visible (${state.chrome})`);}
      checks++;fails+=errors.length?1:0;report.push({family:spec.family,viewport:vp.name,mode,print:printOnly,errors,metrics:got.metrics});
      console.log(`${errors.length?'FAIL':'PASS'} ${spec.family} ${vp.name} ${mode}${errors.length?' — '+errors.join(' | '):''}`);
    } catch(e){fails++;console.error(`FAIL ${spec.family} ${vp.name} ${mode} — ${e.message}`);} finally{await page.close();}
  }
  if(!printOnly){
    const corpus=walk(SITE).filter((p)=>p.endsWith('.html')).filter((p)=>{const rel=path.relative(SITE,p).replaceAll(path.sep,'/');return !m.generatedCorpus.exclude.includes(rel)&&!rel.startsWith('chat/');});
    const vp=m.viewports.find((v)=>v.width===390);
    for(const file of corpus) for(const mode of m.typeModes){const page=await browser.newPage();await page.setViewport(vp);await setMode(page,mode);const rel=path.relative(SITE,file).replaceAll(path.sep,'/');try{await page.goto('file://'+file,{waitUntil:'domcontentloaded',timeout:30000});const state=await page.evaluate(()=>({overflow:document.documentElement.scrollWidth-document.documentElement.clientWidth,large:document.documentElement.classList.contains('type-lg')}));const errors=[];if(state.large!==(mode==='large'))errors.push(`type mode mismatch: expected ${mode}, class type-lg=${state.large}`);if(state.overflow>1)errors.push(`corpus-overflow ${state.overflow}px`);checks++;if(errors.length){fails++;console.error(`FAIL corpus ${rel} ${mode} — ${errors.join(' | ')}`);}}catch(e){fails++;console.error(`FAIL corpus ${rel} ${mode} — ${e.message}`);}finally{await page.close();}}
    console.log(`CORPUS ${corpus.length} generated pages × ${m.typeModes.length} modes checked at 390px`);
  }
  fs.mkdirSync(path.join(REPO,'build'),{recursive:true});fs.writeFileSync(path.join(REPO,'build',printOnly?'platform-print-report.json':'platform-layout-report.json'),JSON.stringify(report,null,2));
  await browser.close();console.log(`LAYOUT SUMMARY ${checks-fails}/${checks} PASS`);return fails?1:0;
}
run().then((code)=>process.exit(code)).catch((e)=>{console.error('BROWSER GATE ERROR:',e.message);process.exit(1);});
