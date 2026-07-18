/* ============================================================================
   Sonsteng Practicum — chat.js
   The consultation-room client-interview app. Vanilla JS, zero dependencies.

   Design contract: docs/research/design-direction.md §§2-4, 6, 8, 9.
   Race-proofing contract: 2026-07-17-001 plan, "Chat UI race-proofing".
   API contract v1: GET /v1/session, POST /v1/chat|/v1/debrief.

   Everything dynamic is rendered via createElement + textContent — NEVER
   innerHTML with model/user/server data. The only innerHTML use is the static,
   author-controlled <style> string injected at boot.
   ============================================================================ */
(function () {
  'use strict';

  var ROOT = document.getElementById('chat-root');
  if (!ROOT) return;

  /* ---------- state machine ------------------------------------------------ */
  var S = { IDLE: 'IDLE', SENDING: 'SENDING', RETRYING: 'RETRYING', CAPPED: 'CAPPED', ENDED: 'ENDED' };
  var state = S.IDLE;

  /* ---------- config (query params + <meta>) ------------------------------- */
  var Q = new URLSearchParams(location.search);
  function meta(name) { var m = document.querySelector('meta[name="' + name + '"]'); return m ? m.content : ''; }
  var cfg = {
    matter_id: Q.get('matter') || '',
    persona_id: Q.get('persona') || '',
    title: Q.get('title') || 'Client Interview',
    client: Q.get('client') || 'The client',
    role: Q.get('role') || 'Client',
    packet: Q.get('packet') || '',
    represented: Q.get('represented') === '1',
    bypass: Q.get('bypass') || '',           // forwarded on /session ONLY; never logged/rendered
    apiParam: Q.get('api') || '',
    sample: Q.get('sample') === '1'          // scripted-replay demo: no API, no key, no session
  };

  /* ---------- API base resolution chain ------------------------------------
     ?api= -> localStorage sonsteng_api -> <meta sonsteng-api> -> same-origin '/api' */
  function apiBase() {
    if (cfg.apiParam) return cfg.apiParam.replace(/\/+$/, '');
    var ls = null; try { ls = localStorage.getItem('sonsteng_api'); } catch (e) {}
    if (ls) return ls.replace(/\/+$/, '');
    var mt = meta('sonsteng-api');
    if (mt) return mt.replace(/\/+$/, '');
    return '/api';
  }
  if (cfg.apiParam) { try { localStorage.setItem('sonsteng_api', cfg.apiParam.replace(/\/+$/, '')); } catch (e) {} }

  /* ---------- storage: probe-write with in-memory fallback ----------------- */
  function makeStore(backing) {
    var mem = {}, live = false;
    try { var k = '__ss_probe__'; backing.setItem(k, '1'); backing.removeItem(k); live = true; } catch (e) { live = false; }
    return {
      live: live,
      get: function (k) { try { return live ? backing.getItem(k) : (k in mem ? mem[k] : null); } catch (e) { return (k in mem ? mem[k] : null); } },
      set: function (k, v) { try { if (live) backing.setItem(k, v); else mem[k] = v; } catch (e) { mem[k] = v; } },
      del: function (k) { try { if (live) backing.removeItem(k); else delete mem[k]; } catch (e) { delete mem[k]; } }
    };
  }
  var SS = makeStore(window.sessionStorage);   // per-tab: session + transcript
  var LS = makeStore(window.localStorage);     // cross-tab: type-lg pref, api

  var K_SESS = 'sonsteng_sess';
  var K_TURNS = 'sonsteng_turns';

  /* ---------- transport (mock hook for the dev harness) --------------------
     window.__SONSTENG_MOCK__({path, method, body}) -> {ok, status, data}
     lets test.html exercise every race/error path with no Worker. */
  function api(path, opts) {
    opts = opts || {};
    var mock = window.__SONSTENG_MOCK__;
    if (typeof mock === 'function') { return Promise.resolve(mock({ path: path, method: opts.method || 'POST', body: opts.body || null })); }
    var fo = { method: opts.method || 'POST', headers: {}, cache: 'no-store', credentials: 'omit' };
    if (opts.body) { fo.headers['content-type'] = 'application/json'; fo.body = JSON.stringify(opts.body); }
    return fetch(apiBase() + path, fo).then(function (res) {
      return res.json().catch(function () { return null; }).then(function (data) {
        return { ok: res.ok, status: res.status, data: data };
      });
    });
  }

  function uuid() {
    try { if (window.crypto && crypto.randomUUID) return crypto.randomUUID(); } catch (e) {}
    return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, function (c) {
      var r = (Math.random() * 16) | 0, v = c === 'x' ? r : ((r & 0x3) | 0x8); return v.toString(16);
    });
  }

  /* ---------- session + transcript model ----------------------------------- */
  var session = null;            // {session_token, sid, pool, max_turns}
  var turns = [];                // [{turn_id, status:'pending'|'committed'|'unresolved', user, assistant, turn, remaining, state}]
  var maxTurns = 20;
  var draftTurnId = null;        // reuse the same turn_id on resend (idempotency)
  var rule42Shown = false;

  function loadSession() { try { return JSON.parse(SS.get(K_SESS) || 'null'); } catch (e) { return null; } }
  function saveSession() { SS.set(K_SESS, JSON.stringify(session)); }
  function loadTurns() { try { var t = JSON.parse(SS.get(K_TURNS) || '[]'); return Array.isArray(t) ? t : []; } catch (e) { return []; } }
  // In ?sample=1 replay mode the scripted turns must NEVER touch per-tab storage:
  // the replay reuses commitTurn() (which persists), and K_TURNS is the SAME key a
  // live interview reads at boot. Without this guard, playing a sample then
  // navigating the same tab to the live room would rehydrate the fake sample
  // transcript (inflating the turn counter / debrief gate and polluting the chat
  // history sent upstream) — and a sample opened after a real interview would
  // OVERWRITE that live transcript. Sample state is in-memory only; a reload
  // simply restarts the replay.
  function saveTurns() { if (cfg.sample) return; SS.set(K_TURNS, JSON.stringify(turns)); }
  function committed() { return turns.filter(function (t) { return t.status === 'committed'; }); }

  /* ============================================================================
     DOM construction — helpers (createElement/textContent only)
     ============================================================================ */
  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  }
  function pad2(n) { n = Number(n) || 0; return (n < 10 ? '0' : '') + n; }

  /* ---------- chat-specific layout CSS (author-controlled; injected) --------
     Composes from theme.css primitives; only chat layout lives here. */
  var CSS = [
    '.chat-wrap{max-width:52rem;margin:0 auto;padding:var(--sp-6) var(--gutter) var(--sp-16)}',
    '.chat-topbar{display:flex;align-items:center;justify-content:space-between;gap:var(--sp-3);flex-wrap:wrap;padding-top:var(--sp-6)}',
    '.chat-topbar .brand{font-family:var(--font-display);font-weight:900;font-size:var(--fs-md);letter-spacing:-.01em}',
    /* the shared segmented-toggle primitive is ~28px tall; bump the type-size */
    /* toggle to a 44px touch target on the chat page (WCAG 2.5.5), esp. since */
    /* it is itself an accessibility control used on mobile. */
    '.chat-topbar .segmented-toggle button{min-height:44px;display:inline-flex;align-items:center}',
    '.chat-head h1{font-size:var(--fs-xl);margin:var(--sp-3) 0 var(--sp-2)}',
    '.chat-head .who{font-family:var(--font-body);color:var(--ink-soft);margin:0}',
    '.chat-head .who .role{font-family:var(--font-mono);font-size:var(--fs-mono-xs);text-transform:uppercase;letter-spacing:.08em;color:var(--ink-faint);margin-left:.5em}',
    '.turn-counter{font-variant-numeric:tabular-nums}',
    '.stream{margin:var(--sp-8) 0 0}',
    '.msg{margin:var(--sp-6) 0;display:flex;flex-direction:column}',
    '.msg__who{font-family:var(--font-mono);font-size:var(--fs-mono-xs);text-transform:uppercase;letter-spacing:.08em;color:var(--ink-faint);margin-bottom:var(--sp-2)}',
    '.msg__body{padding:var(--sp-3) var(--sp-6);border-radius:var(--radius-card);line-height:1.62;white-space:pre-wrap;overflow-wrap:break-word}',
    '.msg--lawyer{align-items:flex-end}',
    '.msg--lawyer .msg__body{background:var(--paper-2);border:var(--rule) solid var(--line);border-left:var(--rule-bold) solid var(--brass);max-width:82%}',
    '.msg--client{align-items:flex-start}',
    '.msg--client .msg__who{color:var(--claret)}',
    '.msg--client .msg__body{background:var(--paper);border:var(--rule) solid var(--line-soft);font-size:var(--fs-md);max-width:88%}',
    '.considering-row{display:flex;align-items:center;gap:var(--sp-3);margin:var(--sp-6) 0;color:var(--ink-faint);font-family:var(--font-mono);font-size:var(--fs-mono-xs);text-transform:uppercase;letter-spacing:.08em}',
    '.chambers{margin:var(--sp-8) 0}',
    '.chambers .label{display:block;margin-bottom:var(--sp-2)}',
    '.chambers__open{font-style:italic;color:var(--ink);font-size:var(--fs-md);margin:0 0 var(--sp-3)}',
    '.chambers__actions{display:flex;gap:var(--sp-6);align-items:center;flex-wrap:wrap}',
    '.composer{display:flex;flex-direction:column;gap:var(--sp-3);margin-top:var(--sp-8)}',
    '.composer textarea{width:100%;min-height:118px;font-family:var(--font-body);font-size:1.18rem;line-height:1.5;color:var(--ink);background:var(--paper);border:var(--rule) solid var(--line);border-left:var(--rule-bold) solid var(--brass);border-radius:var(--radius);padding:var(--sp-3) var(--sp-6);resize:vertical}',
    /* large-type mode must scale the student's OWN input too, not just the client's
       replies — otherwise a low-vision user types into small text. */
    'html.type-lg .composer textarea{font-size:1.42rem}',
    '.composer__row{display:flex;justify-content:space-between;align-items:center;gap:var(--sp-3);flex-wrap:wrap}',
    '.composer__hint{font-family:var(--font-mono);font-size:var(--fs-mono-xs);color:var(--ink-faint);text-transform:uppercase;letter-spacing:.08em}',
    '.btn{font-family:var(--font-mono);font-size:var(--fs-sm);text-transform:uppercase;letter-spacing:.08em;min-height:48px;padding:0 var(--sp-8);cursor:pointer;border-radius:var(--radius);border:var(--rule) solid var(--claret);background:var(--claret);color:var(--ink-invert);transition:opacity var(--dur) var(--ease)}',
    '.btn:disabled{opacity:.45;cursor:not-allowed}',
    '.btn--ghost{background:transparent;color:var(--claret);border-color:var(--line);min-height:44px}',
    '.btn--link{background:none;border:0;color:var(--claret);font-family:var(--font-mono);font-size:var(--fs-mono-xs);text-transform:uppercase;letter-spacing:.08em;cursor:pointer;padding:.4em .2em;min-height:44px}',
    '.tools{margin-top:var(--sp-12)}',
    '.tools__row{display:flex;gap:var(--sp-6);align-items:center;flex-wrap:wrap;margin-bottom:var(--sp-6)}',
    '.export__meta{font-family:var(--font-mono);font-size:var(--fs-mono-xs);color:var(--ink-faint);text-transform:uppercase;letter-spacing:.08em;margin-bottom:var(--sp-3)}',
    '.budget-banner{border:var(--rule-bold) solid var(--brass);background:var(--brass-wash);border-radius:var(--radius);padding:var(--sp-6);margin:var(--sp-6) 0}',
    '.budget-banner .label{color:var(--brass)}',
    '.privacy{margin-top:var(--sp-12);color:var(--ink-soft);font-size:var(--fs-sm)}',
    '.privacy summary{cursor:pointer;font-family:var(--font-mono);font-size:var(--fs-mono-xs);text-transform:uppercase;letter-spacing:.08em;color:var(--ink-faint)}',
    '.privacy p{margin:var(--sp-3) 0 0;max-width:62ch}',
    /* debrief / scorecard */
    '.debrief{margin-top:var(--sp-12)}',
    '.debrief h2{font-size:var(--fs-xl)}',
    '.tier-group{margin:var(--sp-8) 0}',
    '.tier-group h3{font-size:var(--fs-lg);margin-bottom:var(--sp-3)}',
    '.tier-list{display:flex;flex-direction:column;gap:var(--sp-3);list-style:none;padding:0;margin:0}',
    '.tier-list li{display:flex;gap:var(--sp-3);align-items:flex-start;flex-wrap:wrap}',
    '.axis-b{display:grid;gap:var(--sp-6);grid-template-columns:1fr;margin-top:var(--sp-3)}',
    '.axis-b .rate__head{display:flex;justify-content:space-between;align-items:baseline;gap:var(--sp-3);margin-bottom:var(--sp-2)}',
    '.axis-b .rate__name{font-family:var(--font-display);font-size:var(--fs-md)}',
    '.axis-b .rate__comment{color:var(--ink-soft);font-size:var(--fs-sm);margin:var(--sp-2) 0 0}',
    '.ethics-line{display:flex;align-items:center;gap:var(--sp-3);font-family:var(--font-mono);font-size:var(--fs-md);font-variant-numeric:tabular-nums}',
    '.ethics-line .pos{color:var(--green)}.ethics-line .neg{color:var(--claret)}',
    '.reflect{margin-top:var(--sp-8)}',
    '.reflect textarea{width:100%;min-height:96px;font-family:var(--font-body);font-size:1.1rem;background:var(--paper);border:var(--rule) solid var(--line);border-left:var(--rule-bold) solid var(--green);border-radius:var(--radius);padding:var(--sp-3) var(--sp-6);resize:vertical}',
    'html.type-lg .reflect textarea{font-size:1.32rem}',
    /* scripted-sample replay banner (pinned above the composer) */
    '.sample-banner{position:sticky;bottom:0;z-index:5;margin:var(--sp-8) 0 0;padding:var(--sp-3) var(--sp-6);background:var(--paper-3);border:var(--rule) solid var(--line);border-left:var(--rule-bold) solid var(--claret);border-radius:0 var(--radius) var(--radius) 0;box-shadow:var(--shadow)}',
    '.sample-banner__label{display:flex;align-items:center;gap:var(--sp-3);flex-wrap:wrap;font-family:var(--font-mono);font-size:var(--fs-mono-xs);text-transform:uppercase;letter-spacing:.08em;color:var(--claret)}',
    '.sample-banner__tag{font-style:normal;font-weight:700}',
    '.sample-banner__note{font-family:var(--font-body);font-style:italic;color:var(--ink-soft);font-size:var(--fs-sm);margin:var(--sp-2) 0 var(--sp-3);max-width:62ch}',
    '.sample-banner__controls{display:flex;gap:var(--sp-3);align-items:center;flex-wrap:wrap}',
    '.sample-banner__status{font-family:var(--font-mono);font-size:var(--fs-mono-xs);text-transform:uppercase;letter-spacing:.08em;color:var(--ink-faint);margin-left:auto}',
    '.sr-only{position:absolute;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;clip:rect(0,0,0,0);border:0}'
  ].join('\n');

  function injectStyle() {
    var s = document.createElement('style');
    s.textContent = CSS;
    document.head.appendChild(s);
  }

  /* ---------- suggested openings (rotating) --------------------------------- */
  var OPENINGS = [
    "Thank you for coming in today. Before we dig into the details — how are you holding up with all of this?",
    "I appreciate you making the time. Why don't you start wherever feels natural, and tell me what brought you here.",
    "Let's take this at your pace. In your own words, what happened?",
    "I want to make sure I understand your situation fully. Walk me through it from the beginning.",
    "Everything you tell me here is confidential. So — what's on your mind, and what are you hoping we can do?"
  ];
  var openingIdx = 0;

  /* ============================================================================
     Build the room
     ============================================================================ */
  var refs = {};

  function build() {
    var wrap = el('div', 'chat-wrap');

    /* top bar: brand + large-type toggle */
    var top = el('div', 'chat-topbar');
    top.appendChild(el('span', 'brand', 'SONSTENG · CONSULTATION'));

    /* BYOK: persistent header chip + drawer (byok.js loads before this file) */
    var chipMount = el('span'); chipMount.style.display = 'inline-flex';
    top.appendChild(chipMount);

    var tg = el('div', 'segmented-toggle');
    tg.setAttribute('role', 'group');
    tg.setAttribute('aria-label', 'Type size');
    var bStd = el('button', null, 'STANDARD'); bStd.type = 'button'; bStd.setAttribute('aria-pressed', 'true');
    var bLg = el('button', null, 'LARGE TYPE'); bLg.type = 'button'; bLg.setAttribute('aria-pressed', 'false');
    tg.appendChild(bStd); tg.appendChild(bLg);
    top.appendChild(tg);
    wrap.appendChild(top);
    refs.typeStd = bStd; refs.typeLg = bLg;

    /* case header: running head with turn counter IN the rule */
    var head = el('div', 'chat-head');
    var rh = el('div', 'running-head');
    var slug = (cfg.matter_id ? cfg.matter_id.toUpperCase() + ' · ' : '') + 'CLIENT INTERVIEW';
    rh.appendChild(el('span', null, slug));
    rh.appendChild(el('span', 'rh-spacer'));
    var counter = el('span', 'rh-right turn-counter');
    counter.setAttribute('aria-live', 'polite');
    counter.setAttribute('aria-label', 'Turn counter');
    counter.textContent = 'TURN ' + pad2(0) + ' / ' + pad2(maxTurns);
    rh.appendChild(counter);
    head.appendChild(rh);
    refs.counter = counter;

    var h1 = el('h1', null, cfg.title);
    head.appendChild(h1);
    var who = el('p', 'who');
    who.appendChild(document.createTextNode(cfg.client));
    who.appendChild(el('span', 'role', cfg.role));
    head.appendChild(who);
    wrap.appendChild(head);

    /* BYOK drawer sits under the case header, above the stream */
    var drawerMount = el('div');
    wrap.appendChild(drawerMount);
    if (window.SonstengBYOK) {
      window.SonstengBYOK.attach({ chipMount: chipMount, drawerMount: drawerMount });
    }

    /* stream (aria-live polite) */
    var stream = el('div', 'stream');
    stream.id = 'stream';
    stream.setAttribute('aria-live', 'polite');
    stream.setAttribute('aria-label', 'Interview transcript');
    wrap.appendChild(stream);
    refs.stream = stream;

    /* chambers card — suggested opening (first load) */
    var chambers = el('div', 'chambers card');
    chambers.appendChild(el('span', 'label', 'You may wish to begin'));
    var openP = el('p', 'chambers__open');
    chambers.appendChild(openP);
    var acts = el('div', 'chambers__actions');
    var useBtn = el('button', 'btn btn--ghost', 'Use this opening'); useBtn.type = 'button';
    var anotherBtn = el('button', 'btn--link', 'Suggest another'); anotherBtn.type = 'button';
    acts.appendChild(useBtn); acts.appendChild(anotherBtn);
    chambers.appendChild(acts);
    wrap.appendChild(chambers);
    refs.chambers = chambers; refs.opening = openP;
    setOpening();
    useBtn.addEventListener('click', function () { refs.input.value = refs.opening.textContent; refs.input.focus(); });
    anotherBtn.addEventListener('click', function () { openingIdx = (openingIdx + 1) % OPENINGS.length; setOpening(); });

    /* composer */
    var form = el('form', 'composer'); form.setAttribute('novalidate', '');
    var lab = el('label', 'sr-only', 'Your question to the client'); lab.setAttribute('for', 'composer-input');
    form.appendChild(lab);
    var ta = el('textarea'); ta.id = 'composer-input';
    ta.setAttribute('rows', '3');
    ta.setAttribute('placeholder', 'Speak with your client…');
    ta.setAttribute('aria-label', 'Your question to the client');
    form.appendChild(ta);
    var row = el('div', 'composer__row');
    var hint = el('span', 'composer__hint', 'Enter to send · Shift+Enter for a new line');
    var sendBtn = el('button', 'btn', 'Send'); sendBtn.type = 'submit';
    row.appendChild(hint); row.appendChild(sendBtn);
    form.appendChild(row);
    wrap.appendChild(form);
    refs.form = form; refs.input = ta; refs.sendBtn = sendBtn;

    /* tools: debrief + export + privacy */
    var tools = el('section', 'tools');
    var trow = el('div', 'tools__row');
    var debriefBtn = el('button', 'btn btn--ghost', 'Prepare debrief'); debriefBtn.type = 'button'; debriefBtn.disabled = true;
    debriefBtn.title = 'Available after at least 6 completed turns';
    trow.appendChild(debriefBtn);
    tools.appendChild(trow);
    refs.debriefBtn = debriefBtn;

    /* export card */
    var exp = el('div', 'export card');
    exp.appendChild(el('div', 'export__meta', 'TRANSCRIPT · BROWSER-ONLY'));
    exp.appendChild(el('h3', null, 'Take your transcript with you'));
    exp.appendChild(el('p', null, 'Your conversation lives only in this browser tab. Copy or download it before you close the window.'));
    var erow = el('div', 'tools__row');
    var copyBtn = el('button', 'btn btn--ghost', 'Copy transcript'); copyBtn.type = 'button';
    var dlBtn = el('button', 'btn btn--ghost', 'Download .md'); dlBtn.type = 'button';
    erow.appendChild(copyBtn); erow.appendChild(dlBtn);
    exp.appendChild(erow);
    var copyStatus = el('p', 'export__meta'); copyStatus.setAttribute('aria-live', 'polite'); copyStatus.style.marginTop = 'var(--sp-3)';
    exp.appendChild(copyStatus);
    tools.appendChild(exp);
    refs.copyBtn = copyBtn; refs.dlBtn = dlBtn; refs.copyStatus = copyStatus;

    /* mount point for debrief view */
    var debriefMount = el('div'); debriefMount.id = 'debrief-mount';
    tools.appendChild(debriefMount);
    refs.debriefMount = debriefMount;

    /* privacy note (always visible, expandable) */
    var priv = el('details', 'privacy'); priv.open = false;
    var sum = el('summary', null, 'Privacy & how this works');
    priv.appendChild(sum);
    priv.appendChild(el('p', null, 'Transcripts are never stored on our servers — they live only in this browser tab (they survive a refresh but not closing the tab). Your conversation is processed through Anthropic’s API to generate the client’s replies, subject to Anthropic’s data-retention policy. Our server logs metadata only (salted-hashed IP, token counts, timestamps) — never message content and never the URL.'));
    tools.appendChild(priv);

    wrap.appendChild(tools);
    ROOT.appendChild(wrap);

    /* events */
    form.addEventListener('submit', function (e) { e.preventDefault(); submit(); });
    ta.addEventListener('keydown', function (e) {
      if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); submit(); }
    });
    bStd.addEventListener('click', function () { setTypeLg(false); });
    bLg.addEventListener('click', function () { setTypeLg(true); });
    debriefBtn.addEventListener('click', doDebrief);
    copyBtn.addEventListener('click', function () { copyTranscript(); });
    dlBtn.addEventListener('click', function () { downloadTranscript(); });
  }

  function setOpening() { refs.opening.textContent = '“' + OPENINGS[openingIdx] + '”'; }

  /* ---------- large-type toggle (persisted) -------------------------------- */
  function setTypeLg(on) {
    document.documentElement.classList.toggle('type-lg', on);
    refs.typeLg.setAttribute('aria-pressed', on ? 'true' : 'false');
    refs.typeStd.setAttribute('aria-pressed', on ? 'false' : 'true');
    LS.set('sonsteng_type_lg', on ? '1' : '0');
  }

  /* ============================================================================
     Rendering
     ============================================================================ */
  function appendMessage(role, text) {
    var isLawyer = role === 'user';
    var m = el('div', 'msg ' + (isLawyer ? 'msg--lawyer' : 'msg--client'));
    m.appendChild(el('div', 'msg__who', isLawyer ? 'You · Counsel' : cfg.client));
    m.appendChild(el('div', 'msg__body', text));
    refs.stream.appendChild(m);
    scrollToEnd();
    return m;
  }

  function stageDirection(text) {
    var d = el('div', 'stage-direction', text);
    refs.stream.appendChild(d);
    scrollToEnd();
    return d;
  }

  function showConsidering() {
    if (refs.considering) return;
    var row = el('div', 'considering-row');
    row.appendChild(el('span', 'considering'));
    row.appendChild(el('span', null, cfg.client + ' is considering'));
    row.setAttribute('aria-label', cfg.client + ' is considering a response');
    refs.stream.appendChild(row);
    refs.considering = row;
    scrollToEnd();
  }
  function hideConsidering() { if (refs.considering) { refs.considering.remove(); refs.considering = null; } }

  function scrollToEnd() {
    try { refs.stream.lastElementChild && refs.stream.lastElementChild.scrollIntoView({ block: 'end', behavior: 'smooth' }); } catch (e) {}
  }

  function updateCounter(turn) {
    refs.counter.textContent = 'TURN ' + pad2(turn) + ' / ' + pad2(maxTurns);
  }

  function renderRule42() {
    if (rule42Shown) return;
    rule42Shown = true;
    var f = el('div', 'ethics-flag');
    f.setAttribute('role', 'status');
    f.appendChild(el('p', 'ethics-flag__head', 'PROFESSIONAL RESPONSIBILITY — RULE 4.2 · NO CONTACT'));
    f.appendChild(el('p', 'ethics-flag__note', 'This person is represented by counsel in this matter. Rule 4.2 forbids communicating about the subject of the representation with a represented party without their lawyer’s consent. Route contact through opposing counsel, not the party.'));
    f.appendChild(el('span', 'ethics-flag__tag', 'LOGGED TO DEBRIEF'));
    refs.stream.appendChild(f);
    scrollToEnd();
  }

  /* ============================================================================
     Input enable/disable (synchronous, before any await)
     ============================================================================ */
  function disableInput() { refs.input.disabled = true; refs.sendBtn.disabled = true; }
  function enableInput() { refs.input.disabled = false; refs.sendBtn.disabled = false; }
  function setState(next) { state = next; }

  function refreshDebriefBtn() {
    var n = committed().length;
    refs.debriefBtn.disabled = !(n >= 6) || state === S.SENDING || state === S.RETRYING;
    refs.debriefBtn.textContent = 'Prepare debrief' + (n < 6 ? ' (' + n + '/6 turns)' : '');
  }

  /* ============================================================================
     Two-phase storage writes
     ============================================================================ */
  function writePending(turn_id, userText) {
    // A resend of a recovered draft reuses its turn_id (idempotency). The matching
    // 'unresolved' record still lives in turns[]; reuse it in place rather than
    // pushing a duplicate. Otherwise two records share a turn_id and commitTurn()
    // (which resolves only the FIRST match) would orphan the other as a permanent
    // 'pending' phantom that re-triggers "message may not have been delivered"
    // recovery on every reload.
    var rec = findTurn(turn_id);
    if (rec) {
      rec.status = 'pending'; rec.user = userText;
      rec.assistant = null; rec.turn = null; rec.remaining = null; rec.state = null;
    } else {
      turns.push({ turn_id: turn_id, status: 'pending', user: userText, assistant: null, turn: null, remaining: null, state: null });
    }
    saveTurns();
  }
  function commitTurn(turn_id, userText, result) {
    var rec = findTurn(turn_id);
    if (!rec) { rec = { turn_id: turn_id }; turns.push(rec); }
    rec.status = 'committed';
    rec.user = userText;
    rec.assistant = result.reply;
    rec.turn = result.turn;
    rec.remaining = result.remaining;
    rec.state = result.state;
    saveTurns();
  }
  function markUnresolved(turn_id) {
    var rec = findTurn(turn_id);
    if (rec && rec.status !== 'committed') { rec.status = 'unresolved'; saveTurns(); }
  }
  function dropTurn(turn_id) {
    turns = turns.filter(function (t) { return t.turn_id !== turn_id; });
    saveTurns();
  }
  function findTurn(turn_id) { for (var i = 0; i < turns.length; i++) if (turns[i].turn_id === turn_id) return turns[i]; return null; }

  function buildMessages(newUserText) {
    var msgs = [];
    committed().forEach(function (t) {
      msgs.push({ role: 'user', content: t.user });
      msgs.push({ role: 'assistant', content: t.assistant });
    });
    msgs.push({ role: 'user', content: newUserText });
    return msgs;
  }

  /* ============================================================================
     The send flow — the core race-proof path
     ============================================================================ */
  function submit() {
    if (cfg.sample) return;                        // replay demo: the composer never sends
    if (state !== S.IDLE) return;                 // a turn may only begin from IDLE
    var text = (refs.input.value || '').trim();
    if (!text) return;
    if (!session) { stageDirection('The line isn’t connected yet — one moment.'); return; }
    send(text);
  }

  function send(text) {
    // synchronous critical section BEFORE any await
    setState(S.SENDING);
    disableInput();
    refreshDebriefBtn();
    if (refs.chambers) { refs.chambers.remove(); refs.chambers = null; }

    var turn_id = draftTurnId || uuid();
    draftTurnId = null;
    var userNode = appendMessage('user', text);
    writePending(turn_id, text);
    refs.input.value = '';
    showConsidering();

    var body = {
      session_token: session.session_token,
      matter_id: cfg.matter_id,
      persona_id: cfg.persona_id,
      turn_id: turn_id,
      messages: buildMessages(text)
    };
    var byok = window.SonstengBYOK && window.SonstengBYOK.get();
    if (byok) body.byok = byok;   // {provider, api_key, model?} — never logged/rendered

    runWithRetry(body).then(function (out) {
      hideConsidering();
      if (out.ok) {
        var r = out.data;
        commitTurn(turn_id, text, r);
        appendMessage('assistant', r.reply);
        updateCounter(r.turn);
        if (cfg.represented) renderRule42();
        if (r.state === 'warning') stageDirection('[' + cfg.client + ' glances at their watch. Time is getting short.]');
        if (r.state === 'ended') {
          setState(S.ENDED);
          stageDirection('[' + cfg.client + ' gathers their coat. “I think that’s all I can do today — thank you, counselor.” This is a good moment to save your transcript and prepare your debrief.]');
          disableInput();
        } else {
          setState(S.IDLE);
          enableInput();
        }
      } else {
        handleError(out, turn_id, text, userNode);
      }
      refreshDebriefBtn();
    });
  }

  // one sequential retry, same turn_id, only on network throw or upstream_unavailable
  function runWithRetry(body) {
    function attempt(n) {
      return api('/v1/chat', { body: body }).then(function (out) {
        if (out.ok) return out;
        var code = out.data && out.data.error && out.data.error.code;
        if (code === 'upstream_unavailable' && n === 0) { setState(S.RETRYING); return attempt(1); }
        return out; // terminal, non-ok
      }, function (err) {
        if (n === 0) { setState(S.RETRYING); return attempt(1); }
        return { ok: false, status: 0, data: { error: { code: 'network', message: (err && err.message) || 'network error' } }, network: true };
      });
    }
    return attempt(0);
  }

  function handleError(out, turn_id, text, userNode) {
    var e = (out.data && out.data.error) || { code: 'unknown' };
    var code = e.code || 'unknown';

    // recoverable — the turn did NOT commit; let the user resend on the SAME turn_id
    function recoverable(msg) {
      if (userNode && userNode.parentNode) userNode.remove();   // remove the un-sent bubble
      markUnresolved(turn_id);
      refs.input.value = text;                                   // restore draft
      draftTurnId = turn_id;                                     // resend reuses turn_id (idempotent)
      stageDirection(msg);
      setState(S.IDLE);
      enableInput();
      refs.input.focus();
    }

    // provider-side auth failures can surface as validation_error/upstream_unavailable
    var authish = /\b401\b|unauthoriz|authentication|invalid[_\s-]*(api[_\s-]*)?key|api[_\s-]*key[^.]*invalid|credential/i
      .test(e.message || '');

    if (code === 'no_hosted_key') {
      recoverable(e.in_character || '[The receptionist looks up apologetically. “This office doesn’t keep a house key — you’ll need to bring your own.”]');
      if (window.SonstengBYOK) {
        window.SonstengBYOK.open('This deployment has no house key — bring your own to sit down with the client.');
      }
      return;
    }
    if (authish && (code === 'validation_error' || code === 'upstream_unavailable')) {
      recoverable('Your key was declined by the provider — check it in ADD YOUR KEY, then send again.');
      if (window.SonstengBYOK) {
        window.SonstengBYOK.open('Your key was declined — double-check the provider, the key itself, and any model override.');
      }
      return;
    }
    if (code === 'network' || code === 'upstream_unavailable') {
      recoverable('[The phone line crackles — that didn’t get through. Try sending again.]');
      return;
    }
    if (code === 'rate_limited') {
      recoverable(e.in_character || '[The connection is busy. Give it a moment, then try again.]');
      return;
    }
    if (code === 'cap_exceeded') {
      if (userNode && userNode.parentNode) userNode.remove();
      dropTurn(turn_id);
      if (e.in_character) stageDirection(e.in_character);
      showBudgetBanner();
      setState(S.CAPPED);
      disableInput();
      return;
    }
    if (code === 'turn_limit') {
      if (userNode && userNode.parentNode) userNode.remove();
      dropTurn(turn_id);
      stageDirection(e.in_character || '[' + cfg.client + ' has to go. “We’re out of time — thank you, counselor.”]');
      setState(S.ENDED);
      disableInput();
      return;
    }
    if (code === 'validation_error') {
      if (userNode && userNode.parentNode) userNode.remove();
      dropTurn(turn_id);
      stageDirection('That message couldn’t be sent as written. Please shorten or rephrase it and try again.');
      setState(S.IDLE);
      enableInput();
      return;
    }
    if (code === 'origin_forbidden' || code === 'session_invalid') {
      if (userNode && userNode.parentNode) userNode.remove();
      dropTurn(turn_id);
      plainNotice(code === 'session_invalid'
        ? 'Your interview session has expired or isn’t valid anymore. Reload this page to start a fresh session — your existing transcript stays in this tab until then.'
        : 'This interview can’t be reached from this address. If you’re running your own Worker, check its allowed-origins list.');
      setState(S.ENDED);
      disableInput();
      return;
    }
    // unknown -> treat as recoverable network-style
    recoverable('[Something interrupted the line. Try sending again.]');
  }

  function plainNotice(text) {
    var d = el('div', 'budget-banner');
    d.setAttribute('role', 'status');
    d.appendChild(el('span', 'label', 'NOTICE'));
    d.appendChild(el('p', null, text));
    d.querySelector('p').style.margin = 'var(--sp-2) 0 0';
    refs.stream.appendChild(d);
    scrollToEnd();
  }

  function showBudgetBanner() {
    var d = el('div', 'budget-banner');
    d.setAttribute('role', 'status');
    d.appendChild(el('span', 'label', 'DEMO BUDGET'));
    var p = el('p', null, 'Demo budget for today is spent — come back tomorrow or bring your own key.');
    p.style.margin = 'var(--sp-2) 0 0';
    d.appendChild(p);
    refs.stream.appendChild(d);
    scrollToEnd();
  }

  /* ============================================================================
     Transcript export (Copy w/ execCommand fallback + Download .md)
     ============================================================================ */
  function transcriptMarkdown() {
    var lines = [];
    lines.push('# Client Interview — ' + cfg.title);
    if (cfg.matter_id) lines.push('_Matter ' + cfg.matter_id + ' · ' + cfg.client + '_');
    lines.push('');
    committed().forEach(function (t) {
      lines.push('**Counsel:** ' + t.user);
      lines.push('');
      lines.push('**' + cfg.client + ':** ' + t.assistant);
      lines.push('');
    });
    if (refs.reflect && refs.reflect.value.trim()) {
      lines.push('---');
      lines.push('## Self-reflection');
      lines.push(refs.reflect.value.trim());
      lines.push('');
    }
    return lines.join('\n');
  }

  function copyTranscript() {
    var txt = transcriptMarkdown();
    var done = function (ok) { refs.copyStatus.textContent = ok ? 'Copied to clipboard.' : 'Copy failed — select the download instead.'; };
    // execCommand fallback FIRST-class (navigator.clipboard often blocked in iframes/artifacts)
    var ok = false;
    try {
      var ta = document.createElement('textarea');
      ta.value = txt;
      ta.setAttribute('readonly', '');
      ta.style.position = 'fixed'; ta.style.left = '-9999px'; ta.style.top = '0';
      document.body.appendChild(ta);
      ta.select(); ta.setSelectionRange(0, txt.length);
      ok = document.execCommand('copy');
      document.body.removeChild(ta);
    } catch (e) { ok = false; }
    if (ok) { done(true); return; }
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(txt).then(function () { done(true); }, function () { done(false); });
    } else { done(false); }
  }

  function downloadTranscript() {
    var txt = transcriptMarkdown();
    var blob = new Blob([txt], { type: 'text/markdown' });
    var url = URL.createObjectURL(blob);
    var a = document.createElement('a');
    a.href = url;
    a.download = (cfg.matter_id || 'interview') + '-transcript.md';
    document.body.appendChild(a); a.click(); document.body.removeChild(a);
    setTimeout(function () { URL.revokeObjectURL(url); }, 2000);
    refs.copyStatus.textContent = 'Downloaded.';
  }

  /* ============================================================================
     Debrief view (graded return)
     ============================================================================ */
  function doDebrief() {
    if (committed().length < 6) return;
    refs.debriefBtn.disabled = true;
    refs.debriefBtn.textContent = 'Preparing debrief…';
    var body = {
      session_token: session ? session.session_token : '',
      matter_id: cfg.matter_id,
      persona_id: cfg.persona_id,
      transcript: buildTranscript()
    };
    var byok = window.SonstengBYOK && window.SonstengBYOK.get();
    if (byok) body.byok = byok;
    api('/v1/debrief', { body: body }).then(function (out) {
      refs.debriefBtn.textContent = 'Prepare debrief';
      refs.debriefBtn.disabled = false;
      if (out.ok && out.data && out.data.scorecard) {
        renderDebrief(out.data.scorecard);
      } else {
        var e = (out.data && out.data.error) || {};
        if (e.code === 'no_hosted_key' && window.SonstengBYOK) {
          window.SonstengBYOK.open('This deployment has no house key — bring your own to prepare the debrief.');
          plainNotice('Add your own API key (ADD YOUR KEY, top of the page), then request the debrief again.');
        } else {
          plainNotice(e.message || 'The debrief couldn’t be prepared just now. Please try again in a moment.');
        }
      }
    });
  }

  function buildTranscript() {
    var t = [];
    committed().forEach(function (c) {
      t.push({ role: 'user', content: c.user });
      t.push({ role: 'assistant', content: c.assistant });
    });
    return t;
  }

  var TRIGGER_LABELS = {
    open_ended_invitation: 'an open-ended invitation',
    wellbeing_question: 'a question about their wellbeing',
    acknowledged_emotion: 'acknowledging their emotion',
    no_interruption_streak: 'letting them speak without interrupting',
    confidentiality_reassurance: 'reassuring them of confidentiality',
    nonjudgmental_response: 'a non-judgmental response',
    follow_up_on_hint: 'following up on a hint they dropped',
    explained_process: 'explaining the process to them'
  };
  var AXIS_B_LABELS = {
    rapport_opening: 'Rapport & opening',
    listening_t_funnel: 'Listening (broad before narrow)',
    understanding_goals: 'Understanding my goals',
    explanation_next_steps: 'Explanation & next steps',
    overall_confidence: 'Would I come back?'
  };

  function renderDebrief(sc) {
    var mount = refs.debriefMount;
    mount.textContent = '';
    var d = el('section', 'debrief card');
    d.setAttribute('aria-label', 'Interview debrief');

    var rh = el('div', 'running-head');
    rh.appendChild(el('span', null, 'GRADED RETURN · DEBRIEF'));
    rh.appendChild(el('span', 'rh-spacer'));
    rh.appendChild(el('span', 'rh-right', (sc.matter_id || cfg.matter_id || '').toUpperCase()));
    d.appendChild(rh);

    d.appendChild(el('h2', null, 'Your interview, reviewed'));
    if (sc.narrative) d.appendChild(el('p', 'prose', sc.narrative));

    /* Axis A — tiers */
    var a = sc.axis_a || {};
    // Elicited (green)
    var g1 = el('div', 'tier-group');
    g1.appendChild(el('h3', null, 'Elicited — what you drew out'));
    var l1 = el('ul', 'tier-list');
    (a.facts_elicited || []).forEach(function (f) {
      var li = el('li');
      var c = el('span', 'chip chip--tier-volunteered');
      c.appendChild(document.createTextNode('✓ ' + f));
      li.appendChild(c);
      l1.appendChild(li);
    });
    if (!(a.facts_elicited || []).length) l1.appendChild(el('li', null, 'No facts were tagged as elicited in this pass.'));
    g1.appendChild(l1);
    d.appendChild(g1);

    // Askable, never asked (brass)
    var g2 = el('div', 'tier-group');
    g2.appendChild(el('h3', null, 'Askable — there for the asking, but you never asked'));
    var l2 = el('ul', 'tier-list');
    (a.revealed_if_asked_missed || []).forEach(function (topic) {
      var li = el('li');
      var c = el('span', 'chip chip--tier-revealed');
      c.appendChild(document.createTextNode(topic));
      li.appendChild(c);
      l2.appendChild(li);
    });
    if (!(a.revealed_if_asked_missed || []).length) l2.appendChild(el('li', null, 'You covered the askable topics — well done.'));
    g2.appendChild(l2);
    d.appendChild(g2);

    // Rapport-gated, never earned (claret, with needed trigger in mono)
    var g3 = el('div', 'tier-group');
    g3.appendChild(el('h3', null, 'Rapport-gated — trust you hadn’t yet earned'));
    var l3 = el('ul', 'tier-list');
    (a.rapport_gated_unearned || []).forEach(function (r) {
      var li = el('li');
      var c = el('span', 'chip chip--tier-rapport');
      c.appendChild(document.createTextNode(r.topic));
      li.appendChild(c);
      var need = el('span', 'chip chip--folio');
      need.appendChild(document.createTextNode('NEEDED: ' + (TRIGGER_LABELS[r.trigger_needed] || r.trigger_needed)));
      li.appendChild(need);
      l3.appendChild(li);
    });
    if (!(a.rapport_gated_unearned || []).length) l3.appendChild(el('li', null, 'You earned the trust these disclosures required.'));
    g3.appendChild(l3);
    d.appendChild(g3);

    // Rule 4.2 flags
    if ((a.rule_4_2_flags || []).length) {
      var ef = el('div', 'ethics-flag'); ef.setAttribute('role', 'status');
      ef.appendChild(el('p', 'ethics-flag__head', 'RULE 4.2 · NO CONTACT — FLAGGED'));
      (a.rule_4_2_flags || []).forEach(function (fl) { ef.appendChild(el('p', 'ethics-flag__note', fl)); });
      ef.appendChild(el('span', 'ethics-flag__tag', 'AFFECTS ETHICS SCORE'));
      d.appendChild(ef);
    }

    /* Axis B — relational meters */
    var b = sc.axis_b || {};
    var bg = el('div', 'tier-group');
    bg.appendChild(el('h3', null, 'How the client experienced you'));
    var grid = el('div', 'axis-b');
    Object.keys(AXIS_B_LABELS).forEach(function (key) {
      var rate = b[key]; if (!rate) return;
      var block = el('div');
      var hd = el('div', 'rate__head');
      hd.appendChild(el('span', 'rate__name', AXIS_B_LABELS[key]));
      hd.appendChild(el('span', 'meter__label', pad2(rate.score) + ' / 10'));
      block.appendChild(hd);
      var pct = Math.max(0, Math.min(100, (rate.score / 10) * 100));
      var cls = rate.score >= 7 ? 'meter meter--ok' : (rate.score >= 4 ? 'meter meter--warn' : 'meter meter--stop');
      var meter = el('div', cls);
      var fill = el('span', 'meter__fill');
      fill.style.setProperty('--meter-value', pct + '%');
      meter.appendChild(fill);
      block.appendChild(meter);
      if (rate.comment) block.appendChild(el('p', 'rate__comment', rate.comment));
      grid.appendChild(block);
    });
    bg.appendChild(grid);
    d.appendChild(bg);

    /* signed ethics score */
    var eg = el('div', 'tier-group');
    eg.appendChild(el('h3', null, 'Professional responsibility'));
    var eline = el('div', 'ethics-line');
    var val = Number(sc.ethics_score);
    var span = el('span', val >= 0 ? 'pos' : 'neg', (val > 0 ? '+' : '') + val + ' / +2');
    eline.appendChild(el('span', 'meter__label', 'ETHICS'));
    eline.appendChild(span);
    eg.appendChild(eline);
    d.appendChild(eg);

    /* self-reflection prompt + textarea (joins export) */
    if (sc.self_reflection_prompt) {
      var rf = el('div', 'reflect');
      rf.appendChild(el('h3', null, 'For your own reflection'));
      rf.appendChild(el('p', 'prose', sc.self_reflection_prompt));
      var lab = el('label', 'sr-only', 'Your reflection'); lab.setAttribute('for', 'reflect-input');
      rf.appendChild(lab);
      var rta = el('textarea'); rta.id = 'reflect-input';
      rta.setAttribute('placeholder', 'Write your reflection here — it will be included when you copy or download your transcript.');
      rf.appendChild(rta);
      refs.reflect = rta;
      d.appendChild(rf);
    }

    mount.appendChild(d);
    try { d.scrollIntoView({ block: 'start', behavior: 'smooth' }); } catch (e) {}
  }

  /* ============================================================================
     Boot: mint session or reconcile, rehydrate transcript
     ============================================================================ */
  function rehydrate() {
    committed().forEach(function (t) {
      appendMessage('user', t.user);
      appendMessage('assistant', t.assistant);
    });
    var last = committed()[committed().length - 1];
    if (last && last.turn) updateCounter(last.turn);
    if (committed().length && refs.chambers) { refs.chambers.remove(); refs.chambers = null; }
    // reconcile unresolved / pending: pull the text back as a recoverable draft
    var stuck = null;
    turns.forEach(function (t) { if (t.status === 'pending' || t.status === 'unresolved') stuck = t; });
    if (stuck) {
      refs.input.value = stuck.user;
      draftTurnId = stuck.turn_id;
      // keep the record but as unresolved; it is NOT in the committed transcript, so no double-count
      markUnresolved(stuck.turn_id);
      stageDirection('[Your last message may not have been delivered before the page reloaded. It’s back in the box — send it again when you’re ready.]');
    }
    refreshDebriefBtn();
  }

  function mintSession() {
    var path = '/v1/session';
    // bypass token forwarded here ONLY, and only via query — never stored, logged, or rendered
    if (cfg.bypass) path += '?bypass=' + encodeURIComponent(cfg.bypass);
    return api(path, { method: 'GET' }).then(function (out) {
      if (out.ok && out.data && out.data.session_token) {
        session = out.data;
        if (out.data.max_turns) { maxTurns = out.data.max_turns; }
        saveSession();
        updateCounter((committed()[committed().length - 1] || {}).turn || 0);
      } else {
        var e = (out.data && out.data.error) || {};
        stageDirection(e.message || 'Couldn’t open a session with the interview server. You can still read your existing transcript; reload to retry the connection.');
      }
    }, function () {
      stageDirection('The interview server didn’t answer. Check your connection or the API address, then reload.');
    });
  }

  /* ============================================================================
     Scripted-sample replay (?sample=1) — no API, no key, no session.
     Reuses the live renderers (appendMessage / showConsidering / stageDirection /
     updateCounter / commitTurn / renderDebrief / transcript export) so the demo is
     the real consultation room, only driven from a hand-authored recording.
     ============================================================================ */
  var reduceMotion = false;
  try { reduceMotion = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches; } catch (e) {}
  var sample = null;                     // {turns:[{counsel,client,stage?}], scorecard}
  var play = { i: 0, playing: false, done: false, timer: null, pending: null };
  var sampleRefs = {};

  function setupSample() {
    // Strip live-only affordances; the composer is a labeled dead-end in a replay.
    if (refs.chambers) { refs.chambers.remove(); refs.chambers = null; }
    disableInput();
    refs.input.setAttribute('placeholder', 'This is a replay — add a key to interview the client yourself');
    refs.input.value = '';
    // the live "Prepare debrief" button would call the API — the replay renders its own
    if (refs.debriefBtn) refs.debriefBtn.style.display = 'none';

    buildSampleBanner();

    // fetch the recording (same-origin static file next to index.html)
    var url = 'sample-' + encodeURIComponent(cfg.matter_id || 'm05') + '.json';
    fetch(url, { cache: 'no-store', credentials: 'omit' }).then(function (res) {
      if (!res.ok) throw new Error('http ' + res.status);
      return res.json();
    }).then(function (data) {
      sample = data;
      maxTurns = (data.turns && data.turns.length) || maxTurns;
      updateCounter(0);
      sampleRefs.status.textContent = 'READY · ' + pad2(sample.turns.length) + ' TURNS';
      sampleRefs.playBtn.disabled = false;
    }).catch(function () {
      sampleRefs.status.textContent = 'UNAVAILABLE';
      stageDirection('This sample recording could not be loaded. Reload the page to try again.');
    });
  }

  function buildSampleBanner() {
    var b = el('div', 'sample-banner');
    b.setAttribute('role', 'region');
    b.setAttribute('aria-label', 'Scripted sample controls');

    var label = el('div', 'sample-banner__label');
    label.appendChild(el('span', 'sample-banner__tag', 'SCRIPTED SAMPLE'));
    label.appendChild(el('span', null, 'a recorded consultation, not a live AI client'));
    b.appendChild(label);
    b.appendChild(el('p', 'sample-banner__note',
      'This is a fixed, hand-authored recording of a client interview, played back for you. Nothing here is generated live. To interview the client yourself, add your own API key.'));

    var ctr = el('div', 'sample-banner__controls');
    var playBtn = el('button', 'btn', 'Play sample ▸'); playBtn.type = 'button'; playBtn.disabled = true;
    var skipBtn = el('button', 'btn btn--ghost', 'Skip to debrief'); skipBtn.type = 'button'; skipBtn.disabled = true;
    var status = el('span', 'sample-banner__status'); status.setAttribute('aria-live', 'polite'); status.textContent = 'LOADING…';
    ctr.appendChild(playBtn); ctr.appendChild(skipBtn); ctr.appendChild(status);
    b.appendChild(ctr);

    // pin the banner directly above the composer
    refs.form.parentNode.insertBefore(b, refs.form);
    sampleRefs = { banner: b, playBtn: playBtn, skipBtn: skipBtn, status: status };

    playBtn.addEventListener('click', function () { play.playing ? pauseSample() : playSample(); });
    skipBtn.addEventListener('click', skipToDebrief);
  }

  function sampleStatus(word) {
    sampleRefs.status.textContent = word + ' · ' + pad2(play.i) + ' / ' + pad2(sample.turns.length);
  }

  function playSample() {
    if (!sample || play.done) return;
    play.playing = true;
    sampleRefs.playBtn.textContent = 'Pause';
    sampleRefs.skipBtn.disabled = false;
    sampleStatus('PLAYING');
    stepSample();
  }

  function pauseSample() {
    play.playing = false;
    if (play.timer) { clearTimeout(play.timer); play.timer = null; }
    hideConsidering();   // the pending turn's counsel bubble stays; its client half resumes later
    sampleRefs.playBtn.textContent = 'Play sample ▸';
    sampleStatus('PAUSED');
  }

  // A turn plays in two halves: startTurn() shows the counsel line + "considering";
  // finishTurn() reveals the client's reply and advances. play.pending holds the
  // in-flight turn between the halves so pause/resume and skip never re-append it.
  function stepSample() {
    if (!play.playing) return;
    if (play.pending) { finishTurn(); return; }          // resumed mid-considering
    if (play.i >= sample.turns.length) { finishSample(); return; }
    var t = sample.turns[play.i];
    appendMessage('user', t.counsel);
    play.pending = t;
    if (reduceMotion) { finishTurn(); }
    else { showConsidering(); play.timer = setTimeout(finishTurn, 1200); }
  }

  function finishTurn() {
    if (!play.playing) return;
    hideConsidering();
    renderClientTurn(play.pending);
    play.pending = null;
    play.i += 1;
    sampleStatus('PLAYING');
    play.timer = setTimeout(stepSample, reduceMotion ? 0 : 700);
  }

  function renderClientTurn(t) {
    if (t.stage) stageDirection(t.stage);
    appendMessage('assistant', t.client);
    var idx = play.i + 1;
    commitTurn(uuid(), t.counsel, { reply: t.client, turn: idx, remaining: sample.turns.length - idx, state: 'ok' });
    updateCounter(idx);
  }

  function skipToDebrief() {
    if (!sample) return;
    if (play.timer) { clearTimeout(play.timer); play.timer = null; }
    play.playing = false;
    hideConsidering();
    // complete any in-flight turn (counsel already on screen) without re-appending it
    if (play.pending) { renderClientTurn(play.pending); play.pending = null; play.i += 1; }
    while (play.i < sample.turns.length) {
      var t = sample.turns[play.i];
      appendMessage('user', t.counsel);
      renderClientTurn(t);
      play.i += 1;
    }
    finishSample();
  }

  function finishSample() {
    play.playing = false;
    play.done = true;
    play.pending = null;
    if (play.timer) { clearTimeout(play.timer); play.timer = null; }
    hideConsidering();
    sampleRefs.playBtn.disabled = true;
    sampleRefs.playBtn.textContent = 'Replay finished';
    sampleRefs.skipBtn.disabled = true;
    sampleRefs.status.textContent = 'COMPLETE · ' + pad2(sample.turns.length) + ' / ' + pad2(sample.turns.length);
    stageDirection('[End of the scripted sample. Below is the debrief this interview would earn — add your own key to conduct one yourself.]');
    if (sample.scorecard) renderDebrief(sample.scorecard);
  }

  function boot() {
    injectStyle();
    // restore prefs
    if (LS.get('sonsteng_type_lg') === '1') document.documentElement.classList.add('type-lg');
    build();
    if (LS.get('sonsteng_type_lg') === '1') { refs.typeLg.setAttribute('aria-pressed', 'true'); refs.typeStd.setAttribute('aria-pressed', 'false'); }

    if (cfg.sample) { setupSample(); return; }   // replay demo: no session, no transport

    // restore session + transcript for this tab
    session = loadSession();
    turns = loadTurns();
    if (session && session.max_turns) maxTurns = session.max_turns;
    rehydrate();

    // always (re)mint if no session token in this tab
    if (!session || !session.session_token) {
      mintSession();
    }

    // iPad Safari bfcache: a frozen SENDING state bricks input — reset on restore
    window.addEventListener('pageshow', function (e) {
      if (e.persisted) {
        hideConsidering();
        // any in-flight turn is now uncertain
        turns.forEach(function (t) { if (t.status === 'pending') { t.status = 'unresolved'; } });
        saveTurns();
        if (state === S.SENDING || state === S.RETRYING) { setState(S.IDLE); enableInput(); }
        // pull a stuck draft back if present
        var stuck = null;
        turns.forEach(function (t) { if (t.status === 'unresolved') stuck = t; });
        if (stuck && !refs.input.value) { refs.input.value = stuck.user; draftTurnId = stuck.turn_id; }
        refreshDebriefBtn();
      }
    });
  }

  /* expose a tiny surface for the dev harness (test.html) */
  window.SonstengChat = {
    submit: submit,
    send: function (text) { refs.input.value = text; submit(); },
    getState: function () { return state; },
    getTurns: function () { return committed().length; },
    refs: function () { return refs; }
  };

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot);
  else boot();
})();
