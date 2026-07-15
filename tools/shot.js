// Headful screenshot on the real X display. Needs DISPLAY + XAUTHORITY exported.
// Usage: node shot.js <url> <out.png> [full|view]
const pup = require(process.env.PUP_DIR);
(async () => {
  const [,, url, out, mode='full'] = process.argv;
  const browser = await pup.launch({
    executablePath: '/snap/bin/chromium', headless: false,
    args: ['--no-sandbox','--disable-dev-shm-usage','--hide-scrollbars','--window-size=1500,1300']
  });
  const page = await browser.newPage();
  await page.setViewport({ width: 1440, height: 1150, deviceScaleFactor: 1.5 });
  await page.goto(url, { waitUntil: 'networkidle2', timeout: 45000 });
  await new Promise(r => setTimeout(r, 1400));
  await page.screenshot({ path: out, fullPage: mode === 'full' });
  await browser.close();
  console.log('OK', out);
})().catch(e => { console.error('ERR', e.message); process.exit(1); });
