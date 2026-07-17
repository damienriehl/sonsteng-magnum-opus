/* ============================================================================
   Sonsteng Practicum — critique.js
   The galley-proof deliverable critique. Vanilla JS, zero dependencies.

   Design contract: docs/research/design-direction.md §7.
   Scorecard schema: data/schemas/critique.scorecard.schema.json.
   API contract v1: POST /v1/critique {session_token, matter_id, deliverable_text}
                    -> {scorecard}; non-200 -> {error:{code,message,in_character?}}.

   All dynamic content rendered via createElement + textContent (never innerHTML).
   ============================================================================ */
(function () {
  'use strict';

  var ROOT = document.getElementById('critique-root');
  if (!ROOT) return;

  var CAP = 18000; // client-side char cap; the Worker enforces its own limit too.

  var Q = new URLSearchParams(location.search);
  function meta(n) { var m = document.querySelector('meta[name="' + n + '"]'); return m ? m.content : ''; }
  var cfg = {
    matter_id: Q.get('matter') || '',
    title: Q.get('title') || 'Deliverable Critique',
    apiParam: Q.get('api') || '',
    bypass: Q.get('bypass') || ''
  };

  function apiBase() {
    if (cfg.apiParam) return cfg.apiParam.replace(/\/+$/, '');
    var ls = null; try { ls = localStorage.getItem('sonsteng_api'); } catch (e) {}
    if (ls) return ls.replace(/\/+$/, '');
    var mt = meta('sonsteng-api'); if (mt) return mt.replace(/\/+$/, '');
    return '/api';
  }
  if (cfg.apiParam) { try { localStorage.setItem('sonsteng_api', cfg.apiParam.replace(/\/+$/, '')); } catch (e) {} }

  /* per-tab session token (shared with chat), probe-write storage */
  function ss(k) { try { return sessionStorage.getItem(k); } catch (e) { return null; } }
  function sessionToken() {
    try { var s = JSON.parse(ss('sonsteng_sess') || 'null'); return s && s.session_token ? s.session_token : ''; } catch (e) { return ''; }
  }

  function api(path, opts) {
    opts = opts || {};
    var mock = window.__SONSTENG_MOCK__;
    if (typeof mock === 'function') return Promise.resolve(mock({ path: path, method: opts.method || 'POST', body: opts.body || null }));
    var fo = { method: opts.method || 'POST', headers: {}, cache: 'no-store', credentials: 'omit' };
    if (opts.body) { fo.headers['content-type'] = 'application/json'; fo.body = JSON.stringify(opts.body); }
    return fetch(apiBase() + path, fo).then(function (res) {
      return res.json().catch(function () { return null; }).then(function (data) { return { ok: res.ok, status: res.status, data: data }; });
    });
  }

  function el(tag, cls, text) { var n = document.createElement(tag); if (cls) n.className = cls; if (text != null) n.textContent = text; return n; }
  function fmtPts(n) { n = Number(n); return (Math.round(n * 10) / 10).toString(); }

  var CSS = [
    '.crit-wrap{max-width:var(--maxw);margin:0 auto;padding:var(--sp-6) var(--gutter) var(--sp-16)}',
    '.crit-head h1{font-size:var(--fs-2xl);margin:var(--sp-3) 0 var(--sp-2)}',
    '.crit-intro{max-width:62ch;color:var(--ink-soft)}',
    '.paste-warn{border:var(--rule-bold) solid var(--claret);background:var(--claret-wash);border-radius:var(--radius);padding:var(--sp-3) var(--sp-6);margin:var(--sp-6) 0;max-width:62ch}',
    '.paste-warn .label{color:var(--claret);display:block;margin-bottom:var(--sp-2)}',
    '.paste-warn p{margin:0}',
    '.paste{display:flex;flex-direction:column;gap:var(--sp-3);margin:var(--sp-6) 0}',
    '.paste textarea{width:100%;min-height:240px;font-family:var(--font-body);font-size:1.1rem;line-height:1.55;color:var(--ink);background:var(--paper);border:var(--rule) solid var(--line);border-left:var(--rule-bold) solid var(--brass);border-radius:var(--radius);padding:var(--sp-6);resize:vertical}',
    '.paste__row{display:flex;justify-content:space-between;align-items:center;gap:var(--sp-3);flex-wrap:wrap}',
    '.paste__count{font-family:var(--font-mono);font-size:var(--fs-mono-xs);color:var(--ink-faint);text-transform:uppercase;letter-spacing:.08em;font-variant-numeric:tabular-nums}',
    '.paste__count.over{color:var(--claret)}',
    '.btn{font-family:var(--font-mono);font-size:var(--fs-sm);text-transform:uppercase;letter-spacing:.08em;min-height:48px;padding:0 var(--sp-8);cursor:pointer;border-radius:var(--radius);border:var(--rule) solid var(--claret);background:var(--claret);color:var(--ink-invert);transition:opacity var(--dur) var(--ease)}',
    '.btn:disabled{opacity:.45;cursor:not-allowed}',
    '.btn--ghost{background:transparent;color:var(--claret);border-color:var(--line)}',
    '.galley{display:grid;grid-template-columns:1fr;gap:var(--sp-8);margin-top:var(--sp-8)}',
    '@media (min-width:60rem){.galley{grid-template-columns:1.1fr 1fr}}',
    '.galley__proof{background:var(--paper);border:var(--rule) solid var(--line);border-radius:var(--radius-card);box-shadow:inset 0 0 0 var(--rule) var(--paper-edge);padding:var(--sp-8)}',
    '.galley__proof .doc-card__meta{margin-bottom:var(--sp-3)}',
    '.proof-text{font-family:var(--font-body);font-size:var(--fs-base);line-height:1.7;white-space:pre-wrap;overflow-wrap:break-word;margin:0}',
    '.ledger-margin{display:flex;flex-direction:column;gap:var(--sp-6)}',
    '.total-card{background:var(--paper-2);border:var(--rule) solid var(--line);border-top:var(--rule-bold) solid var(--brass);border-radius:var(--radius-card);padding:var(--sp-6)}',
    '.total-card .kpi-tile__value{font-size:var(--fs-2xl)}',
    '.crit-card{background:var(--paper-2);border:var(--rule) solid var(--line);border-radius:var(--radius-card);box-shadow:inset 0 0 0 var(--rule) var(--paper-edge);padding:var(--sp-6)}',
    '.crit-card.weak{border-left:var(--rule-bold) solid var(--claret)}',
    '.crit-card.strong{border-left:var(--rule-bold) solid var(--green)}',
    '.crit-card__head{display:flex;justify-content:space-between;align-items:baseline;gap:var(--sp-3);margin-bottom:var(--sp-2)}',
    '.crit-card__name{font-family:var(--font-display);font-size:var(--fs-md);margin:0}',
    '.crit-card__pts{font-family:var(--font-mono);font-size:var(--fs-sm);font-variant-numeric:tabular-nums;white-space:nowrap}',
    '.crit-card__id{font-family:var(--font-mono);font-size:var(--fs-mono-xs);text-transform:uppercase;letter-spacing:.08em;color:var(--ink-faint);margin-bottom:var(--sp-3)}',
    '.crit-card__body{margin:var(--sp-3) 0 0}',
    '.crit-card__body p{margin:0 0 var(--sp-2)}',
    '.crit-card__label{font-family:var(--font-mono);font-size:var(--fs-mono-xs);text-transform:uppercase;letter-spacing:.08em;color:var(--ink-faint)}',
    '.revise{border:var(--rule-bold) solid var(--green);background:var(--green-wash);border-radius:var(--radius);padding:var(--sp-6);margin-top:var(--sp-8);max-width:62ch}',
    '.revise .label{color:var(--green);display:block;margin-bottom:var(--sp-2)}',
    '.oversize{border:var(--rule-bold) solid var(--brass);background:var(--brass-wash);border-radius:var(--radius);padding:var(--sp-6);margin:var(--sp-6) 0;max-width:62ch}',
    '.oversize .label{color:var(--brass);display:block;margin-bottom:var(--sp-2)}',
    '.spend-note{font-family:var(--font-mono);font-size:var(--fs-mono-xs);color:var(--ink-faint);text-transform:uppercase;letter-spacing:.08em;margin-top:var(--sp-3)}',
    '.considering-row{display:flex;align-items:center;gap:var(--sp-3);margin:var(--sp-6) 0;color:var(--ink-faint);font-family:var(--font-mono);font-size:var(--fs-mono-xs);text-transform:uppercase;letter-spacing:.08em}',
    '.sr-only{position:absolute;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;clip:rect(0,0,0,0);border:0}'
  ].join('\n');

  var refs = {};
  var busy = false;

  function build() {
    var s = document.createElement('style'); s.textContent = CSS; document.head.appendChild(s);

    var wrap = el('div', 'crit-wrap');

    /* BYOK: persistent header chip row + drawer (byok.js loads before this file) */
    var topRow = el('div');
    topRow.style.cssText = 'display:flex;justify-content:flex-end;padding:var(--sp-3) 0';
    var chipMount = el('span'); chipMount.style.display = 'inline-flex';
    topRow.appendChild(chipMount);
    wrap.appendChild(topRow);

    var rh = el('div', 'running-head');
    rh.appendChild(el('span', null, (cfg.matter_id ? cfg.matter_id.toUpperCase() + ' · ' : '') + 'GALLEY PROOF · CRITIQUE'));
    rh.appendChild(el('span', 'rh-spacer'));
    rh.appendChild(el('span', 'rh-right', 'FIRST-PASS REVIEW'));
    wrap.appendChild(rh);

    var head = el('div', 'crit-head');
    head.appendChild(el('h1', null, cfg.title));
    head.appendChild(el('p', 'crit-intro', 'Paste a draft deliverable — a memo, letter, or pleading — and it will be marked up against this matter’s rubric, criterion by criterion, in the Sonsteng revise-and-resubmit tradition.'));
    wrap.appendChild(head);

    var drawerMount = el('div');
    wrap.appendChild(drawerMount);
    if (window.SonstengBYOK) {
      window.SonstengBYOK.attach({ chipMount: chipMount, drawerMount: drawerMount });
    }

    var warn = el('div', 'paste-warn');
    warn.setAttribute('role', 'note');
    warn.appendChild(el('span', 'label', 'BEFORE YOU PASTE'));
    warn.appendChild(el('p', null, 'Do not paste confidential client information or personally identifying details (PII). Use the synthetic practicum matter facts only. Your text is sent to the grading model to produce the critique and is not stored on our servers.'));
    wrap.appendChild(warn);

    var paste = el('form', 'paste'); paste.setAttribute('novalidate', '');
    var lab = el('label', 'sr-only', 'Paste your deliverable'); lab.setAttribute('for', 'deliverable'); paste.appendChild(lab);
    var ta = el('textarea'); ta.id = 'deliverable';
    ta.setAttribute('placeholder', 'Paste your memo, letter, or pleading here…');
    ta.setAttribute('aria-describedby', 'char-count');
    paste.appendChild(ta);
    var prow = el('div', 'paste__row');
    var count = el('span', 'paste__count'); count.id = 'char-count'; count.setAttribute('aria-live', 'polite');
    var submit = el('button', 'btn', 'Submit for critique'); submit.type = 'submit';
    prow.appendChild(count); prow.appendChild(submit);
    paste.appendChild(prow);
    wrap.appendChild(paste);
    refs.ta = ta; refs.count = count; refs.submit = submit; refs.form = paste;

    var mount = el('div'); mount.id = 'result-mount'; wrap.appendChild(mount);
    refs.mount = mount;

    ROOT.appendChild(wrap);

    ta.addEventListener('input', updateCount);
    paste.addEventListener('submit', function (e) { e.preventDefault(); run(); });
    updateCount();
  }

  function updateCount() {
    var n = refs.ta.value.length;
    refs.count.textContent = n.toLocaleString() + ' / ' + CAP.toLocaleString() + ' CHARS';
    refs.count.classList.toggle('over', n > CAP);
  }

  function run() {
    if (busy) return;
    var text = refs.ta.value;
    refs.mount.textContent = '';
    if (!text.trim()) { oversizeOrNotice('Nothing to critique yet', 'Paste a draft deliverable above, then submit.'); return; }
    if (text.length > CAP) {
      oversizeOrNotice('That draft is a little long for a first pass',
        'This tool is sized for a memo of roughly four pages (' + CAP.toLocaleString() + ' characters). Trim it to your core argument and submit again — the point of the first pass is the shape, not every footnote.');
      return;
    }
    busy = true; refs.submit.disabled = true; refs.submit.textContent = 'Reviewing…';
    var considering = el('div', 'considering-row');
    considering.appendChild(el('span', 'considering'));
    considering.appendChild(el('span', null, 'The grader is reading your draft'));
    refs.mount.appendChild(considering);

    var body = { session_token: sessionToken(), matter_id: cfg.matter_id, deliverable_text: text };
    if (cfg.bypass) body.bypass = cfg.bypass;
    var byok = window.SonstengBYOK && window.SonstengBYOK.get();
    if (byok) body.byok = byok;   // {provider, api_key, model?} — never logged/rendered
    api('/v1/critique', { body: body }).then(function (out) {
      busy = false; refs.submit.disabled = false; refs.submit.textContent = 'Submit for critique';
      refs.mount.textContent = '';
      if (out.ok && out.data && out.data.scorecard) {
        // criteria_labels: {criterion_id: name} — sibling of scorecard in the
        // /v1/critique response (also accepted inside the scorecard, defensively).
        var labels = out.data.criteria_labels || out.data.scorecard.criteria_labels || {};
        renderCritique(text, out.data.scorecard, labels);
      } else {
        var e = (out.data && out.data.error) || {};
        var authish = /\b401\b|unauthoriz|authentication|invalid[_\s-]*(api[_\s-]*)?key|api[_\s-]*key[^.]*invalid|credential/i.test(e.message || '');
        if (e.code === 'no_hosted_key') {
          oversizeOrNotice('Bring your own key', 'This deployment has no house key — bring your own to have the draft reviewed. Use ADD YOUR KEY at the top of the page, then submit again.');
          if (window.SonstengBYOK) window.SonstengBYOK.open('This deployment has no house key — bring your own to have the draft reviewed.');
        } else if (authish && (e.code === 'validation_error' || e.code === 'upstream_unavailable')) {
          oversizeOrNotice('Your key was declined', 'The provider rejected your key — check it in ADD YOUR KEY, then submit again.');
          if (window.SonstengBYOK) window.SonstengBYOK.open('Your key was declined — double-check the provider, the key itself, and any model override.');
        } else if (e.code === 'validation_error' && /size|long|large|413/.test((e.message || '') + out.status)) {
          oversizeOrNotice('That draft is too long', e.message || 'Trim your draft and try again.');
        } else if (e.code === 'cap_exceeded') {
          oversizeOrNotice('Demo budget spent', e.in_character || 'Demo budget for today is spent — come back tomorrow or bring your own key.');
        } else {
          oversizeOrNotice('Couldn’t complete the critique', e.message || 'The grader didn’t respond just now. Please try again in a moment.');
        }
      }
    }, function () {
      busy = false; refs.submit.disabled = false; refs.submit.textContent = 'Submit for critique';
      refs.mount.textContent = '';
      oversizeOrNotice('Couldn’t reach the grader', 'The critique server didn’t answer. Check your connection or API address and try again.');
    });
  }

  function oversizeOrNotice(head, msg) {
    var d = el('div', 'oversize'); d.setAttribute('role', 'status');
    d.appendChild(el('span', 'label', head));
    d.appendChild(el('p', null, msg));
    refs.mount.appendChild(d);
  }

  function renderCritique(memoText, sc, labels) {
    labels = labels || {};
    var galley = el('div', 'galley');

    /* left — the pasted memo as a manuscript proof (textContent only) */
    var proof = el('div', 'galley__proof');
    proof.appendChild(el('div', 'doc-card__meta', 'THE DRAFT · AS SUBMITTED'));
    var pre = el('p', 'proof-text');
    pre.textContent = memoText;   // textContent — never innerHTML
    proof.appendChild(pre);
    galley.appendChild(proof);

    /* right — grader's ledger margin */
    var margin = el('div', 'ledger-margin');

    var total = sc.total || { earned: 0, possible: 0 };
    var tc = el('div', 'total-card');
    tc.appendChild(el('p', 'kpi-tile__label', 'TOTAL · FIRST PASS'));
    tc.appendChild(el('p', 'kpi-tile__value', fmtPts(total.earned) + ' / ' + fmtPts(total.possible)));
    if (sc.narrative) tc.appendChild(el('p', null, sc.narrative));
    margin.appendChild(tc);

    (sc.criteria || []).forEach(function (c) {
      var earned = Number(c.score), possible = Number(c.weight_points);
      var strong = possible > 0 && earned / possible >= 0.75;
      var weak = possible > 0 && earned / possible < 0.5;
      var card = el('div', 'crit-card' + (weak ? ' weak' : (strong ? ' strong' : '')));
      var hd = el('div', 'crit-card__head');
      hd.appendChild(el('h3', 'crit-card__name', criterionName(c.criterion_id, labels)));
      hd.appendChild(el('span', 'crit-card__pts', fmtPts(earned) + ' / ' + fmtPts(possible) + ' PTS'));
      card.appendChild(hd);
      card.appendChild(el('div', 'crit-card__id', c.criterion_id));

      var pct = possible > 0 ? Math.max(0, Math.min(100, (earned / possible) * 100)) : 0;
      var cls = strong ? 'meter meter--ok' : (weak ? 'meter meter--stop' : 'meter meter--warn');
      var meter = el('div', cls);
      var fill = el('span', 'meter__fill'); fill.style.setProperty('--meter-value', pct + '%');
      meter.appendChild(fill); card.appendChild(meter);

      var body = el('div', 'crit-card__body');
      if (c.evidence) { body.appendChild(el('span', 'crit-card__label', 'IN THE DRAFT')); body.appendChild(el('p', null, c.evidence)); }
      if (c.suggestions) { body.appendChild(el('span', 'crit-card__label', 'REVISE')); body.appendChild(el('p', null, c.suggestions)); }
      card.appendChild(body);
      margin.appendChild(card);
    });

    galley.appendChild(margin);
    refs.mount.appendChild(galley);

    /* revise & resubmit insert (green-bound) */
    if (sc.revise_resubmit_note) {
      var rr = el('div', 'revise'); rr.setAttribute('role', 'status');
      rr.appendChild(el('span', 'label', 'REVISE & RESUBMIT'));
      rr.appendChild(el('p', null, sc.revise_resubmit_note));
      refs.mount.appendChild(rr);
    }

    var spend = el('p', 'spend-note', 'ONE CRITIQUE · COUNTS AGAINST TODAY’S DEMO BUDGET');
    refs.mount.appendChild(spend);

    try { galley.scrollIntoView({ block: 'start', behavior: 'smooth' }); } catch (e) {}
  }

  // Prefer the Worker-supplied display name from criteria_labels
  // ({criterion_id: name}); fall back to a "Criterion NN" heading derived from
  // the id (e.g. m05.rub.c01) when no label is present. The mono id sublabel
  // renders either way.
  function criterionName(id, labels) {
    if (labels && typeof labels[id] === 'string' && labels[id].trim()) return labels[id].trim();
    var m = /c(\d+)(?:\.s(\d+))?$/.exec(id || '');
    if (!m) return id || 'Criterion';
    return 'Criterion ' + m[1] + (m[2] ? '.' + m[2] : '');
  }

  window.SonstengCritique = { run: run, refs: function () { return refs; } };

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', build);
  else build();
})();
