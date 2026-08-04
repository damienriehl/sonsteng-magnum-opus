// Usage: node shot.js <url> <out.png> [view|full] [width] [scale] [scrollTarget]
// scrollTarget: "#selector" (scrollIntoView center) or a pixel number.
let pup;
for (const candidate of [process.env.PUP_DIR, 'puppeteer', '/home/damienriehl/.npm/_npx/7d92d9a2d2ccc630/node_modules/puppeteer'].filter(Boolean)) {
  try { pup = require(candidate); break; } catch (_) {}
}
if (!pup) throw new Error('Puppeteer unavailable (set PUP_DIR or install puppeteer)');
(async () => {
  const [,, url, out, mode='view', width='1440', scale='1.5', target='0'] = process.argv;
  const b = await pup.launch({ executablePath:process.env.CHROME_BIN||process.env.CHROMIUM_PATH||'/snap/bin/chromium', headless:process.env.HEADLESS==='1', userDataDir:'/tmp/sonsteng-shot-'+process.pid,
    args:['--no-sandbox','--disable-dev-shm-usage','--disable-crash-reporter','--disable-breakpad','--hide-scrollbars','--window-size='+width+',1300'] });
  const p = await b.newPage();
  await p.setViewport({ width:+width, height:1150, deviceScaleFactor:+scale });
  await p.goto(url, { waitUntil:'networkidle2', timeout:45000 });
  if (target.startsWith('#')) { try{ await p.$eval(target, el=>el.scrollIntoView({block:'start'})); }catch(e){} }
  else if (+target) { await p.evaluate(y=>window.scrollTo(0,y), +target); }
  await new Promise(r=>setTimeout(r, 1200));
  await p.screenshot({ path:out, fullPage: mode==='full' });
  await b.close(); console.log('OK', out);
})().catch(e=>{ console.error('ERR', e.message); process.exit(1); });
