/* ============================================================================
   Sonsteng Practicum — byok.js
   "Bring your own key" drawer, shared by the interview room (chat.js) and the
   critique galley (critique.js). Vanilla JS, zero dependencies.

   Provider-agnostic BYOK: the user supplies their OWN key (Anthropic, Google
   Gemini, or OpenAI). No hosted demo key exists, so this drawer is the primary
   onboarding path.

   Storage: localStorage "sonsteng_byok" = {provider, api_key, model?} (JSON).
   The key is NEVER logged and NEVER rendered in full — only masked last-4.
   It is included per-request in POST bodies as byok:{provider, api_key, model?}
   by chat.js / critique.js; this module only stores and edits it.

   All rendering via createElement/textContent. Styled per The Practicum Press
   (.card + brass rule + claret warning), composing theme.css primitives.
   ============================================================================ */
(function () {
  'use strict';

  var LS_KEY = 'sonsteng_byok';

  /* localStorage probe-write with in-memory fallback (private mode throws) */
  var mem = null, live = false;
  try { localStorage.setItem('__byok_probe__', '1'); localStorage.removeItem('__byok_probe__'); live = true; } catch (e) {}

  var PROVIDERS = [
    // Model placeholders are hints only — the Worker applies the real default
    // when model is omitted. (Anthropic id per current model catalog.)
    { id: 'anthropic', label: 'Anthropic (Claude)', modelHint: 'claude-haiku-4-5', keyUrl: 'https://console.anthropic.com', keyHost: 'console.anthropic.com' },
    { id: 'google', label: 'Google Gemini', modelHint: 'gemini-2.5-flash', keyUrl: 'https://aistudio.google.com/apikey', keyHost: 'aistudio.google.com/apikey' },
    { id: 'openai', label: 'OpenAI', modelHint: 'gpt-4o-mini', keyUrl: 'https://platform.openai.com/api-keys', keyHost: 'platform.openai.com/api-keys' }
  ];

  var listeners = [];
  var refs = {};

  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  }

  function get() {
    try {
      var raw = live ? localStorage.getItem(LS_KEY) : mem;
      var c = raw ? JSON.parse(raw) : null;
      if (c && c.provider && c.api_key) return c;
    } catch (e) {}
    return null;
  }
  function set(cfg) {
    var clean = { provider: cfg.provider, api_key: cfg.api_key };
    if (cfg.model && String(cfg.model).trim()) clean.model = String(cfg.model).trim();
    var raw = JSON.stringify(clean);
    try { if (live) localStorage.setItem(LS_KEY, raw); else mem = raw; } catch (e) { mem = raw; }
    refresh();
    notify();
  }
  function clear() {
    try { if (live) localStorage.removeItem(LS_KEY); } catch (e) {}
    mem = null;
    refresh();
    notify();
  }
  function notify() { var c = get(); listeners.forEach(function (cb) { try { cb(c); } catch (e) {} }); }
  function mask(key) { return '••••' + String(key).slice(-4); }
  function providerLabel(id) {
    for (var i = 0; i < PROVIDERS.length; i++) if (PROVIDERS[i].id === id) return PROVIDERS[i].label;
    return id;
  }

  var CSS = [
    '.byok-chip{display:inline-flex;align-items:center;gap:.45em;cursor:pointer;',
    '  font-family:var(--font-mono);font-size:var(--fs-mono-xs);text-transform:uppercase;letter-spacing:.08em;',
    '  color:var(--claret-strong);background:var(--surface-card);border:var(--rule) solid var(--line);',
    '  border-left:var(--rule-bold) solid var(--claret);border-radius:var(--radius);',
    '  padding:.6em .9em;min-height:44px;white-space:nowrap}',
    '.byok-chip.is-set{color:var(--green);border-left-color:var(--green);background:var(--green-wash)}',
    '.byok-drawer{margin:var(--sp-6) 0;display:none}',
    '.byok-drawer.is-open{display:block}',
    '.byok-drawer.card{background:var(--surface-card);border-color:var(--border-strong);box-shadow:var(--shadow-offset)}',
    '.byok-drawer .brass-rule{margin:var(--sp-3) 0 var(--sp-6)}',
    '.byok-note{font-style:italic;color:var(--ink-soft);background:var(--surface-inset);',
    '  border-left:var(--rule-bold) solid var(--claret);border-radius:0 var(--radius) var(--radius) 0;',
    '  padding:var(--sp-3) var(--sp-6);margin:0 0 var(--sp-6)}',
    '.byok-warn{border:var(--rule) solid var(--line);border-left:var(--rule-bold) solid var(--claret);background:var(--surface-featured);',
    '  border-radius:var(--radius);padding:var(--sp-3) var(--sp-6);margin:0 0 var(--sp-6)}',
    '.byok-warn .label{color:var(--claret);display:block;margin-bottom:var(--sp-2)}',
    '.byok-warn p{margin:0;font-size:var(--fs-sm)}',
    '.byok-form{display:grid;gap:var(--sp-3)}',
    '.byok-form label.byok-lab{font-family:var(--font-mono);font-size:var(--fs-mono-xs);text-transform:uppercase;letter-spacing:.08em;color:var(--ink-soft)}',
    '.byok-form select,.byok-form input{font-family:var(--font-body);font-size:1.05rem;color:var(--ink);',
    '  background:var(--surface-card);border:var(--rule) solid var(--line);border-left:var(--rule-bold) solid var(--brass);',
    '  border-radius:var(--radius);padding:.6em .8em;min-height:48px;width:100%}',
    '.byok-form input[type=password]{font-family:var(--font-mono);letter-spacing:.05em}',
    '.byok-saved{font-family:var(--font-mono);font-size:var(--fs-sm);font-variant-numeric:tabular-nums;color:var(--green);margin:0}',
    '.byok-actions{display:flex;gap:var(--sp-3);flex-wrap:wrap;align-items:center;margin-top:var(--sp-3)}',
    '.byok-btn{font-family:var(--font-mono);font-size:var(--fs-sm);text-transform:uppercase;letter-spacing:.08em;',
    '  min-height:48px;padding:0 var(--sp-8);cursor:pointer;border-radius:var(--radius);',
    '  border:var(--rule) solid var(--claret);background:var(--surface-featured);color:var(--claret-strong)}',
    '.byok-btn--ghost{background:var(--surface-card);color:var(--claret);border-color:var(--line)}',
    '.byok-btn--link{background:none;border:0;color:var(--claret);font-size:var(--fs-mono-xs);padding:.4em .2em;min-height:44px;cursor:pointer;font-family:var(--font-mono);text-transform:uppercase;letter-spacing:.08em}',
    '.byok-where{font-size:var(--fs-sm);color:var(--ink-soft);margin:var(--sp-6) 0 0}',
    '.byok-where a{color:var(--claret);text-decoration:underline;text-underline-offset:2px}',
    '.byok-status{font-family:var(--font-mono);font-size:var(--fs-mono-xs);text-transform:uppercase;letter-spacing:.08em;color:var(--ink-faint);margin:var(--sp-2) 0 0}'
  ].join('\n');

  function injectCss() {
    if (document.getElementById('byok-css')) return;
    var s = document.createElement('style');
    s.id = 'byok-css';
    s.textContent = CSS;
    document.head.appendChild(s);
  }

  /* ---------- build the chip + drawer ---------- */
  function attach(opts) {
    opts = opts || {};
    injectCss();

    /* chip */
    var chip = el('button', 'byok-chip');
    chip.type = 'button';
    chip.setAttribute('aria-expanded', 'false');
    chip.addEventListener('click', function () { toggle(); });
    if (opts.chipMount) opts.chipMount.appendChild(chip);
    refs.chip = chip;

    /* drawer */
    var d = el('section', 'byok-drawer card');
    d.setAttribute('aria-label', 'Bring your own key');

    var rh = el('div', 'running-head');
    rh.appendChild(el('span', null, 'BRING YOUR OWN KEY'));
    rh.appendChild(el('span', 'rh-spacer'));
    var rhRight = el('span', 'rh-right', 'NOT CONFIGURED');
    rh.appendChild(rhRight);
    d.appendChild(rh);
    refs.rhRight = rhRight;

    var note = el('p', 'byok-note'); note.style.display = 'none';
    d.appendChild(note);
    refs.note = note;

    d.appendChild(el('h3', null, 'Your key opens the door'));
    d.appendChild(el('p', null, 'This deployment runs on your own API key — Anthropic, Google Gemini, or OpenAI. Add one below and the client will be ready to talk.'));

    /* LOUD warning */
    var warn = el('div', 'byok-warn'); warn.setAttribute('role', 'note');
    warn.appendChild(el('span', 'label', 'READ BEFORE SAVING A KEY'));
    warn.appendChild(el('p', null, 'Your key is stored in PLAIN TEXT in this browser’s localStorage, where browser extensions can read it. Use a low-limit key you can revoke, never a production key. Each request sends the key only to this app’s API server, which forwards it to your chosen provider and never stores it.'));
    d.appendChild(warn);

    /* saved-state line (masked) */
    var saved = el('p', 'byok-saved'); saved.style.display = 'none';
    d.appendChild(saved);
    refs.saved = saved;

    /* form */
    var form = el('form', 'byok-form'); form.setAttribute('novalidate', '');

    var labP = el('label', 'byok-lab', 'Provider'); labP.setAttribute('for', 'byok-provider');
    var sel = el('select'); sel.id = 'byok-provider';
    PROVIDERS.forEach(function (p) {
      var o = el('option', null, p.label); o.value = p.id; sel.appendChild(o);
    });
    form.appendChild(labP); form.appendChild(sel);

    var labK = el('label', 'byok-lab', 'API key'); labK.setAttribute('for', 'byok-key');
    var key = el('input'); key.id = 'byok-key'; key.type = 'password';
    key.setAttribute('autocomplete', 'off');
    key.setAttribute('spellcheck', 'false');
    key.setAttribute('placeholder', 'Paste your API key');
    form.appendChild(labK); form.appendChild(key);

    var labM = el('label', 'byok-lab', 'Model override (optional)'); labM.setAttribute('for', 'byok-model');
    var model = el('input'); model.id = 'byok-model'; model.type = 'text';
    model.setAttribute('autocomplete', 'off');
    model.setAttribute('spellcheck', 'false');
    form.appendChild(labM); form.appendChild(model);

    var acts = el('div', 'byok-actions');
    var save = el('button', 'byok-btn', 'Save key'); save.type = 'submit';
    var clr = el('button', 'byok-btn byok-btn--ghost', 'Clear key'); clr.type = 'button';
    var close = el('button', 'byok-btn--link', 'Close'); close.type = 'button';
    acts.appendChild(save); acts.appendChild(clr); acts.appendChild(close);
    form.appendChild(acts);

    var status = el('p', 'byok-status'); status.setAttribute('aria-live', 'polite');
    form.appendChild(status);
    refs.status = status;

    d.appendChild(form);

    /* where do I get a key? — external navigation links (allowed exception to
       the no-external-requests rule: these are links the user clicks, not
       resources the page loads). */
    var where = el('p', 'byok-where');
    where.appendChild(document.createTextNode('Where do I get a key? '));
    PROVIDERS.forEach(function (p, i) {
      var a = el('a', null, p.keyHost);
      a.href = p.keyUrl;
      a.target = '_blank';
      a.rel = 'noopener noreferrer';
      where.appendChild(a);
      if (i < PROVIDERS.length - 1) where.appendChild(document.createTextNode(' · '));
    });
    d.appendChild(where);

    if (opts.drawerMount) opts.drawerMount.appendChild(d);
    refs.drawer = d; refs.sel = sel; refs.key = key; refs.model = model;

    sel.addEventListener('change', updateModelHint);
    form.addEventListener('submit', function (e) {
      e.preventDefault();
      var k = key.value.trim();
      var existing = get();
      if (!k && existing && existing.provider === sel.value) {
        // provider unchanged, key untouched -> just update the model override
        set({ provider: existing.provider, api_key: existing.api_key, model: model.value });
        status.textContent = 'Updated.';
        return;
      }
      if (!k) { status.textContent = 'Paste a key before saving.'; key.focus(); return; }
      set({ provider: sel.value, api_key: k, model: model.value });
      key.value = '';                          // never keep the raw key in the field
      status.textContent = 'Key saved to this browser.';
    });
    clr.addEventListener('click', function () {
      clear();
      key.value = ''; model.value = '';
      status.textContent = 'Key cleared from this browser.';
    });
    close.addEventListener('click', function () { toggle(false); });

    refresh();
  }

  function updateModelHint() {
    if (!refs.sel || !refs.model) return;
    var id = refs.sel.value;
    for (var i = 0; i < PROVIDERS.length; i++) {
      if (PROVIDERS[i].id === id) {
        refs.model.setAttribute('placeholder', 'Provider default · e.g. ' + PROVIDERS[i].modelHint);
        return;
      }
    }
  }

  function refresh() {
    var c = get();
    if (refs.chip) {
      refs.chip.textContent = c ? ('KEY · ' + c.provider.toUpperCase() + ' ' + mask(c.api_key)) : 'ADD YOUR KEY';
      refs.chip.classList.toggle('is-set', !!c);
      refs.chip.setAttribute('aria-label', c
        ? 'API key configured for ' + providerLabel(c.provider) + ' — open key settings'
        : 'No API key configured — add your key');
    }
    if (refs.rhRight) refs.rhRight.textContent = c ? (c.provider.toUpperCase() + ' · ' + mask(c.api_key)) : 'NOT CONFIGURED';
    if (refs.saved) {
      if (c) {
        refs.saved.textContent = 'SAVED · ' + providerLabel(c.provider) + ' · ' + mask(c.api_key) + (c.model ? ' · ' + c.model : '');
        refs.saved.style.display = '';
      } else {
        refs.saved.style.display = 'none';
      }
    }
    if (refs.sel && c) refs.sel.value = c.provider;
    if (refs.model && c && c.model) refs.model.value = c.model;
    updateModelHint();
  }

  function toggle(force) {
    if (!refs.drawer) return;
    var open = typeof force === 'boolean' ? force : !refs.drawer.classList.contains('is-open');
    refs.drawer.classList.toggle('is-open', open);
    if (refs.chip) refs.chip.setAttribute('aria-expanded', open ? 'true' : 'false');
    if (!open && refs.note) refs.note.style.display = 'none';
    if (open) {
      try { refs.drawer.scrollIntoView({ block: 'nearest', behavior: 'smooth' }); } catch (e) {}
    }
  }

  /* open the drawer with an optional explainer line (e.g. no_hosted_key) */
  function open(noticeText) {
    if (refs.note) {
      if (noticeText) { refs.note.textContent = noticeText; refs.note.style.display = ''; }
      else refs.note.style.display = 'none';
    }
    toggle(true);
    if (refs.key && !get()) { try { refs.key.focus(); } catch (e) {} }
  }

  window.SonstengBYOK = {
    get: get,
    set: set,          // used by the dev harness; UI path goes through the form
    clear: clear,
    attach: attach,
    open: open,
    toggle: toggle,
    onChange: function (cb) { listeners.push(cb); }
  };
})();
