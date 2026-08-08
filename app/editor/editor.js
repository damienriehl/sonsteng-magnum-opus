/* ============================================================================
   Sonsteng Practicum — editor.js
   The Worker-injected EDIT-MODE client. Vanilla JS, zero dependencies.

   Served ONLY by the Worker's /edit/<path> proxy-injector. Turns the real
   platform page into an editor for Prof. John O. Sonsteng (Windows-primary,
   iPad secondary, zero training): click a paragraph to edit it in place;
   highlight text to leave a Word-style comment. Everything is a SUGGESTION
   that goes to Damien for review.

   Contract this file codes to (AUTHORITATIVE Worker contract, 102 tests):
     - Page arrives with TWO escaped-JSON islands:
         #editor-map-data = { version, page?, blocks:[block…] }
           block: {index, kind:'prose'|'json_scalar'|'comment_only',
                   source_ref, original_text, original_hash,
                   has_inline_formatting, context, json_path?}
         #edits-data      = { items:[item…] }
           item:  {block_index, source_ref, status, kind, preview, note?}
       Read via textContent + JSON.parse — NEVER eval, NEVER innerHTML.
     - Blocks are located by mirroring THE WALKER CONTRACT byte-for-byte
       (build/editor-map.generated.json .walker_contract): within <main>,
       candidate elements in document order = p, li, h1-h6, blockquote
       (OUTERMOST — candidates never nest); index = 0-based position. The
       Worker does NOT stamp data-eb — this walk IS the block-location contract.
     - Cookie auth is automatic (edit_scope, HttpOnly, Path=/edit); mutations
       send header  X-Edit-Request:1  (+ credentials:'same-origin').
     - POST /edit/v1/suggest
         { id, source_ref, json_path?, new_text|comment, original_hash } -> 200 {status}
       (server derives editor/original_text/kind/page/map_version; client values
        for those are IGNORED.)
       error codes: no_edit_auth(401 → friendly re-auth, preserve draft+id),
                    rate_limited(429), validation_error(413 oversize),
                    stale_page(409 → reload this block). Read data.error.code first.
     - GET  /edit/v1/pending?page=  ->  {items:[…]}  (inline status sync).

   LIFTED VERBATIM from the two PROVEN spikes (app/editor/spikes/, 21 race
   assertions pass): makeStore + SS/LS split, uuid, the three-verb
   blur/draft/commit model, enterEdit mint-once, the synchronous
   disable-before-await commit critical section, saveDraft/loadDraft/
   clearDraft/reconcileDraft (keyed to source_ref+original_hash, full-state
   reset on discard), neutralize (capture-phase), pageshow/visibilitychange
   re-poll, the mousedown+preventDefault Save binding with the e.detail===0
   keyboard guard; currentSelectionSnapshot (capture-at-rest + tremor debounce
   + PROSE-scoped clamp), onSelectionActivity, findLastTextNode, normalizeWs,
   the float mousedown-preventDefault reading `captured`.

   ADAPTED for production: `normalize()` now mirrors tools/text_norm.py
   BYTE-FOR-BYTE (was a lite subset in the spike); blocks are the injected
   page's real elements (not spike-built); suggest/comment payloads follow
   the /edit/v1 contract; inline pending status + Word-style margin bubbles;
   a single fixed keyboard-tracking save bar (visualViewport); an
   autocorrect-preview step before submit; comment-only enforcement on
   json_scalar + has_inline_formatting blocks.

   Everything dynamic is rendered via createElement + textContent — NEVER
   innerHTML with server/user data. The JSON island is read via textContent.
   ============================================================================ */
(function () {
  'use strict';

  /* ============================================================================
     0. Config, storage, ids, transport  (spike + chat.js house patterns)
     ============================================================================ */

  var API_BASE = '/edit/v1';

  /* ---------- auto-save timing (fence durability spec) ---------------------
     Typing pauses ~AUTOSAVE_MS → one debounced auto-send per block. One save is
     in flight at a time (guarded by state===SAVING); input during a send queues
     one more. A network failure backs off (RETRY_BASE_MS × 2ⁿ, capped) and
     retries. The idempotency id is FRESH per burst and rotated on every success,
     so a same-id-different-payload replay can never silently drop a later edit. */
  var AUTOSAVE_MS = 2500;
  var RETRY_BASE_MS = 4000;
  var RETRY_MAX_MS = 60000;

  /* ---------- storage: probe-write with in-memory fallback (VERBATIM) ------- */
  function makeStore(backing) {
    var mem = {}, live = false;
    try { var k = '__ed_probe__'; backing.setItem(k, '1'); backing.removeItem(k); live = true; } catch (e) { live = false; }
    return {
      live: live,
      get: function (k) { try { return live ? backing.getItem(k) : (k in mem ? mem[k] : null); } catch (e) { return (k in mem ? mem[k] : null); } },
      set: function (k, v) { try { if (live) backing.setItem(k, v); else mem[k] = v; } catch (e) { mem[k] = v; } },
      del: function (k) { try { if (live) backing.removeItem(k); else delete mem[k]; } catch (e) { delete mem[k]; } }
    };
  }
  var SS = makeStore(window.sessionStorage);   // per-tab ACTIVE draft buffer
  var LS = makeStore(window.localStorage);     // cross-tab recovery-only + prefs
  var DRAFT_PREFIX = 'sonsteng_edit_draft:';   // keyed by page + source_ref
  var SCOPED_DRAFT_KEY = DRAFT_PREFIX + 'scoped-request|' + location.pathname;
  var FACT_DRAFT_KEY = DRAFT_PREFIX + 'add-fact|' + location.pathname;

  function saveDialogDraft(key, rec) {
    rec.ts = Date.now();
    SS.set(key, JSON.stringify(rec));
    LS.set(key, JSON.stringify(rec));
  }
  function loadDialogDraft(key) {
    try { return JSON.parse(SS.get(key) || 'null'); } catch (e) { return null; }
  }
  function clearDialogDraft(key) { SS.del(key); LS.del(key); }

  /* ---------- uuid (VERBATIM) ---------------------------------------------- */
  function uuid() {
    try { if (window.crypto && crypto.randomUUID) return crypto.randomUUID(); } catch (e) {}
    return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, function (c) {
      var r = (Math.random() * 16) | 0, v = c === 'x' ? r : ((r & 0x3) | 0x8); return v.toString(16);
    });
  }

  /* ---------- normalization — MIRRORS tools/text_norm.py BYTE-FOR-BYTE ------
     Both sides must agree to the code point or every hash mismatches and the
     apply loop stalls. Ordered exactly per the frozen spec:
       1. NFC
       2. contenteditable artifact strip: CRLF/CR -> LF, U+2028/U+2029 -> LF,
          then every LF -> single space
       3. smart-quote / dash / space fold to ASCII; delete zero-width; …-> ...
       4. collapse whitespace runs -> one space, trim
     (Client uses this for the dirty check + the no-op guard; the block's
     original_hash comes from the map, so no async SHA-256 is needed to key or
     submit. normHash() is provided for parity checks.) */
  function normalize(s) {
    if (s == null) return '';
    s = String(s);
    try { s = s.normalize('NFC'); } catch (e) {}
    // 2. contenteditable artifact strip: CRLF/CR -> LF, U+2028/U+2029 -> LF, LF -> space
    s = s.replace(/\r\n/g, '\n').replace(/\r/g, '\n');
    s = s.replace(/\u2028/g, '\n').replace(/\u2029/g, '\n');
    s = s.replace(/\n/g, ' ');
    // 3. smart-quote / dash / space fold; delete zero-width; ellipsis -> ...
    s = s.replace(/[\u2018\u2019\u201A\u201B\u2032]/g, "'")
         .replace(/[\u201C\u201D\u201E\u201F\u2033]/g, '"')
         .replace(/[\u2010\u2011\u2012\u2013\u2014\u2015\u2212]/g, '-')
         .replace(/[\u00A0\u2007\u202F\u2009\u200A]/g, ' ')
         .replace(/[\u200B\u200C\u200D\uFEFF]/g, '')
         .replace(/\u2026/g, '...');
    // 4. whitespace collapse + trim
    s = s.replace(/\s+/g, ' ').replace(/^\s+|\s+$/g, '');
    return s;
  }
  // SHA-256 hex of normalize(s) — the map's original_hash spec. Async (subtle).
  function normHash(s) {
    var bytes = new TextEncoder().encode(normalize(s));
    if (window.crypto && crypto.subtle && crypto.subtle.digest) {
      return crypto.subtle.digest('SHA-256', bytes).then(function (buf) {
        var a = new Uint8Array(buf), h = '';
        for (var i = 0; i < a.length; i++) h += (a[i] < 16 ? '0' : '') + a[i].toString(16);
        return h;
      });
    }
    return Promise.resolve(null);
  }

  /* ---------- log (optional dev harness surface) --------------------------- */
  var LOG = [];
  function log() {
    var m = Array.prototype.slice.call(arguments).join(' ');
    LOG.push(m);
    var el = document.getElementById('editor-log');
    if (el) { el.textContent = LOG.slice(-40).join('\n'); el.scrollTop = el.scrollHeight; }
  }

  /* ---------- transport (mock hook for the dev harness) --------------------
     window.__EDITOR_MOCK__({path, method, body, headers}) -> {ok,status,data}
     lets test-harness.html exercise every path with no Worker. Real path uses
     same-origin fetch: cookie auth automatic (credentials:'include'); every
     mutation carries the X-Edit-Request:1 header (CSRF layer). */
  function api(path, opts) {
    opts = opts || {};
    var headers = { 'X-Edit-Request': '1' };
    var mock = window.__EDITOR_MOCK__;
    if (typeof mock === 'function') {
      return Promise.resolve(mock({ path: path, method: opts.method || 'POST', body: opts.body || null, headers: headers }));
    }
    var fo = { method: opts.method || 'POST', headers: headers, cache: 'no-store', credentials: 'same-origin' };
    if (opts.keepalive) fo.keepalive = true;   // survive pagehide/visibilitychange
    if (opts.body) { fo.headers['content-type'] = 'application/json'; fo.body = JSON.stringify(opts.body); }
    return fetch(API_BASE + path, fo).then(function (res) {
      return res.json().catch(function () { return null; }).then(function (data) {
        return { ok: res.ok, status: res.status, data: data };
      });
    }, function (err) {
      return { ok: false, status: 0, data: { error: { code: 'network', message: (err && err.message) || 'network' } }, network: true };
    });
  }

  /* ---------- DOM helper (createElement/textContent only) ------------------ */
  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  }

  /* ============================================================================
     1. Read the TWO injected JSON islands (Worker contract) — textContent + parse.
        #editor-map-data = { version, page?, blocks:[{index,kind,source_ref,
          json_path?,original_text,original_hash,has_inline_formatting,context}] }
        #edits-data      = { items:[{block_index,source_ref,status,kind,preview,note?}] }
        Read the MAP from #editor-map-data.blocks; PENDING from #edits-data.items.
     ============================================================================ */
  function readJsonIsland(id) {
    var node = document.getElementById(id);
    if (!node) return null;
    try { return JSON.parse(node.textContent || 'null'); } catch (e) { log('ISLAND parse failed: ' + id); return null; }
  }
  function derivePage() {
    var p = location.pathname || '';
    var i = p.indexOf('/edit/');
    return i >= 0 ? p.slice(i + 6) : p.replace(/^\//, '');
  }
  var MAP_ISLAND = readJsonIsland('editor-map-data') || {};
  var EDITS_ISLAND = readJsonIsland('edits-data') || {};
  var MAP = MAP_ISLAND.blocks || [];
  var PAGE_OVERRIDES = MAP_ISLAND.overrides || [];
  var INITIAL_PENDING = EDITS_ISLAND.items || [];
  var PAGE = MAP_ISLAND.page || derivePage();

  /* ---------- SL6 liveness (apply-daemon heartbeat + direct-apply mode) -----
     The injected island seeds these; every /pending re-poll refreshes them. The
     age is a snapshot at projection time, so we anchor it to load time and let it
     grow locally (the banner degrades to the "paused" warning even with no poll). */
  var DIRECT_APPLY = EDITS_ISLAND.direct_apply === true;
  var hbBaseAge = (typeof EDITS_ISLAND.heartbeat_age_s === 'number') ? EDITS_ISLAND.heartbeat_age_s : null;
  var hbBaseAt = Date.now();
  function setHeartbeat(ageS, directApply) {
    if (typeof directApply === 'boolean') DIRECT_APPLY = directApply;
    hbBaseAge = (typeof ageS === 'number') ? ageS : null;
    hbBaseAt = Date.now();
    updateBanner();
  }
  function currentHbAge() {
    if (hbBaseAge == null) return null;
    return hbBaseAge + Math.floor((Date.now() - hbBaseAt) / 1000);
  }

  /* ============================================================================
     2. THE WALKER CONTRACT — mirror the generator's block-walk byte-for-byte.
        Within <main>, candidate tags in document order are p, li, h1-h6,
        blockquote; take the OUTERMOST (a candidate that is not a descendant of
        another candidate). index = 0-based position in that ordered list.
     ============================================================================ */
  var CANDIDATE_SEL = 'p,li,h1,h2,h3,h4,h5,h6,blockquote';
  function outermostCandidates(main) {
    if (!main) return [];
    var all = main.querySelectorAll(CANDIDATE_SEL);   // document order
    var out = [];
    for (var i = 0; i < all.length; i++) {
      var e = all[i], nested = false, p = e.parentElement;
      while (p && p !== main) {
        if (p.matches && p.matches(CANDIDATE_SEL)) { nested = true; break; }
        p = p.parentElement;
      }
      if (!nested) out.push(e);
    }
    return out;
  }

  /* ============================================================================
     3. Edit-session model — the state machine (three verbs, one event each).
        LIFTED from spike-blur-save: makeSession, ST states, saveDraft/loadDraft/
        clearDraft/reconcileDraft, enterEdit mint-once, commit critical section,
        neutralize, discard.
     ============================================================================ */
  var ST = { IDLE: 'IDLE', EDITING: 'EDITING', SAVING: 'SAVING' };
  var sessions = {};        // source_ref -> session
  var byIndex = {};         // block_index -> session
  var activeRef = null;

  function session(ref) { return sessions[ref]; }

  function makeSession(desc, elx, editable, commentOnly) {
    var s = {
      ref: desc.source_ref, index: desc.index, el: elx, kind: desc.kind,
      editable: editable, commentOnly: commentOnly,
      hasFormatting: !!desc.has_inline_formatting, jsonPath: desc.json_path || null,
      occurrences: Array.isArray(desc.occurrences) ? desc.occurrences.slice() : [],
      shared: Array.isArray(desc.occurrences) && desc.occurrences.length > 1,
      originalText: desc.original_text != null ? desc.original_text : (elx.textContent || ''),
      originalHtml: elx.innerHTML,
      originalHash: desc.original_hash || '',
      suggestionId: null, snapshot: null, state: ST.IDLE, dirty: false,
      _debounce: null, _retry: null, _retryDelay: 0, _queued: false, // auto-save
      pendingComment: null   // {id, anchor_text} while a comment bubble is open on this block
    };
    s.snapshot = s.originalText;
    sessions[s.ref] = s; byIndex[s.index] = s;
    return s;
  }

  function draftKey(s) { return DRAFT_PREFIX + PAGE + '|' + s.ref; }

  /* ---------- draft persistence (VERBATIM shape; keyed source_ref+hash) ----- */
  function saveDraft(s) {
    var rec = { page: PAGE, source_ref: s.ref, original_hash: s.originalHash,
                suggestion_id: s.suggestionId, new_text: s.snapshot, ts: Date.now() };
    SS.set(draftKey(s), JSON.stringify(rec));
    LS.set(draftKey(s), JSON.stringify(rec));   // survives a closed tab/browser restart
  }
  function loadDraft(s) {
    try {
      return JSON.parse(SS.get(draftKey(s)) || LS.get(draftKey(s)) || 'null');
    } catch (e) { return null; }
  }
  function clearDraft(s) { SS.del(draftKey(s)); LS.del(draftKey(s)); }

  /* ---------- reconcile a draft against the block's CURRENT hash (R8) ------- */
  function reconcileDraft(s) {
    var rec = loadDraft(s);
    if (!rec) return;
    if (rec.original_hash !== s.originalHash) {
      clearDraft(s);
      s.suggestionId = null;
      s.snapshot = s.originalText;
      s.dirty = false;
      s.state = ST.IDLE;
      restoreOriginal(s);
      s.el.classList.remove('editing', 'dirty');
      if (activeRef === s.ref) hideBar();
      showNote(s, 'This paragraph was updated on the site, so your unsent draft here was set aside. Nothing was lost anywhere else — just start fresh.');
      log('DRAFT discarded (hash moved) ref=' + s.ref);
      return;
    }
    if (typeof rec.new_text !== 'string' || rec.new_text === s.originalText) {
      clearDraft(s);
      return;
    }
    // A valid draft is newer user intent than both the rendered source and any
    // pending overlay. Put it back on screen immediately after refresh, with an
    // explicit unsent status, and preserve the original id for an idempotent
    // retry. Previously the record existed but was never rehydrated, leaving
    // John's wording stranded invisibly in storage.
    s.suggestionId = rec.suggestion_id || s.suggestionId || uuid();
    s.snapshot = rec.new_text;
    s.dirty = true;
    setBlockText(s, rec.new_text);
    s.el.classList.add('dirty');
    setLocalStatus(s, 'Draft restored — not sent yet', 'err');
    log('DRAFT restored ref=' + s.ref + ' id=' + s.suggestionId.slice(0, 8));
  }

  function setBlockText(s, text) { s.el.textContent = text; }   // never innerHTML
  function restoreOriginal(s) { s.el.innerHTML = s.originalHtml; }
  function htmlForText(text) {
    var holder = document.createElement('span');
    holder.textContent = text == null ? '' : String(text);
    return holder.innerHTML;
  }

  /* ---------- computed values: locked, but never a dead end -------------- */
  var lockedDialogEl = null;
  var lockedReturnFocus = null;

  function closeLockedDialog() {
    if (!lockedDialogEl) return;
    lockedDialogEl.remove();
    lockedDialogEl = null;
    if (lockedReturnFocus && lockedReturnFocus.focus) lockedReturnFocus.focus();
    lockedReturnFocus = null;
  }

  function openLockedDialog(el) {
    if (!el) return;
    closeLockedDialog();
    lockedReturnFocus = el;
    var origin = el.getAttribute('data-eb-origin') || 'a generated source';
    var passage = el.getAttribute('data-eb-passage') || '';
    var dlg = document.createElement('div');
    dlg.className = 'eb-locked-dialog';
    dlg.setAttribute('role', 'dialog');
    dlg.setAttribute('aria-modal', 'true');
    dlg.setAttribute('aria-labelledby', 'eb-locked-title');
    var inner = document.createElement('div');
    inner.className = 'eb-locked-dialog__inner';
    var title = document.createElement('h2');
    title.id = 'eb-locked-title';
    title.className = 'eb-locked-dialog__title';
    title.textContent = 'Computed value';
    var lede = document.createElement('p');
    lede.className = 'eb-locked-dialog__lede';
    lede.textContent = 'This value comes from ' + origin + ' and cannot be edited here.';
    var row = document.createElement('div');
    row.className = 'eb-locked-dialog__row';
    if (passage) {
      var link = document.createElement('a');
      link.className = 'eb-locked-dialog__passage';
      link.href = passage;
      link.target = '_blank';
      link.rel = 'noopener noreferrer';
      link.textContent = 'Open its editing surface';
      row.appendChild(link);
    }
    var close = document.createElement('button');
    close.type = 'button';
    close.className = 'eb-locked-dialog__close';
    close.textContent = 'Close';
    close.addEventListener('click', closeLockedDialog);
    row.appendChild(close);
    inner.appendChild(title); inner.appendChild(lede); inner.appendChild(row);
    dlg.appendChild(inner); document.body.appendChild(dlg);
    dlg.addEventListener('mousedown', function (ev) { if (ev.target === dlg) closeLockedDialog(); });
    dlg.addEventListener('keydown', function (ev) {
      if (ev.key === 'Escape') { ev.preventDefault(); closeLockedDialog(); }
    });
    lockedDialogEl = dlg;
    (passage ? row.querySelector('a') : close).focus();
  }

  function installLockedPassages() {
    var locked = document.querySelectorAll('[data-eb-locked]');
    for (var i = 0; i < locked.length; i++) (function (el) {
      el.classList.add('eb--locked');
      el.setAttribute('role', 'button');
      el.setAttribute('tabindex', '0');
      el.setAttribute('aria-label', 'Computed value: ' + (el.textContent || '') + '. Show its source.');
      el.addEventListener('click', function () { openLockedDialog(el); });
      el.addEventListener('keydown', function (ev) {
        if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); openLockedDialog(el); }
      });
    })(locked[i]);
  }

  /* ============================================================================
     4. Per-block chrome — affordances, notes, inline status, margin bubbles.
        Each editable/commentable block is wrapped in a light DOM: the block
        keeps its element; we append sibling nodes for tools + status.
     ============================================================================ */
  /* ---------- icon affordances (pencil / speech bubble) --------------------
     Replaces the pair of full-width uppercase word-buttons that dominated every
     paragraph. Three rules govern this, and the first two are why the icons are
     NOT hover-only:

       1. TOUCH HAS NO HOVER. The guide tells John an iPad works, and on a tablet
          a hover-revealed control simply does not exist. Reveal-on-hover would
          silently remove editing for whichever device he happens to pick up.
       2. DISCOVERABILITY. A first-time reader does not sweep the pointer over
          paragraphs hunting for controls. The affordance has to be visible while
          the page is at rest for anyone to learn it is there.
       3. QUIET, THEN LOUD. What was actually wrong was WEIGHT, not presence. So
          the icons sit at rest in a muted ink, and come up to full contrast (with
          a visible word label) as soon as the pointer or keyboard reaches the
          paragraph. Present always; assertive only when relevant.

     Hit areas stay at 44x44 CSS px (WCAG 2.5.5) even though the glyph is 20px —
     the visual weight drops, the target does not. The original design deliberately
     oversized these for an older reader and that judgment still holds. */
  var SVG_NS = 'http://www.w3.org/2000/svg';
  function iconSvg(paths) {
    var svg = document.createElementNS(SVG_NS, 'svg');
    svg.setAttribute('viewBox', '0 0 24 24');
    svg.setAttribute('width', '20'); svg.setAttribute('height', '20');
    svg.setAttribute('fill', 'none'); svg.setAttribute('stroke', 'currentColor');
    svg.setAttribute('stroke-width', '1.75');
    svg.setAttribute('stroke-linecap', 'round'); svg.setAttribute('stroke-linejoin', 'round');
    svg.setAttribute('aria-hidden', 'true'); svg.setAttribute('focusable', 'false');
    paths.forEach(function (d) {
      var p = document.createElementNS(SVG_NS, 'path'); p.setAttribute('d', d); svg.appendChild(p);
    });
    return svg;
  }
  var ICON_PENCIL = ['M12 20h9', 'M16.4 3.6a2.12 2.12 0 0 1 3 3L7.5 18.5 3.5 19.5l1-4Z'];
  var ICON_COMMENT = ['M20.5 11.4a7.9 7.9 0 0 1-.85 3.6 8 8 0 0 1-7.15 4.4 7.9 7.9 0 0 1-3.6-.85L3.5 20.5l2.35-5.4A7.9 7.9 0 0 1 5 11.5a8 8 0 0 1 4.4-7.15 7.9 7.9 0 0 1 3.6-.85h.5a7.98 7.98 0 0 1 7.5 7.5v.4Z'];
  var ICON_PLUS = ['M12 5v14', 'M5 12h14'];
  var ICON_TRASH = ['M4 7h16', 'M9 7V5a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2', 'M6.5 7l.8 12a1 1 0 0 0 1 .9h7.4a1 1 0 0 0 1-.9l.8-12', 'M10 11v6', 'M14 11v6'];
  var ICON_UP = ['M12 19V5', 'M6 11l6-6 6 6'];
  var ICON_DOWN = ['M12 5v14', 'M6 13l6 6 6-6'];

  /* ---------- structural operations (U4) -----------------------------------
     Add / remove / move whole paragraphs. Each is a suggestion of a structural
     KIND through the same /suggest endpoint — and structural changes always
     WAIT for Damien (they never take the instant path), so every affordance
     says so plainly. Identity: refs carry durable block IDs, so a move never
     renumbers anything. */
  function structBaseOf(ref) { return (ref || '').replace(/(\.|#)b[0-9a-f]{8}$/, ''); }
  function sameFilePeers(s) {
    var base = structBaseOf(s.ref);
    return MAP.filter(function (b) {
      return b.kind === 'prose' && structBaseOf(b.source_ref) === base;
    }).sort(function (a, b) { return a.index - b.index; });
  }
  var STRUCT_LABELS = {
    insert_after: 'New paragraph', delete: 'Removal', split: 'Split',
    merge: 'Merge', move: 'Move'
  };

  function sendStructural(s, op, fields, onDone) {
    var body = { id: uuid(), source_ref: s.ref, op: op, original_hash: s.originalHash };
    if (fields) for (var k in fields) body[k] = fields[k];
    log('STRUCT ' + op + ' ref=' + s.ref);
    api('/suggest', { body: body }).then(function (out) {
      if (out.ok) {
        setLocalStatus(s, (STRUCT_LABELS[op] || 'Change') + ' — waiting for review', 'pending',
          'Structural changes always go to Damien first; the page updates once he approves.');
        if (onDone) onDone(true);
        repollPending();
      } else {
        var data = out.data || {};
        var code = (data.error && data.error.code) || data.code || 'network';
        if (code === 'no_edit_auth') {
          setLocalStatus(s, 'Your link needs a refresh', 'err'); showReauth(s);
        } else if (code === 'stale_page') {
          showNote(s, 'This page just updated — please reload and try again. Nothing was lost.');
        } else {
          showNote(s, 'That didn\u2019t send. Please try again in a moment.');
        }
        if (onDone) onDone(false);
        log('STRUCT error op=' + op + ' code=' + code);
      }
    });
  }

  /* Inline composer for “add a paragraph” — a plain box right where the new
     paragraph will appear, with one honest button. */
  function openComposer(s) {
    if (s._composer && s._composer.isConnected) {
      s._composer.querySelector('textarea').focus();
      return;
    }
    var c = el('div', 'eb-composer');
    var lab = el('div', 'eb-composer__label', 'New paragraph — it will appear after this one, once Damien approves it.');
    var ta = document.createElement('textarea');
    ta.className = 'eb-composer__text';
    ta.setAttribute('aria-label', 'Text of the new paragraph');
    ta.rows = 3;
    var row = el('div', 'eb-composer__row');
    var send = el('button', 'eb-composer__send', 'Send to Damien'); send.type = 'button';
    var cancel = el('button', 'eb-composer__cancel', 'Cancel'); cancel.type = 'button';
    row.appendChild(send); row.appendChild(cancel);
    c.appendChild(lab); c.appendChild(ta); c.appendChild(row);
    insertAfter(c, s._note || s.el);
    s._composer = c;
    var close = function () { if (c.parentNode) c.parentNode.removeChild(c); s._composer = null; };
    cancel.addEventListener('click', close);
    send.addEventListener('click', function () {
      var text = (ta.value || '').replace(/\s*\n+\s*/g, ' ').trim();
      if (!text) { ta.focus(); return; }
      send.disabled = true; cancel.disabled = true;
      sendStructural(s, 'insert_after', { new_text: text }, function (ok) {
        if (ok) close(); else { send.disabled = false; cancel.disabled = false; }
      });
    });
    setTimeout(function () { try { ta.focus(); } catch (e) {} }, 0);
  }

  /* Two-step inline confirm for “remove this paragraph” — destructive actions
     are plainly reversible and never one accidental tap (R9). */
  function openDeleteConfirm(s) {
    if (s._confirm && s._confirm.isConnected) return;
    var c = el('div', 'eb-confirm');
    c.appendChild(el('span', 'eb-confirm__msg',
      'Remove this paragraph? Damien reviews it first, and it can always be restored.'));
    var yes = el('button', 'eb-confirm__yes', 'Yes, ask to remove it'); yes.type = 'button';
    var no = el('button', 'eb-confirm__no', 'Keep it'); no.type = 'button';
    c.appendChild(yes); c.appendChild(no);
    insertAfter(c, s._note || s.el);
    s._confirm = c;
    var close = function () { if (c.parentNode) c.parentNode.removeChild(c); s._confirm = null; };
    no.addEventListener('click', close);
    yes.addEventListener('click', function () {
      yes.disabled = true; no.disabled = true;
      sendStructural(s, 'delete', null, function () { close(); });
    });
    no.focus();
  }

  /* U8: the scoped-change dialog — an editor describes a bigger change in
     their own words at a chosen scope. The blast radius is server-enumerated;
     above the ceiling the server asks once for plain-words confirmation. */
  var scopedDlg = null;
  function scopeOptions() {
    var opts = [];
    var m = PAGE.match(/^matters\/([^/]+)\//);
    if (m) {
      var slug = m[1];
      opts.push({ label: 'This matter\u2019s case file', level: 'part', matter: slug, part: 'case-file' });
      opts.push({ label: 'This matter\u2019s exercise', level: 'part', matter: slug, part: 'exercise' });
      opts.push({ label: 'This matter\u2019s business arrangements', level: 'part', matter: slug, part: 'business' });
      opts.push({ label: 'This whole matter', level: 'matter', matter: slug });
    }
    ['M1', 'M2', 'M3'].forEach(function (mod) {
      opts.push({ label: 'Module ' + mod.slice(1) + ' (across all its matters)', level: 'module', module: mod });
    });
    opts.push({ label: 'The whole course', level: 'course' });
    return opts;
  }
  function openScopedDialog() {
    if (scopedDlg && scopedDlg.isConnected) { scopedDlg.querySelector('textarea').focus(); return; }
    var d = el('div', 'eb-scoped');
    d.setAttribute('role', 'dialog'); d.setAttribute('aria-modal', 'true');
    d.setAttribute('aria-label', 'Ask for a bigger change');
    var inner = el('div', 'eb-scoped__inner');
    inner.appendChild(el('h2', 'eb-scoped__title', 'Ask for a bigger change'));
    inner.appendChild(el('p', 'eb-scoped__lede',
      'Say what you want changed, in your own words. The change is drafted for every place it applies, and Damien approves it before anything on the site changes.'));
    var lab1 = el('label', 'eb-scoped__label', 'Where should it apply?');
    var sel = document.createElement('select');
    sel.className = 'eb-scoped__scope';
    scopeOptions().forEach(function (o, i) {
      var op = document.createElement('option');
      op.value = String(i); op.textContent = o.label;
      sel.appendChild(op);
    });
    lab1.appendChild(sel);
    inner.appendChild(lab1);
    var lab2 = el('label', 'eb-scoped__label', 'What should change?');
    var ta = document.createElement('textarea');
    ta.className = 'eb-scoped__text'; ta.rows = 4;
    ta.setAttribute('placeholder', 'For example: Wherever the filing deadline of 14 days appears, change it to 30 days.');
    lab2.appendChild(ta);
    inner.appendChild(lab2);
    var status = el('p', 'eb-scoped__status', '');
    inner.appendChild(status);
    var row = el('div', 'eb-scoped__row');
    var send = el('button', 'eb-scoped__send', 'Send to Damien'); send.type = 'button';
    var cancel = el('button', 'eb-scoped__cancel', 'Cancel'); cancel.type = 'button';
    row.appendChild(send); row.appendChild(cancel);
    inner.appendChild(row);
    d.appendChild(inner);
    document.body.appendChild(d);
    scopedDlg = d;
    var draft = loadDialogDraft(SCOPED_DRAFT_KEY);
    var reqId = draft && draft.id ? draft.id : uuid();
    var confirmed = false;
    var confirmationToken = null;
    if (draft) {
      ta.value = draft.instruction || '';
      if (/^\d+$/.test(String(draft.scope_index)) &&
          parseInt(draft.scope_index, 10) < sel.options.length) {
        sel.value = String(draft.scope_index);
      }
    }
    var persist = function () {
      saveDialogDraft(SCOPED_DRAFT_KEY, {
        id: reqId, scope_index: sel.value, instruction: ta.value
      });
    };
    if (draft) persist(); // refresh the durable mirror; never restore confirmation
    // A confirmation is for the radius the editor was SHOWN. Change the scope
    // or the wording and that number is stale — carrying confirmed:true across
    // the edit would file a course-wide change on a module-sized "yes".
    var resetConfirmation = function () {
      if (!confirmed && !confirmationToken) return;
      confirmed = false;
      confirmationToken = null;
      reqId = uuid();
      send.textContent = 'Send to Damien';
      status.textContent = '';
      persist();
    };
    var closeTimer = null;
    var close = function () {
      if (closeTimer) { clearTimeout(closeTimer); closeTimer = null; }
      if (d.parentNode) d.parentNode.removeChild(d);
      if (scopedDlg === d) scopedDlg = null;   // a stale timer must not blind the guard
    };
    cancel.addEventListener('click', function () {
      if (!send.disabled) { clearDialogDraft(SCOPED_DRAFT_KEY); close(); }
    });
    d.addEventListener('keydown', function (e) {
      if (e.key === 'Escape' && !send.disabled) close();
    });
    send.addEventListener('click', function () {
      var text = (ta.value || '').trim();
      if (!text) { ta.focus(); return; }
      var o = scopeOptions()[parseInt(sel.value, 10) || 0];
      var body = { id: reqId, level: o.level, instruction: text };
      if (o.matter) body.matter = o.matter;
      if (o.part) body.part = o.part;
      if (o.module) body.module = o.module;
      if (confirmed) {
        body.confirmed = true;
        if (confirmationToken) body.confirmation_token = confirmationToken;
      }
      send.disabled = true;
      status.textContent = 'Sending\u2026';
      api('/scoped-request', { body: body }).then(function (out) {
        if (out.ok) {
          clearDialogDraft(SCOPED_DRAFT_KEY);
          status.textContent = 'Sent. Damien will review the drafted change before anything on the site changes.';
          send.textContent = 'Sent \u2713';
          closeTimer = setTimeout(close, 3500);
          return;
        }
        var data = out.data || {};
        var code = (data.error && data.error.code) || data.code;
        if (code === 'ceiling_confirmation_required') {
          var r = data.radius || {};
          confirmed = true;
          confirmationToken = data.confirmation_token || null;
          send.disabled = false;
          send.textContent = 'Yes \u2014 that wide. Send it';
          status.textContent = 'That change would touch ' + (r.blocks || '?') +
            ' paragraphs across ' + (r.matters || '?') +
            ' matter(s). If you mean it that widely, press the button again.';
          return;
        }
        send.disabled = false;
        status.textContent = 'That didn\u2019t send. Please try again in a moment.';
        log('SCOPED error code=' + code);
      });
    });
    sel.addEventListener('change', function () { resetConfirmation(); persist(); });
    ta.addEventListener('input', function () { resetConfirmation(); persist(); });
    setTimeout(function () { try { ta.focus(); } catch (e) {} }, 0);
  }

  /* U8: "Add a fact" on the per-matter Facts page (U5/OQ5). */
  function factsMatterSlug() {
    var m = PAGE.match(/^matters\/([^/]+)\/facts\//);
    return m ? m[1] : null;
  }
  function buildAddFact() {
    var slug = factsMatterSlug();
    if (!slug) return;
    var main = document.querySelector('main');
    if (!main) return;
    var box = el('div', 'eb-composer');
    box.appendChild(el('div', 'eb-composer__label',
      'Add a fact — it is recorded here first, and appears in the prose only through drafted mentions Damien approves.'));
    var name = document.createElement('input');
    name.type = 'text'; name.className = 'eb-addfact__name';
    name.setAttribute('aria-label', 'Short name for the fact');
    name.setAttribute('placeholder', 'short-name (letters, digits, dashes)');
    var ta = document.createElement('textarea');
    ta.className = 'eb-composer__text'; ta.rows = 2;
    ta.setAttribute('aria-label', 'The fact itself');
    ta.setAttribute('placeholder', 'The fact itself \u2014 e.g. The response deadline is 21 days.');
    var row = el('div', 'eb-composer__row');
    var send = el('button', 'eb-composer__send', 'Add this fact'); send.type = 'button';
    var status = el('span', 'eb-scoped__status', '');
    // ONE id per fact being composed, rotated only after it lands. Minting a
    // fresh uuid per click makes a retry-after-failure file a SECOND fact
    // instead of replaying the first — the idempotency discipline the rest of
    // this client already keeps (see sendSuggestion's post-success rotation).
    var draft = loadDialogDraft(FACT_DRAFT_KEY);
    var factId = draft && draft.id ? draft.id : uuid();
    if (draft) {
      name.value = draft.name || '';
      ta.value = draft.text || '';
    }
    var persist = function () {
      saveDialogDraft(FACT_DRAFT_KEY, { id: factId, name: name.value, text: ta.value });
    };
    name.addEventListener('input', persist);
    ta.addEventListener('input', persist);
    row.appendChild(send); row.appendChild(status);
    box.appendChild(name); box.appendChild(ta); box.appendChild(row);
    main.appendChild(box);
    send.addEventListener('click', function () {
      var key = (name.value || '').trim().toLowerCase().replace(/[^a-z0-9_-]+/g, '-').replace(/^-+|-+$/g, '');
      var text = (ta.value || '').trim();
      if (!key) { name.focus(); return; }
      if (!text) { ta.focus(); return; }
      send.disabled = true;
      api('/suggest', { body: { id: factId, op: 'json_add', matter: slug,
                                fact_key: key, new_text: text } }).then(function (out) {
        if (out.ok) {
          clearDialogDraft(FACT_DRAFT_KEY);
          status.textContent = 'Added \u2014 waiting for review.';
          name.value = ''; ta.value = ''; send.disabled = false;
          factId = uuid();          // landed: the next fact is a new intent
        } else {
          send.disabled = false;    // same id: a retry REPLAYS, never duplicates
          status.textContent = 'That didn\u2019t save \u2014 check the short name and try again.';
        }
      });
    });
  }

  function requestMove(s, dir) {
    var peers = sameFilePeers(s);
    var i = -1;
    for (var k = 0; k < peers.length; k++) if (peers[k].source_ref === s.ref) { i = k; break; }
    if (i < 0) return;
    var dest = null;
    if (dir > 0 && i + 1 < peers.length) dest = peers[i + 1].source_ref;
    if (dir < 0 && i >= 2) dest = peers[i - 2].source_ref;
    if (!dest) return;
    sendStructural(s, 'move', { op_arg: dest }, null);
  }

  function actButton(s, kind, label, paths, hint, onActivate) {
    var b = el('button', 'eb-act eb-act--' + kind); b.type = 'button';
    b.setAttribute('aria-label', label);
    if (hint) b.setAttribute('title', label + ' — ' + hint);
    b.appendChild(iconSvg(paths));
    // The word label rides along, revealed on hover/focus. Screen readers get it
    // from aria-label, so the visible copy is marked hidden to avoid a double read.
    var tip = el('span', 'eb-act__label', label); tip.setAttribute('aria-hidden', 'true');
    b.appendChild(tip);
    // mousedown+preventDefault preserves the caret/selection contract the rest of
    // the client relies on; the detail===0 click is the keyboard path.
    b.addEventListener('mousedown', function (e) { e.preventDefault(); onActivate(); });
    b.addEventListener('click', function (e) { if (e.detail === 0) onActivate(); });
    // Point at what you are about to act on: hovering or tabbing to the control
    // lights up its paragraph, so there is never doubt which one it belongs to.
    var mark = function () { s.el.classList.add('eb--target'); };
    var unmark = function () { s.el.classList.remove('eb--target'); };
    b.addEventListener('mouseenter', mark);
    b.addEventListener('focus', mark);
    b.addEventListener('mouseleave', unmark);
    b.addEventListener('blur', unmark);
    return b;
  }

  /* ---------- rail placement: MEASURED, never assumed ----------------------
     The rail belongs beside its passage, out of the reading column. The first
     two attempts decided that with a media query — "wider than 1100px, therefore
     there is a gutter" — which is not the same question. A page whose content
     column runs nearly to the window edge is wide AND has no gutter, and the
     controls landed on top of the words (Damien caught it twice: once over a
     section heading, once mid-sentence with the status pill trailing behind).

     So measure the actual free space to the right of THIS block, and only move
     out there if the rail genuinely fits. The move is a transform, not a margin:
     transforms do not participate in layout, so the rail can be zero-height (no
     vertical cost) and positioned relative to itself — no dependence on which
     ancestor happens to be positioned, which is what made the margin approach
     fragile in a flex container.

     Anything that can change the geometry re-runs this: resize, large type,
     fonts finishing, the comment panel pushing the page over, and every pending
     re-render (a status pill changes the rail's width). */
  var RAIL_GAP = 16;          // breathing space between the column and the controls
  var railLayoutQueued = false;
  var railLayer = null;

  /* Why an overlay layer, after three attempts at doing this in flow.
     Each earlier version injected the rail into the page's own layout and then
     tried to nudge it clear with margins or a transform. Every one of them found
     a new collision the last had not: inside a flex heading the rail collapsed to
     content width and sat on the words; aligned to a block's own right edge it
     landed on the lede printed beside a short caption; in flow before a block it
     dropped into the chips row and covered "MERIDIAN".

     The page's markup is generated and varied, and the rail is not part of it.
     So it stops pretending to be: on any layout with a real gutter the rails live
     in one absolutely-positioned layer in DOCUMENT coordinates, outside the flow
     entirely. Nothing to collide with, no containing-block guesswork, no vertical
     cost, and every control lines up on one column edge. Where there is no gutter
     (phones, narrow windows) they return to the flow AFTER their block, which is
     the one arrangement that has never overlapped anything. */
  function ensureRailLayer() {
    if (railLayer && railLayer.isConnected) return railLayer;
    railLayer = el('div', 'eb-rail-layer');
    railLayer.setAttribute('aria-hidden', 'false');
    document.body.appendChild(railLayer);
    return railLayer;
  }

  function railContentWidth(t) {
    var kids = t.children;
    if (!kids.length) return 0;
    var first = kids[0].getBoundingClientRect();
    var last = kids[kids.length - 1].getBoundingClientRect();
    return Math.max(last.right - first.left, 0);
  }

  // No room beside the column (a phone, a narrow window). The controls do NOT
  // disappear — on touch there is no hover to reveal them and a control nobody
  // can find is a control nobody uses, which is the whole reason they are drawn
  // at rest. They return to the flow immediately AFTER their block: the original
  // arrangement, and the only one that has never collided with anything. It
  // costs a row, which on a phone is the right trade.
  function railToFlow(s, t) {
    t.classList.remove('eb-tools--gutter', 'eb-tools--offstage');
    t.style.position = ''; t.style.left = ''; t.style.top = ''; t.style.maxWidth = '';
    if (s.el.nextElementSibling !== t) insertAfter(t, s.el);
  }

  function layoutRails() {
    railLayoutQueued = false;
    var refs = Object.keys(sessions);
    if (!refs.length) return;
    var docW = document.documentElement.clientWidth;
    var layer = ensureRailLayer();

    // ONE column edge for the page, taken from the widest block. A per-block edge
    // puts controls just past a short caption — which is where the lede beside it
    // is already printed.
    var columnRight = 0;
    refs.forEach(function (ref) {
      var s = sessions[ref];
      if (!s.el || !s.el.isConnected) return;
      var r = s.el.getBoundingClientRect();
      if (r.width > 0 && r.right > columnRight) columnRight = r.right;
    });
    if (!columnRight) return;

    var room = docW - columnRight - RAIL_GAP;
    var sx = window.scrollX || window.pageXOffset || 0;
    var sy = window.scrollY || window.pageYOffset || 0;
    var placed = 0;

    refs.forEach(function (ref) {
      var s = sessions[ref];
      var t = s._tools;
      if (!t || !s.el || !s.el.isConnected) return;
      t.classList.remove('eb-tools--offstage');      // measurable before deciding
      var b = s.el.getBoundingClientRect();
      var w = railContentWidth(t);
      // PER RAIL: deciding once from the widest rail sent all 182 controls
      // offstage on a layout with a 400px gutter, because one status pill was long.
      if (!b.width || !w || w > room) { railToFlow(s, t); return; }
      // Gutter rails live in the overlay layer — never in the page's flow, so
      // they cannot disturb or collide with the generated markup.
      if (t.parentNode !== layer) layer.appendChild(t);
      t.classList.add('eb-tools--gutter');
      t.style.position = 'absolute';
      t.style.left = Math.round(columnRight + RAIL_GAP + sx) + 'px';
      t.style.top = Math.round(b.top + sy) + 'px';   // the passage's first line
      // CLAMP to the measured room: a hovered word-label grows its button, and
      // with six verbs on a rail that growth can run past the viewport edge
      // (caught at 900px — by the tester's own resting cursor). The gutter rail
      // may never paint wider than the gutter; a label that cannot fit clips
      // (its full words remain in the title + aria-label).
      t.style.maxWidth = Math.floor(room) + 'px';
      // TRUST THE PAINT, not the arithmetic: measure the rail where it now
      // actually sits. Rounding + scrollbar width can put a rail that "fit" a
      // few pixels past the edge — if any part is past the viewport, it
      // returns to the flow.
      var rr = t.getBoundingClientRect();
      if (Math.round(rr.right) > docW) { railToFlow(s, t); return; }
      placed++;
    });
    document.documentElement.classList.toggle('eb-rails-offstage', placed === 0);
  }

  function scheduleRailLayout() {
    if (railLayoutQueued) return;
    railLayoutQueued = true;
    (window.requestAnimationFrame || setTimeout)(layoutRails, 0);
  }

  function toolsEl(s) {
    if (s._tools) return s._tools;
    var t = el('div', 'eb-tools');
    if (s.editable) {
      t.appendChild(actButton(s, 'edit', 'Edit', ICON_PENCIL,
        'change the wording here', function () { makeEditable(s, true); }));
    }
    // Comment-only blocks (numbers, formatted lines) used to carry a standing
    // beige explainer under every one of them. The explanation now travels with
    // the control that can act on it, and appears in the comment panel itself.
    var why = s.commentOnly
      ? 'this line is not edited directly — say what it should read and Damien applies it'
      : 'leave a note for Damien';
    t.appendChild(actButton(s, 'comment', 'Comment', ICON_COMMENT, why,
      function () { openBubbleWhole(s); }));
    // Structural verbs (U4): whole-paragraph add / remove / move, prose only.
    // Every one goes to Damien for review — the affordances say so themselves.
    if (s.editable && s.kind === 'prose') {
      t.appendChild(actButton(s, 'add', 'Add paragraph', ICON_PLUS,
        'write a new paragraph after this one (Damien approves it first)',
        function () { openComposer(s); }));
      t.appendChild(actButton(s, 'remove', 'Remove', ICON_TRASH,
        'ask to remove this paragraph (Damien approves; always restorable)',
        function () { openDeleteConfirm(s); }));
      var peers = sameFilePeers(s), pi = -1;
      for (var pk = 0; pk < peers.length; pk++) {
        if (peers[pk].source_ref === s.ref) { pi = pk; break; }
      }
      if (pi >= 2) {
        t.appendChild(actButton(s, 'up', 'Move up', ICON_UP,
          'move this paragraph one place earlier (Damien approves it first)',
          function () { requestMove(s, -1); }));
      }
      if (pi >= 0 && pi + 1 < peers.length) {
        t.appendChild(actButton(s, 'down', 'Move down', ICON_DOWN,
          'move this paragraph one place later (Damien approves it first)',
          function () { requestMove(s, 1); }));
      }
    }
    // The rail is inserted BEFORE its block and pulled back down over it, so it
    // aligns to the block's FIRST line. Sitting after the block aligned it to the
    // LAST line, which meant the controls landed at a different offset for every
    // paragraph — in a numbered activity list they scattered down the margin with
    // no visible relationship to the items they belonged to. Paragraph TOPS are
    // evenly spaced; bottoms are not.
    t.setAttribute('data-eb-for', String(s.index));
    s.el.parentNode.insertBefore(t, s.el);
    // Approaching the passage raises its controls (the CSS sibling selector can
    // no longer reach forward, and this is deterministic across browsers).
    s.el.addEventListener('mouseenter', function () { t.classList.add('eb-tools--near'); });
    s.el.addEventListener('mouseleave', function () { t.classList.remove('eb-tools--near'); });
    s._tools = t;
    return t;
  }
  function insertAfter(node, ref) { ref.parentNode.insertBefore(node, ref.nextSibling); }

  function noteEl(s) {
    if (s._note) return s._note;
    var n = el('div', 'eb-note'); n.setAttribute('role', 'status');
    insertAfter(n, s.el);
    s._note = n; return n;
  }
  function showNote(s, msg) { var n = noteEl(s); n.textContent = msg; n.classList.add('show'); }
  function hideNote(s) { if (s._note) s._note.classList.remove('show'); }

  function statusPill(s) {
    if (s._status) return s._status;
    var p = el('span', 'eb-status'); p.setAttribute('aria-live', 'polite');
    (s._tools || toolsEl(s)).appendChild(p);
    s._status = p; return p;
  }
  function setStatusPill(p, msg, cls, longMsg) {
    p.textContent = msg || '';
    p.className = 'eb-status' + (cls ? ' eb-status--' + cls : '');
    if (longMsg) p.setAttribute('title', longMsg); else p.removeAttribute('title');
    scheduleRailLayout();          // the pill just changed the rail's width
  }
  function setLocalStatus(s, msg, cls, longMsg) {
    setStatusPill(statusPill(s), msg, cls, longMsg);
  }

  /* ---------- re-auth affordance (401, never a raw 4xx) -------------------- */
  function reauthEl(s) {
    if (s._reauth) return s._reauth;
    var box = el('div', 'eb-reauth');
    var p = el('p', null, 'Your editing link needs a refresh before this can be saved. Your words are safe here — press the button and we will try sending again.');
    var btn = el('button', 'btn', 'Refresh & send again'); btn.type = 'button';
    btn.addEventListener('mousedown', function (e) { e.preventDefault(); hideReauth(s); sendSuggestion(s); });
    btn.addEventListener('click', function (e) { if (e.detail === 0) { hideReauth(s); sendSuggestion(s); } });
    box.appendChild(p); box.appendChild(btn);
    insertAfter(box, s._note || s.el);
    s._reauth = box; return box;
  }
  function showReauth(s) { reauthEl(s).classList.add('show'); }
  function hideReauth(s) { if (s._reauth) s._reauth.classList.remove('show'); }

  /* ============================================================================
     5. Wiring — three verbs, one event each  (LIFTED from spike-blur-save)
     ============================================================================ */
  function wireBlock(s) {
    var eb = s.el;
    eb.classList.add('eb');
    eb.classList.add(s.editable ? 'eb--editable' : 'eb--comment-only');
    eb.setAttribute('data-eb-index', String(s.index));
    if (s.shared) {
      eb.classList.add('eb--shared');
      eb.setAttribute('data-eb-shared', 'true');
      eb.setAttribute('title', 'Shared text — editing it can change more than one page');
    }

    toolsEl(s);

    // json_scalar + inline-formatted blocks stay comment-only in v1. The standing
    // explainer under every such block was pure noise on a page with dozens of
    // them — the same sentence, over and over, addressed to nobody in particular.
    // It now surfaces where it is actually needed: on the Comment control's
    // tooltip, and at the top of the comment panel once you open it.

    if (!s.editable) return;   // comment-only blocks need no edit wiring

    /* R7: NEUTRALIZE origin-page handlers inside the edit region (capture-phase
       stopPropagation) so scroll-spy / segmented toggles can never fight the
       caret while this block is being edited. Only swallow while EDITING. */
    var neutralize = function (e) { if (s.state !== ST.IDLE) { e.stopPropagation(); } };
    ['mousedown', 'mouseup', 'click', 'keydown', 'wheel', 'touchstart', 'pointerdown']
      .forEach(function (t) { eb.addEventListener(t, neutralize, true); });

    /* click-to-edit-in-place: a plain click (no active selection) on the block
       enters edit. A drag-selection is left alone so it can become a comment. */
    eb.addEventListener('click', function () {
      if (s.state !== ST.IDLE) return;
      var sel = window.getSelection();
      if (sel && !sel.isCollapsed && sel.toString().trim()) return;   // selecting -> comment path
      makeEditable(s, true);
    });

    /* focus => enter EDIT session; mint suggestion_id ONCE (R2) */
    eb.addEventListener('focus', function () { enterEdit(s); });

    /* every input => update SNAPSHOT + flush draft (R3 snapshot, R1 draft) */
    eb.addEventListener('input', function () {
      if (s.state === ST.IDLE) enterEdit(s);
      if (!s.suggestionId) s.suggestionId = uuid();     // R2 mint-once guard
      s.snapshot = eb.textContent;                      // snapshot maintained continuously
      s.dirty = normalize(s.snapshot) !== normalize(s.originalText);
      eb.classList.toggle('dirty', s.dirty);
      saveDraft(s);                                     // R1: flush-to-DRAFT only
      setLocalStatus(s, s.dirty ? 'Draft saved — not sent yet' : '', s.dirty ? 'draft' : null);
      if (activeRef === s.ref) syncBar(s);
      scheduleAutoSave(s);                              // debounced auto-send (~2.5s)
    });

    /* blur => flush-to-draft ONLY. Inert: never commit, never discard (R1/R6). */
    eb.addEventListener('blur', function () {
      if (s.dirty) saveDraft(s);
      log('BLUR (draft-only, inert) ref=' + s.ref);
    });
  }

  function makeEditable(s, focus) {
    if (!s.editable) return;
    try { s.el.setAttribute('contenteditable', 'plaintext-only'); } catch (e) { s.el.setAttribute('contenteditable', 'true'); }
    s.el.setAttribute('role', 'textbox');
    s.el.setAttribute('aria-label', 'Editable paragraph' + (s.kind ? '' : ''));
    if (focus) { try { s.el.focus(); } catch (e) {} enterEdit(s); }
  }

  function enterEdit(s) {
    if (s.state !== ST.IDLE) { activeRef = s.ref; showBar(s); return; }
    activeRef = s.ref;
    s.state = ST.EDITING;
    s.el.classList.add('editing');
    if (!s.suggestionId) s.suggestionId = uuid();   // R2 mint ONCE per edit-session
    hideNote(s); hideReauth(s);
    reconcileDraft(s);                              // R8 re-poll on (re)entry
    showBar(s);
    log('EDIT enter ref=' + s.ref + ' id=' + (s.suggestionId || '').slice(0, 8));
  }

  /* ---------- AUTO-SAVE scheduling (debounce + backoff) -------------------- */
  function scheduleAutoSave(s) {
    if (!s.editable) return;
    if (s._debounce) { clearTimeout(s._debounce); s._debounce = null; }
    if (!s.dirty) return;
    s._debounce = setTimeout(function () { s._debounce = null; autoSaveFire(s); }, AUTOSAVE_MS);
  }
  function autoSaveFire(s) {
    if (!s.dirty) return;
    if (s.state === ST.SAVING) { s._queued = true; return; }   // one in-flight per block
    // Re-auth is an explicit user action — never auto-resend behind a shown prompt.
    if (s._reauth && s._reauth.classList.contains('show')) return;
    sendSuggestion(s, { auto: true });
  }
  function armRetry(s) {
    s._retryDelay = s._retryDelay ? Math.min(s._retryDelay * 2, RETRY_MAX_MS) : RETRY_BASE_MS;
    if (s._retry) clearTimeout(s._retry);
    s._retry = setTimeout(function () {
      s._retry = null;
      if (s.dirty && s.state !== ST.SAVING) sendSuggestion(s, { auto: true });
    }, s._retryDelay);
    log('RETRY armed ref=' + s.ref + ' in ' + s._retryDelay + 'ms');
  }
  function clearTimers(s) {
    if (s._debounce) { clearTimeout(s._debounce); s._debounce = null; }
    if (s._retry) { clearTimeout(s._retry); s._retry = null; }
    s._retryDelay = 0; s._queued = false;
  }

  /* ---------- COMMIT: the synchronous critical section BEFORE any await -----
     opts.auto = the debounced auto-send (keeps the block editable so typing flows
     on); no opts = the explicit bar Save (exits edit on success, as before). The
     client NEVER invents a "saved" status — the pill reflects the store's status
     returned by the endpoint (and re-poll). The idempotency id is rotated on
     success so the next burst is a distinct request. */
  function sendSuggestion(s, opts) {
    opts = opts || {};
    if (s.state === ST.SAVING) return;              // in-flight guard (dedupe layer 1)
    if (!s.dirty) { if (!opts.auto) setLocalStatus(s, 'No change to send', null); return; }
    // A shared leaf remains one edit session and one suggestion. Its reach is a
    // choice made before the critical section, never client-side fan-out.
    if (s.shared && !opts.sharedEverywhere) {
      openSharedReachDialog(s);
      return;
    }
    clearTimeout(s._debounce); s._debounce = null;  // a send is happening now
    // ---- synchronous critical section (chat.js §Input; spike R3/R4) ----
    s.state = ST.SAVING;
    barDisable(true);                               // sync disable BEFORE await (R3/R4)
    setLocalStatus(s, 'Saving…', 'sending');
    setBarStatus('Saving your change…');
    var id = s.suggestionId || (s.suggestionId = uuid());
    var textAtClick = s.snapshot;                   // R3: read SNAPSHOT, not live DOM
    // Worker contract: server derives editor/original_text/kind/page/map_version
    // and IGNORES client values for those. Send only { id, source_ref,
    // json_path?, new_text, original_hash }.
    var payload = { id: id, source_ref: s.ref, new_text: textAtClick,
      original_hash: s.originalHash, page: PAGE };
    if (s.jsonPath) payload.json_path = s.jsonPath;
    log('SEND ref=' + s.ref + ' id=' + id.slice(0, 8) + ' state=SAVING (disabled)' + (opts.auto ? ' auto' : ''));

    api('/suggest', { body: payload }).then(function (out) {
      if (out.ok) {
        if (s._retry) { clearTimeout(s._retry); s._retry = null; }
        s._retryDelay = 0;
        clearDraft(s);
        s.originalText = textAtClick;               // the sent text is now the baseline
        s.originalHtml = htmlForText(textAtClick);  // keep rollback DOM in step with it
        s.suggestionId = null;                      // rotate: next burst mints a fresh id
        // Honest status from the STORE (accepted "Going live…" under DIRECT_APPLY,
        // else "Pending review") — never an optimistic "Saved".
        var srv = (out.data && out.data.status) || 'pending';
        var lbl = STATUS_LABELS[srv] || 'Sent';
        var tone = STATUS_TONE[srv] || 'sending';
        // Did the user type MORE while this send was in flight?
        var stillDirty = normalize(s.snapshot) !== normalize(s.originalText);
        s.dirty = stillDirty;
        if (opts.auto) {
          // keep the block editable so typing continues seamlessly
          s.state = ST.EDITING; barDisable(false);
          if (stillDirty) {
            s.suggestionId = uuid();                // fresh id for the continued burst
            setLocalStatus(s, 'Draft saved — not sent yet', 'draft');
            saveDraft(s); scheduleAutoSave(s);
          } else {
            s.el.classList.remove('dirty');
            setLocalStatus(s, lbl + (s._attr ? ' · ' + s._attr : ''), tone);
          }
        } else {
          // explicit bar Save: commit + exit edit (unchanged UX)
          s.state = ST.IDLE;
          s.el.classList.remove('editing', 'dirty');
          s.el.removeAttribute('contenteditable');
          setLocalStatus(s, lbl, tone);
          hidePreview(); hideBar();
        }
        s._queued = false;
        log('SENT ref=' + s.ref + ' status=' + srv + (opts.auto ? ' (kept editing)' : ' (id cleared)'));
        repollPending();                            // pull canonical server status
      } else {
        handleSendError(s, out, opts);
      }
    });
  }

  /* ---------- SHARED REACH: one leaf, an explicit scope decision (U4) ----- */
  var sharedReachDlg = null;
  function titleWords(value) {
    return String(value || '').replace(/[-_]+/g, ' ').replace(/\b\w/g, function (c) { return c.toUpperCase(); });
  }
  function pageLabel(page) {
    var parts = String(page || '').replace(/\/index\.html$/, '').split('/').filter(Boolean);
    if (parts[0] === 'modules' && parts[1]) return 'Module ' + parts[1].replace(/^m/i, '');
    if (parts[0] === 'matters' && parts[1]) {
      var matter = titleWords(parts[1].replace(/^m\d+-/, ''));
      return matter + (parts[2] ? ' · ' + titleWords(parts.slice(2).join(' · ')) : ' · Overview');
    }
    return parts.length ? parts.map(titleWords).join(' · ') : String(page || 'another page');
  }
  function otherOccurrences(s) {
    return s.occurrences.filter(function (o) { return o && o.page && o.page !== PAGE; });
  }
  function closeSharedReachDialog() {
    if (!sharedReachDlg) return;
    var trigger = sharedReachDlg._trigger;
    if (sharedReachDlg.parentNode) sharedReachDlg.parentNode.removeChild(sharedReachDlg);
    sharedReachDlg = null;
    if (trigger) { try { trigger.focus(); } catch (e) {} }
  }
  function requestPageOverride(s) {
    if (s.state === ST.SAVING) return;
    var textAtClick = s.snapshot;
    clearTimeout(s._debounce); s._debounce = null;
    s.state = ST.SAVING;
    // Freeze the editable surface before the request leaves. This prevents a
    // keystroke during the response window from being cleared as if it were
    // part of the page-only copy that was just sent.
    s.el.removeAttribute('contenteditable');
    barDisable(true);
    setLocalStatus(s, 'Saving this page’s copy…', 'sending');
    setBarStatus('Creating a page-only authored copy…');
    var id = s.suggestionId || (s.suggestionId = uuid());
    api('/suggest', { body: {
      id: id, source_ref: s.ref, new_text: textAtClick,
      original_hash: s.originalHash, op: 'page_override', page: PAGE
    }}).then(function (out) {
      if (!out.ok) { handleSendError(s, out, { pageOverride: true }); return; }
      clearDraft(s); s.originalText = textAtClick; s.originalHtml = htmlForText(textAtClick); s.suggestionId = null;
      var stillDirty = normalize(s.snapshot) !== normalize(textAtClick);
      s.dirty = stillDirty;
      if (stillDirty) {
        s.state = ST.EDITING; s.suggestionId = uuid(); barDisable(false);
        saveDraft(s); s.el.classList.add('editing', 'dirty');
        setLocalStatus(s, 'Page-only copy recorded; newer draft not sent', 'draft');
        setBarStatus('This page’s copy was recorded. Save again to send your newer wording.');
        repollPending(); return;
      }
      s.state = ST.IDLE;
      s.el.classList.remove('editing', 'dirty'); s.el.removeAttribute('contenteditable');
      setLocalStatus(s, 'Page-only copy recorded', 'pending');
      setBarStatus('This page now has its own authored copy.');
      hideBar(); repollPending();
    });
  }

  function buildOverridesView() {
    if (!PAGE_OVERRIDES.length) return;
    var details = el('details', 'eb-overrides');
    var summary = el('summary', 'eb-overrides__summary',
      'Page-only copies (' + PAGE_OVERRIDES.length + ')');
    details.appendChild(summary);
    PAGE_OVERRIDES.forEach(function (record) {
      var row = el('div', 'eb-overrides__row');
      row.appendChild(el('span', 'eb-overrides__text',
        record.value + ' · by ' + record.deliberate_by + ' · ' + record.deliberate_at));
      var revert = el('button', 'btn btn--ghost', 'Return to shared text'); revert.type = 'button';
      revert.addEventListener('click', function () {
        var block = MAP.find(function (b) { return b.source_ref === record.source_ref; });
        if (!block) { setBarStatus('Reload before returning this copy to shared text.'); return; }
        api('/suggest', { body: { id: uuid(), source_ref: record.source_ref,
          original_hash: block.original_hash, op: 'page_override_revert', page: PAGE }}).then(function (out) {
          if (!out.ok) { setBarStatus('That page-only copy could not be reverted.'); return; }
          row.remove(); setBarStatus('The page will return to shared text when this change is applied.');
        });
      });
      row.appendChild(revert); details.appendChild(row);
    });
    document.body.appendChild(details);
  }
  function openSharedReachDialog(s) {
    if (sharedReachDlg && sharedReachDlg.isConnected) return;
    var others = otherOccurrences(s);
    var d = el('div', 'eb-shared-dialog');
    d.setAttribute('role', 'dialog'); d.setAttribute('aria-modal', 'true');
    d.setAttribute('aria-label', 'Choose where this shared edit applies');
    var inner = el('div', 'eb-shared-dialog__inner');
    inner.appendChild(el('h2', 'eb-shared-dialog__title', 'This text appears on more than one page'));
    inner.appendChild(el('p', 'eb-shared-dialog__lede',
      'Changing it everywhere will also update ' + (others.length === 1 ? 'this page:' : 'these pages:')));
    var list = el('ul', 'eb-shared-dialog__pages');
    others.forEach(function (o) { list.appendChild(el('li', '', pageLabel(o.page))); });
    inner.appendChild(list);
    var row = el('div', 'eb-shared-dialog__row');
    var everywhere = el('button', 'eb-shared-dialog__everywhere', 'Change everywhere'); everywhere.type = 'button';
    var here = el('button', 'eb-shared-dialog__here', 'Change this page only'); here.type = 'button';
    var cancel = el('button', 'eb-shared-dialog__cancel', 'Keep editing'); cancel.type = 'button';
    row.appendChild(everywhere); row.appendChild(here); row.appendChild(cancel);
    inner.appendChild(row); d.appendChild(inner); document.body.appendChild(d);
    d._session = s; d._trigger = barSave || s.el; sharedReachDlg = d;
    everywhere.addEventListener('click', function () { closeSharedReachDialog(); sendSuggestion(s, { sharedEverywhere: true }); });
    here.addEventListener('click', function () { closeSharedReachDialog(); requestPageOverride(s); });
    cancel.addEventListener('click', closeSharedReachDialog);
    d.addEventListener('keydown', function (e) { if (e.key === 'Escape') closeSharedReachDialog(); });
    setTimeout(function () { try { everywhere.focus(); } catch (e) {} }, 0);
  }

  function handleSendError(s, out, opts) {
    opts = opts || {};
    var data = out.data || {};
    // Read data.error.code FIRST (authoritative), fall back to the HTTP-status map.
    var code = (data.error && data.error.code) || data.code ||
      (out.status === 401 ? 'no_edit_auth' : out.status === 429 ? 'rate_limited' :
       out.status === 413 ? 'validation_error' : (out.status === 409 || out.status === 410) ? 'stale_page' : 'network');
    if (opts.pageOverride && code !== 'stale_page') makeEditable(s, false);

    if (code === 'id_conflict') {
      // The server saw this idempotency id with a DIFFERENT payload (rotation raced
      // a replay). Rotate to a fresh id and resend — the newer edit must land, never
      // be swallowed. Keep the draft + dirty so nothing is lost.
      s.state = ST.EDITING; barDisable(false);
      s.suggestionId = uuid();
      saveDraft(s);
      if (opts.pageOverride) {
        setLocalStatus(s, 'Not sent — choose this page again', 'err');
        setBarStatus('The request conflicted with an earlier send. Save again and choose this page only.');
        return;
      }
      log('SEND id_conflict ref=' + s.ref + ' -> rotated id, resending');
      sendSuggestion(s, { auto: true });
      return;
    }
    if (code === 'no_edit_auth') {
      // R5: preserve draft + id; friendly re-auth; resend reuses the same id.
      s.state = ST.EDITING; barDisable(false);
      saveDraft(s);
      setLocalStatus(s, 'Your link needs a refresh', 'err');
      setBarStatus('Your editing link needs a refresh — press “Refresh & send again”.');
      showReauth(s);
      log('SEND no_edit_auth ref=' + s.ref + ' (draft+id preserved id=' + (s.suggestionId || '').slice(0, 8) + ')');
      return;
    }
    if (code === 'rate_limited') {
      s.state = ST.EDITING; barDisable(false);
      saveDraft(s);
      setLocalStatus(s, 'Please wait a moment', 'err');
      setBarStatus('You are sending changes faster than the inbox can take them. Wait a moment, then press Save again.');
      log('SEND rate_limited ref=' + s.ref);
      return;
    }
    if (code === 'validation_error') {
      // 413 oversize — graceful large-type message, draft preserved.
      s.state = ST.EDITING; barDisable(false);
      saveDraft(s);
      setLocalStatus(s, 'Too long to send', 'err');
      setBarStatus('That change is longer than the editor can send in one piece. Please shorten it a little, then press Save again.');
      log('SEND validation_error(413) ref=' + s.ref);
      return;
    }
    if (code === 'stale_page') {
      // reload THIS block: the source moved under John — discard draft gently.
      s.state = ST.IDLE; barDisable(false);
      clearDraft(s); s.suggestionId = null; s.snapshot = s.originalText; s.dirty = false;
      // The server rejected this draft because the rendered source is stale.
      // Do not leave the rejected wording on screen looking successfully saved.
      // Restore the original DOM (not textContent) so inline formatting survives
      // until the requested page reload fetches the new authoritative version.
      restoreOriginal(s);
      s.el.classList.remove('editing', 'dirty'); s.el.removeAttribute('contenteditable');
      hideBar();
      showNote(s, 'This paragraph was just updated on the site. Please reload the page and make your change again — your other edits are safe.');
      log('SEND stale_page ref=' + s.ref + ' (block reset)');
      return;
    }
    // network / unknown -> recoverable, draft preserved. Auto-save arms a backoff
    // retry (the user need not press anything); the manual bar keeps its Save CTA.
    s.state = ST.EDITING; barDisable(false);
    saveDraft(s);
    if (opts.pageOverride) {
      setLocalStatus(s, 'Not sent — choose this page again', 'err');
      setBarStatus('That didn’t send. Your wording is preserved; save again and choose this page only.');
      log('PAGE OVERRIDE error ref=' + s.ref + ' status=' + out.status + ' code=' + code);
      return;
    }
    setLocalStatus(s, 'Not sent — will retry', 'err');
    setBarStatus('That didn’t send — it will retry automatically, or press Save to try now.');
    if (opts.auto) armRetry(s);
    log('SEND error ref=' + s.ref + ' status=' + out.status + ' code=' + code);
  }

  /* ---------- DISCARD: only from explicit Cancel (R1) --------------------- */
  function discard(s) {
    if (s.state === ST.SAVING) return;
    clearTimers(s);                                 // cancel any pending auto-send/retry
    s.state = ST.IDLE;
    s.snapshot = s.originalText;
    restoreOriginal(s);
    s.el.classList.remove('editing', 'dirty');
    s.el.removeAttribute('contenteditable');
    s.dirty = false;
    clearDraft(s);
    s.suggestionId = null;                          // discard drops the id
    hideReauth(s); hideNote(s); hidePreview(); hideBar();
    s._hydrated = false; s._attr = '';
    s.el.classList.remove('eb--pending'); s.el.removeAttribute('data-eb-pending');
    setLocalStatus(s, 'Change discarded', null);
    log('CANCEL discard ref=' + s.ref);
    // A discarded block's RESTING state is any outstanding suggestion (WYSIWYG),
    // not the bare original — re-sync from the server so the overlay re-applies.
    repollPending();
  }

  /* ============================================================================
     6. The fixed, keyboard-tracking SAVE BAR (visualViewport) + autocorrect
        preview. One bar, bound to the active session. Save is bound on
        mousedown+preventDefault (never steals the caret) with the e.detail===0
        keyboard guard; the actual network send is the guarded critical section.
     ============================================================================ */
  var bar, barSave, barCancel, barStatusEl, barPreview, barPreviewText, barConfirm, barKeepEditing;
  function buildBar() {
    bar = el('div', 'editor-bar'); bar.setAttribute('role', 'region'); bar.setAttribute('aria-label', 'Editing controls');
    bar.hidden = true;

    var main = el('div', 'editor-bar__main');
    // "Save my change" / "Cancel" described a world that no longer exists. Typing
    // auto-saves after ~2.5s and the daemon publishes within ~2 min, so by the
    // time anyone reached for Save the change was already sent — the button was
    // asking for a decision that had been made for them, and Damien rightly
    // asked what it was for. What a reviewer actually needs here is (a) to know
    // it is handled, and (b) a way OUT of a change they regret.
    barSave = el('button', 'btn', 'Done'); barSave.type = 'button';
    barSave.setAttribute('aria-label', 'Done editing this paragraph');
    barCancel = el('button', 'btn btn--ghost', 'Undo my change'); barCancel.type = 'button';
    barStatusEl = el('span', 'editor-bar__status'); barStatusEl.setAttribute('aria-live', 'polite');
    main.appendChild(barSave); main.appendChild(barCancel); main.appendChild(barStatusEl);
    bar.appendChild(main);

    // autocorrect preview (shown before the real submit)
    barPreview = el('div', 'editor-bar__preview'); barPreview.hidden = true;
    barPreview.appendChild(el('div', 'editor-bar__preview-label', 'This is exactly what Damien will receive:'));
    barPreviewText = el('div', 'editor-bar__preview-text');
    barPreview.appendChild(barPreviewText);
    var prow = el('div', 'editor-bar__preview-row');
    barConfirm = el('button', 'btn', 'Send to Damien'); barConfirm.type = 'button';
    barKeepEditing = el('button', 'btn btn--ghost', 'Keep editing'); barKeepEditing.type = 'button';
    prow.appendChild(barConfirm); prow.appendChild(barKeepEditing);
    barPreview.appendChild(prow);
    bar.appendChild(barPreview);

    document.body.appendChild(bar);

    // Done -> flush whatever is unsent immediately and step out of the block.
    // Never a gate: the edit is already on its way, so this only removes the wait.
    barSave.addEventListener('mousedown', function (e) { e.preventDefault(); finishEditing(); });
    barSave.addEventListener('click', function (e) { if (e.detail === 0) finishEditing(); });
    // Confirm -> the guarded network send. mousedown+preventDefault keeps caret;
    // e.detail===0 lets keyboard users through the click path.
    barConfirm.addEventListener('mousedown', function (e) { e.preventDefault(); confirmSend(); });
    barConfirm.addEventListener('click', function (e) { if (e.detail === 0) confirmSend(); });
    barKeepEditing.addEventListener('mousedown', function (e) { e.preventDefault(); hidePreview(); refocusActive(); });
    barCancel.addEventListener('mousedown', function (e) { e.preventDefault(); var s = active(); if (s) discard(s); });
    barCancel.addEventListener('click', function (e) { if (e.detail === 0) { var s = active(); if (s) discard(s); } });

    if (window.visualViewport) {
      window.visualViewport.addEventListener('resize', positionBar);
      window.visualViewport.addEventListener('scroll', positionBar);
    }
  }
  function active() { return activeRef ? sessions[activeRef] : null; }
  function refocusActive() { var s = active(); if (s) try { s.el.focus(); } catch (e) {} }

  function positionBar() {
    if (!bar || bar.hidden) return;
    var vv = window.visualViewport;
    if (vv) {
      // keep the bar pinned just above the on-screen keyboard
      var gap = window.innerHeight - (vv.height + vv.offsetTop);
      bar.style.bottom = Math.max(0, gap) + 'px';
    } else {
      bar.style.bottom = '0px';
    }
  }
  function showBar(s) {
    if (!bar) buildBar();
    if (!s.editable) return;
    bar.hidden = false;
    hidePreview();
    setBarStatus(s.dirty ? 'Your change is ready to send.' : 'Click into the paragraph and edit the wording.');
    barDisable(false);
    positionBar();
  }
  function hideBar() { if (bar) { bar.hidden = true; hidePreview(); } }
  function syncBar(s) {
    if (!bar || bar.hidden || active() !== s) return;
    setBarStatus(s.dirty
      ? (DIRECT_APPLY
          ? 'Saving automatically — your change goes live on its own.'
          : 'Saving automatically — your change goes to Damien for review.')
      : 'Click into the paragraph and edit the wording.');
  }
  function setBarStatus(msg) { if (barStatusEl) barStatusEl.textContent = msg || ''; }
  function barDisable(on) { if (barSave) barSave.disabled = !!on; if (barConfirm) barConfirm.disabled = !!on; }

  function openPreview() {
    var s = active(); if (!s) return;
    if (!s.dirty) { setBarStatus('No change to send yet.'); return; }
    barPreviewText.textContent = s.snapshot;          // textContent only
    barPreview.hidden = false;
    setBarStatus('Check the wording, then send.');
  }

  // "Done": send anything still pending, then release the block. The reviewer is
  // telling us they have finished this paragraph, not asking permission. It never
  // reverts and never discards — the only destructive control here is Undo.
  function finishEditing() {
    var s = active(); if (!s) return;
    if (s.dirty) {
      clearTimers(s);            // do not let the debounce fire a second send
      sendSuggestion(s);
    } else {
      setLocalStatus(s, '', null);
    }
    try { s.el.blur(); } catch (e) {}
    s.el.classList.remove('editing');
    s.el.removeAttribute('contenteditable');
    hidePreview(); hideBar();
    log('DONE ref=' + s.ref + ' dirty=' + s.dirty);
  }
  function hidePreview() { if (barPreview) barPreview.hidden = true; }
  function confirmSend() { var s = active(); if (s) sendSuggestion(s); }

  /* ============================================================================
     7. Selection-first commenting  (LIFTED from spike-selection-comment)
        currentSelectionSnapshot (capture-at-rest + tremor debounce +
        PROSE-scoped clamp to the START block), onSelectionActivity, the float
        button bound on mousedown+preventDefault reading the STORED `captured`.
     ============================================================================ */
  var floatBtn, bubble, bAnchor, bQuote, bText, bSend, bCancel, bClose, bWhy;
  function buildCommentUI() {
    floatBtn = el('button', 'float-comment', 'Comment'); floatBtn.type = 'button';
    floatBtn.setAttribute('aria-label', 'Comment on the selected text');
    document.body.appendChild(floatBtn);

    // A side panel rather than a bubble floating over the passage: the note you
    // are writing and the sentence you are writing about stay visible together,
    // and the panel never covers the text it is asking you to judge.
    bubble = el('div', 'comment-bubble comment-bubble--panel'); bubble.setAttribute('role', 'dialog'); bubble.setAttribute('aria-modal', 'true'); bubble.setAttribute('aria-label', 'Leave a comment');
    bClose = el('button', 'comment-bubble__close'); bClose.type = 'button';
    bClose.setAttribute('aria-label', 'Close');
    bClose.appendChild(iconSvg(['M6 6l12 12', 'M18 6L6 18']));
    bAnchor = el('div', 'comment-bubble__anchor', 'On —');
    bQuote = el('p', 'comment-bubble__quote');
    bWhy = el('p', 'comment-bubble__why');
    var lab = el('label', 'comment-bubble__label', 'Your note for Damien'); lab.setAttribute('for', 'eb-comment-text');
    bText = el('textarea'); bText.id = 'eb-comment-text';
    bText.setAttribute('placeholder', 'What should Damien know about this passage?');
    bSend = el('button', 'btn', 'Send comment'); bSend.type = 'button';
    bCancel = el('button', 'btn btn--ghost', 'Cancel'); bCancel.type = 'button';
    var row = el('div', 'comment-bubble__row'); row.appendChild(bSend); row.appendChild(bCancel);
    bubble.appendChild(bClose);
    bubble.appendChild(bAnchor); bubble.appendChild(bQuote); bubble.appendChild(bWhy);
    bubble.appendChild(lab); bubble.appendChild(bText); bubble.appendChild(row);
    document.body.appendChild(bubble);

    bClose.addEventListener('mousedown', function (e) { e.preventDefault(); closeBubble(); });
    bClose.addEventListener('click', function (e) { if (e.detail === 0) closeBubble(); });

    floatBtn.addEventListener('mousedown', function (e) {
      e.preventDefault();                             // C2: keep the selection from collapsing
      if (!captured) { log('FLOAT click but no captured range'); return; }
      openBubbleFromCapture(captured);
    });
    // Keyboard path: a focused float button activated by Enter/Space fires `click`
    // with detail===0 (no preceding mousedown), so mouse users are unaffected but
    // keyboard users can open the comment dialog after a keyboard text selection.
    floatBtn.addEventListener('click', function (e) {
      if (e.detail !== 0) return;                     // mouse already handled on mousedown
      if (!captured) { log('FLOAT key but no captured range'); return; }
      openBubbleFromCapture(captured);
    });
    bSend.addEventListener('click', sendComment);
    // Cancel must be operable by BOTH mouse (mousedown+preventDefault keeps the
    // caret) AND keyboard (Enter/Space -> click, detail===0). Without the click
    // handler the dialog's Cancel was mouse-only — a keyboard trap.
    bCancel.addEventListener('mousedown', function (e) { e.preventDefault(); closeBubble(); });
    bCancel.addEventListener('click', function (e) { if (e.detail === 0) closeBubble(); });
    // Escape closes the dialog and returns focus to the trigger (WCAG 2.1.2 no
    // keyboard trap + 2.4.3 focus order). keydown bubbles up from the textarea.
    bubble.addEventListener('keydown', function (e) {
      if (e.key === 'Escape' || e.keyCode === 27) { e.preventDefault(); closeBubble(); }
    });

    // clicking elsewhere hides the floating button (but not while a bubble is open)
    document.addEventListener('mousedown', function (e) {
      if (e.target === floatBtn || bubble.contains(e.target)) return;
      if (!bubble.classList.contains('show')) floatBtn.classList.remove('show');
    }, true);

    document.addEventListener('mouseup', function () { onSelectionActivity(); });
    document.addEventListener('selectionchange', function () { onSelectionActivity(); });
  }

  var captured = null;    // { text, source_ref, session, rect } — the STABLE snapshot
  var tremorTimer = null;
  var MIN_CHARS = 1;
  function normalizeWs(s) { return (s || '').replace(/\s+/g, ' ').trim(); }
  function findLastTextNode(elx) {
    if (!elx) return null;
    var walker = document.createTreeWalker(elx, NodeFilter.SHOW_TEXT, null);
    var last = null, n; while ((n = walker.nextNode())) last = n; return last;
  }
  function sessionOfNode(node) {
    var e = node && node.nodeType === 3 ? node.parentNode : node;
    var block = e && e.closest ? e.closest('.eb') : null;
    if (!block) return null;
    var idx = block.getAttribute('data-eb-index');
    return idx != null ? byIndex[idx] : null;
  }

  function currentSelectionSnapshot() {
    var sel = window.getSelection();
    if (!sel || sel.rangeCount === 0 || sel.isCollapsed) return null;
    var raw = sel.getRangeAt(0);
    var text = sel.toString();
    if (normalizeWs(text).length < MIN_CHARS) return null;              // C3: jitter/zero-width

    var startS = sessionOfNode(raw.startContainer);
    var endS = sessionOfNode(raw.endContainer);
    if (!startS) return null;
    // never offer the selection-comment float on a block being edited (that's typing)
    if (startS.state !== ST.IDLE) return null;

    var range = raw.cloneRange();
    if (!endS || endS.ref !== startS.ref) {
      // C4: clamp a cross-block selection to the END of the START block's prose.
      var host = range.startContainer.nodeType === 3 ? range.startContainer.parentNode : range.startContainer;
      var startBlock = host.closest ? host.closest('.eb') : null;
      var textNode = findLastTextNode(startBlock);
      if (textNode) { range.setEnd(textNode, textNode.textContent.length); }
      text = range.toString();
      log('CLAMP cross-block -> start ref=' + startS.ref);
    }
    return { text: text, source_ref: startS.ref, session: startS, rect: range.getBoundingClientRect() };
  }

  function onSelectionActivity() {
    if (tremorTimer) clearTimeout(tremorTimer);
    tremorTimer = setTimeout(function () {
      var snap = currentSelectionSnapshot();
      if (snap) {
        captured = snap;
        positionFloat(snap.rect);
        floatBtn.classList.add('show');
        log('CAPTURE stable text="' + snap.text.slice(0, 30) + '" ref=' + snap.source_ref);
      }
      // else: keep last-stable `captured` (C3 tremor tolerance); button stays as is
    }, 90);
  }

  function positionFloat(rect) {
    var top = window.scrollY + rect.top - 46;
    var left = window.scrollX + rect.left + Math.min(rect.width / 2, 120);
    floatBtn.style.top = Math.max(4, top) + 'px';
    floatBtn.style.left = Math.max(4, left) + 'px';
  }

  function openBubbleFromCapture(snap) { openBubble(snap.session, snap.text, snap.rect); }
  function openBubbleWhole(s) {
    var r = s.el.getBoundingClientRect();
    openBubble(s, s.el.textContent, r);
    log('FALLBACK block-comment ref=' + s.ref);
  }
  function openBubble(s, text, rect) {
    // Remember what to return focus to when the dialog closes (the Comment
    // affordance for keyboard opens; null for mouse, where activeElement is body).
    bubbleReturnFocus = (document.activeElement && document.activeElement !== document.body) ? document.activeElement : null;
    s.pendingComment = { id: uuid(), anchor_text: text };
    activeCommentSession = s;
    // Never show a reviewer the internal source_ref. When the generator gave the
    // block a human context ("matter caption", "section title") use it; otherwise
    // say something a person would say. Damien still sees the exact ref on the
    // review page — it is his routing key, not John's reading material.
    bAnchor.textContent = 'On — ' + (s.context || 'this passage');
    bQuote.textContent = '“' + text + '”';
    // The comment-only explanation lives here now, said once, at the moment it
    // is relevant — instead of standing under every formatted block all day.
    if (s.commentOnly) {
      bWhy.textContent = 'This line is not edited directly — say what it should read and Damien applies the wording.';
      bWhy.classList.add('show');
    } else {
      bWhy.textContent = '';
      bWhy.classList.remove('show');
    }
    bText.value = '';
    bSend.disabled = false;
    // The panel is docked, not anchored: no positioning math, nothing covering
    // the passage. `rect` is kept in the signature for the selection path's
    // callers and for the scroll-into-view below.
    if (rect && typeof rect.top === 'number') {
      var offscreen = rect.top < 80 || rect.bottom > (window.innerHeight - 40);
      if (offscreen && s.el && s.el.scrollIntoView) {
        try { s.el.scrollIntoView({ block: 'center', behavior: 'auto' }); } catch (e) {}
      }
    }
    if (s.el) s.el.classList.add('eb--commenting');
    bubble.classList.add('show');
    document.documentElement.classList.add('has-comment-panel');
    scheduleRailLayout();          // the panel just narrowed the page
    floatBtn.classList.remove('show');
    setTimeout(function () { try { bText.focus(); } catch (e) {} }, 0);
    log('BUBBLE open id=' + s.pendingComment.id.slice(0, 8) + ' anchor="' + text.slice(0, 30) + '"');
  }
  var activeCommentSession = null;
  var bubbleReturnFocus = null;
  function closeBubble() {
    bubble.classList.remove('show');
    document.documentElement.classList.remove('has-comment-panel');
    scheduleRailLayout();
    if (activeCommentSession) {
      activeCommentSession.pendingComment = null;
      if (activeCommentSession.el) activeCommentSession.el.classList.remove('eb--commenting');
    }
    activeCommentSession = null;
    try { window.getSelection().removeAllRanges(); } catch (e) {}
    // Return focus to the trigger so keyboard users are not dropped at <body>.
    var rf = bubbleReturnFocus; bubbleReturnFocus = null;
    if (rf && rf.isConnected && typeof rf.focus === 'function') { try { rf.focus(); } catch (e) {} }
  }

  function sendComment() {
    var s = activeCommentSession; if (!s || !s.pendingComment) return;
    if (s._commentSending) return;                    // in-flight guard
    s._commentSending = true; bSend.disabled = true;
    var pc = s.pendingComment;
    // Comment: server derives kind/page; send { id, source_ref, comment,
    // original_hash } only.
    var payload = { id: pc.id, source_ref: s.ref, comment: (bText.value || '').trim(),
      original_hash: s.originalHash, page: PAGE };
    api('/suggest', { body: payload }).then(function (out) {
      s._commentSending = false;
      if (out.ok) {
        setLocalStatus(s, 'Comment sent ✓', 'sent');
        closeBubble();
        log('COMMENT sent ref=' + s.ref + ' id=' + pc.id.slice(0, 8));
        repollPending();
      } else {
        bSend.disabled = false;
        var data = out.data || {};
        var code = (data.error && data.error.code) || data.code ||
          (out.status === 401 ? 'no_edit_auth' : out.status === 429 ? 'rate_limited' : out.status === 413 ? 'validation_error' : 'network');
        var msg = code === 'no_edit_auth' ? 'Your editing link needs a refresh — reload the page, then send this comment again.'
          : code === 'rate_limited' ? 'You are sending comments quickly — wait a moment, then send again.'
          : code === 'validation_error' ? 'That comment is a bit too long to send — please shorten it and try again.'
          : 'That comment didn’t send — please try again.';
        setBubbleError(msg);
        log('COMMENT error ref=' + s.ref + ' code=' + code);
      }
    });
  }
  function setBubbleError(msg) {
    var e = bubble.querySelector('.comment-bubble__err');
    if (!e) { e = el('div', 'comment-bubble__err'); e.setAttribute('role', 'status'); bubble.insertBefore(e, bubble.querySelector('.comment-bubble__row')); }
    e.textContent = msg;
  }

  /* ============================================================================
     8. Inline pending / accepted / declined status  +  Word-style margin
        bubbles for comments. Rendered from #edits-data initially, then synced
        from GET /edit/v1/pending?page= .  textContent only.
     ============================================================================ */
  // Honest, store-driven pill wording (SL1). "Saved/Live" language appears ONLY
  // for store-confirmed states: `applied` (git-confirmed live) and — softened —
  // `accepted`/`in_flight` which say "Going live…" (in the pipeline, not yet live).
  // `needs_human` is UNMASKED: an explicit "needs attention — not applied" warning.
  // Short by design. These ride in the rail beside the passage, and the rail can
  // only sit OUT of the reading column if it FITS there — "Live on the site ✓ ·
  // JOS" is wide enough that it forced the controls back over the text on most
  // blocks. The long form survives as the pill's tooltip, so nothing is lost.
  var STATUS_LABELS = {
    pending: 'Pending',
    accepted: 'Going live…', in_flight: 'Going live…',
    accepted_blocked: 'Needs a fix',
    declined: 'Set aside', drift: 'Needs a look',
    needs_human: 'Not applied',
    applied: 'Live ✓', superseded: 'Replaced'
  };
  var STATUS_LONG = {
    pending: 'Waiting for Damien to look at it',
    accepted: 'Going live — publishing now', in_flight: 'Going live — publishing now',
    accepted_blocked: 'Accepted, but it could not be applied cleanly',
    declined: 'Set aside by Damien — the original wording is back',
    drift: 'The underlying text moved; this needs another look',
    needs_human: 'Needs attention — shown here, but NOT applied to the site',
    applied: 'Live on the site', superseded: 'Replaced by a newer edit'
  };
  var STATUS_TONE = {
    pending: 'pending', accepted: 'live', in_flight: 'live', applied: 'ok',
    accepted_blocked: 'warn', declined: 'stop', drift: 'warn',
    needs_human: 'warn', superseded: 'faint'
  };
  // Statuses that render an UNMASKED warning frame on the block (honest failure).
  var UNMASK_STATUSES = { needs_human: 1 };

  /* ---------- PENDING OVERLAY (WYSIWYG across reloads) ---------------------
     Statuses whose new_text is the block's INTENDED content and should be
     painted into the block on load, so a reload reproduces the same visual
     state as just-after-save. This is the store's listAll() "active" set MINUS
     drift:
       pending          — awaiting Damien's review
       accepted         — accepted, not yet applied to the site
       accepted_blocked — accepted, but an apply-time fix is pending
       in_flight        — mid-apply (claimed + leased)
       needs_human      — Damien will hand-apply (ambiguous / formatted)
     DELIBERATELY EXCLUDED (fall back to today's pill-only behavior):
       drift               the anchor moved; new_text was authored against text
                           that no longer exists at this block, so overlaying it
                           would misrepresent (the client stale-guard catches it
                           too, but we exclude the status on principle);
       applied             already live in the served page HTML (a no-op overlay);
       declined/superseded terminal reverts — the block must show its ORIGINAL.
     Hydration is DISPLAY-ONLY and never mutates originalText / originalHash /
     snapshot / dirty / suggestionId, so a bad overlay can never poison a save
     (worst case = wrong text on screen; the block still re-edits + re-saves
     against the canonical original from the map). textContent only (no innerHTML). */
  var HYDRATE_STATUSES = {
    pending: 1, accepted: 1, accepted_blocked: 1, in_flight: 1, needs_human: 1
  };

  var marginBubbles = [];   // track so we can clear between polls

  function clearMargins() {
    marginBubbles.forEach(function (n) { if (n.parentNode) n.parentNode.removeChild(n); });
    marginBubbles = [];
  }

  // A persisted, still-valid unsent draft for this block (keyed source_ref+hash).
  function draftPresent(s) {
    var rec = loadDraft(s);
    return !!(rec && rec.original_hash === s.originalHash);
  }

  // Can this pending item paint its new_text into the block RIGHT NOW? Guards:
  // hydratable status, an edit (not a comment) with real new_text, the block is
  // IDLE (never clobber a live edit), no unsent draft (a draft is newer intent
  // and WINS), and the suggestion's baseline still matches the block (stale
  // guard vs the map island's hash + version).
  function canHydrate(s, item) {
    if (!s) return false;
    if (item.kind === 'comment') return false;
    // Structural kinds (insert_after/delete/split/merge/move) address the block
    // as an ANCHOR — painting their payload into it would corrupt the display.
    // They surface as a status pill on the anchor instead.
    if (item.kind && item.kind !== 'prose' && item.kind !== 'json_scalar') return false;
    if (!HYDRATE_STATUSES[item.status]) return false;
    if (typeof item.new_text !== 'string' || item.new_text === '') return false;
    if (s.state !== ST.IDLE) return false;
    if (draftPresent(s)) return false;
    if (item.base_hash != null && item.base_hash !== s.originalHash) return false;   // source moved
    if (item.map_version && MAP_ISLAND.version && item.map_version !== MAP_ISLAND.version) return false;
    return true;
  }
  function paintHydration(s, item, label, tone, pill) {
    s.el.textContent = item.new_text;                 // textContent ONLY (XSS-safe)
    s.el.classList.add('eb--pending');
    s.el.setAttribute('data-eb-pending', '1');
    // SL1 unmask: needs_human still SHOWS the edited text, but framed as an
    // explicit warning (never masquerading as applied). The pill already says
    // "needs attention — not applied"; the frame makes it visible on the block.
    var unmask = !!UNMASK_STATUSES[item.status];
    s.el.classList.toggle('eb--warn', unmask);
    if (unmask) s.el.setAttribute('data-eb-warn', '1'); else s.el.removeAttribute('data-eb-warn');
    s._hydrated = true;
    s._attr = item.attribution || '';
    setStatusPill(pill, label + (item.attribution ? ' · ' + item.attribution : ''), tone);
    if (item.preview) pill.setAttribute('title', item.preview);
  }
  // Revert a block we hydrated earlier this session back to its canonical
  // original (e.g. the suggestion was declined/superseded between polls). Never
  // yanks text out from under a live edit or an unsent draft.
  function clearHydration(s) {
    if (!s || !s._hydrated) return;
    if (s.state !== ST.IDLE || draftPresent(s)) return;
    s.el.textContent = s.originalText;
    s.el.classList.remove('eb--pending', 'eb--warn');
    s.el.removeAttribute('data-eb-pending');
    s.el.removeAttribute('data-eb-warn');
    s._hydrated = false; s._attr = '';
  }

  function renderPending(items) {
    clearMargins();
    items = items || [];
    Object.keys(byIndex).forEach(function (idx) {
      var s = byIndex[idx];
      (s._pendingStatuses || []).forEach(function (p) {
        if (p.parentNode) p.parentNode.removeChild(p);
        if (s._status === p) s._status = null;
      });
      s._pendingStatuses = [];
    });
    // Reconcile hydration first: any block we hydrated before but that is no
    // longer hydratable this pass reverts to its original text.
    var willHydrate = {};
    items.forEach(function (item) {
      if (canHydrate(byIndex[item.block_index], item)) willHydrate[item.block_index] = true;
    });
    Object.keys(byIndex).forEach(function (idx) {
      if (!willHydrate[idx]) clearHydration(byIndex[idx]);
    });

    items.forEach(function (item) {
      var s = byIndex[item.block_index];
      if (!s) return;
      var label = STATUS_LABELS[item.status] || item.status || 'Sent';
      if (STRUCT_LABELS[item.kind]) {
        // Structural changes always wait for Damien — say so in full words
        // while they are pending; terser status words after a decision.
        label = STRUCT_LABELS[item.kind] + ' \u2014 ' +
          (item.status === 'pending' ? 'waiting for review' : label.toLowerCase());
      }
      var labelLong = STATUS_LONG[item.status] || '';
      var tone = STATUS_TONE[item.status] || 'pending';
      if (item.kind === 'comment') {
        var b = el('div', 'eb-comment-bubble eb-comment-bubble--' + tone);
        b.setAttribute('role', 'note');
        var head = el('div', 'eb-comment-bubble__head');
        head.appendChild(el('span', 'eb-comment-bubble__who', item.attribution ? ('Comment · ' + item.attribution) : 'Your comment'));
        head.appendChild(el('span', 'eb-comment-bubble__status', label));
        b.appendChild(head);
        if (item.preview) b.appendChild(el('p', 'eb-comment-bubble__body', item.preview));
        if (item.note) b.appendChild(el('p', 'eb-comment-bubble__note', 'Damien: ' + item.note));
        // AFTER the passage, never between the rail and its block: the rail is
        // pulled back over the element that follows it, so anything slipped in
        // between drags the controls away from the paragraph they belong to.
        insertAfter(b, s._note || s.el);
        s.el.classList.add('eb--has-margin');
        marginBubbles.push(b);
      } else {
        // A restored draft is newer than any server-side pending item. Keep its
        // unsent warning as the session's primary status and render older
        // suggestion history in a separate pill; otherwise "Pending" can make
        // John's unsent wording look submitted.
        var preserveDraftStatus = draftPresent(s);
        var pill = (s._pendingStatuses.length || preserveDraftStatus) ? el('span', 'eb-status') : statusPill(s);
        if (s._pendingStatuses.length || preserveDraftStatus) {
          pill.setAttribute('aria-live', 'polite');
          (s._tools || toolsEl(s)).appendChild(pill);
        }
        s._pendingStatuses.push(pill);
        if (!preserveDraftStatus) s._status = pill;
        // prose/json_scalar suggestion — paint the new_text into the block
        // (WYSIWYG) when it's safe, else fall back to the pill-only status
        // (today's behavior). Attribution rides on the pill either way.
        if (canHydrate(s, item)) {
          paintHydration(s, item, label, tone, pill);
        } else {
          setStatusPill(pill, label + (item.attribution ? ' · ' + item.attribution : ''), tone,
                        labelLong + (item.preview ? ' — “' + item.preview + '”' : ''));
        }
        if (item.note) { showNote(s, 'Damien: ' + item.note); }
      }
    });
  }

  var polling = false;
  function repollPending() {
    if (polling) return;
    polling = true;
    api('/pending?page=' + encodeURIComponent(PAGE), { method: 'GET' }).then(function (out) {
      polling = false;
      if (out.ok && out.data && Array.isArray(out.data.items)) {
        renderPending(out.data.items);
        // Refresh the SL6 liveness signals + banner from the same projection.
        setHeartbeat(out.data.heartbeat_age_s, out.data.direct_apply);
        log('PENDING synced: ' + out.data.items.length + ' item(s)');
      }
    });
  }

  /* ============================================================================
     9. Persistent banner + large-type toggle (design-direction §9)
     ============================================================================ */
  var bannerEl, bannerMsgEl;
  function buildBanner() {
    var banner = el('div', 'editor-banner'); banner.setAttribute('role', 'region'); banner.setAttribute('aria-label', 'Editing mode');
    bannerEl = banner;
    var msg = el('div', 'editor-banner__msg');
    msg.appendChild(el('span', 'editor-banner__tag', 'EDITING'));
    bannerMsgEl = el('span', null, 'You’re editing — changes go to Damien for review.');
    msg.appendChild(bannerMsgEl);
    banner.appendChild(msg);

    // History link — the editor-gated redline browser (index at /edit/history/).
    // Small chrome addition per the history-browser contract; absolute path so it
    // is independent of the wrapped page's <base>.
    var histLink = el('a', 'editor-banner__history', 'History');
    histLink.setAttribute('href', '/edit/history/');
    histLink.setAttribute('title', 'Redline change history for every document');
    banner.appendChild(histLink);

    // U8: the request-level verb — "ask for a bigger change in your own words".
    // Every scoped change waits for Damien (R6/KD3), and the dialog says so.
    var bigBtn = el('button', 'editor-banner__bigger', 'Bigger change…');
    bigBtn.type = 'button';
    bigBtn.setAttribute('title', 'Describe a change across this matter, a module, or the whole course — Damien approves it before anything changes');
    bigBtn.addEventListener('click', function () { openScopedDialog(); });
    banner.appendChild(bigBtn);

    var tg = el('div', 'segmented-toggle'); tg.setAttribute('role', 'group'); tg.setAttribute('aria-label', 'Type size');
    var bStd = el('button', null, 'STANDARD'); bStd.type = 'button'; bStd.setAttribute('aria-pressed', 'true');
    var bLg = el('button', null, 'LARGE TYPE'); bLg.type = 'button'; bLg.setAttribute('aria-pressed', 'false');
    tg.appendChild(bStd); tg.appendChild(bLg);
    banner.appendChild(tg);
    document.body.insertBefore(banner, document.body.firstChild);
    document.documentElement.classList.add('has-editor-banner');

    function setTypeLg(on) {
      document.documentElement.classList.toggle('type-lg', on);
      bLg.setAttribute('aria-pressed', on ? 'true' : 'false');
      bStd.setAttribute('aria-pressed', on ? 'false' : 'true');
      LS.set('sonsteng-type-lg', on ? '1' : '0');
    }
    bStd.addEventListener('click', function () { setTypeLg(false); });
    bLg.addEventListener('click', function () { setTypeLg(true); });
    // Use the same preference key as the public platform. Read the old editor-
    // only spelling once so existing users keep their choice during migration.
    if (LS.get('sonsteng-type-lg') === '1' || LS.get('sonsteng_type_lg') === '1') setTypeLg(true);
    updateBanner();
    // Let the banner degrade to "paused" locally even if a re-poll never lands.
    setInterval(updateBanner, 30000);
  }

  /* ---------- SL6 banner honesty: fresh / paused auto-apply -----------------
     Only DIRECT_APPLY (auto-apply) mode makes freshness claims. Fresh (<5 min)
     → subtle "edits go live automatically (~2 min)"; stale (>10 min, or the
     daemon has never checked in) → warning "auto-apply paused — last run N min
     ago; edits are safe and queued". In classic suggestion mode the banner keeps
     its original "changes go to Damien for review" copy. */
  function updateBanner() {
    if (!bannerEl || !bannerMsgEl) return;
    if (!DIRECT_APPLY) {
      bannerEl.classList.remove('editor-banner--warn');
      bannerMsgEl.textContent = 'You’re editing — changes go to Damien for review.';
      return;
    }
    var age = currentHbAge();
    if (age == null || age >= 600) {
      bannerEl.classList.add('editor-banner--warn');
      var mins = age == null ? null : Math.floor(age / 60);
      bannerMsgEl.textContent = mins == null
        ? 'Auto-apply paused — the apply service hasn’t checked in yet. Your edits are safe and queued.'
        : 'Auto-apply paused — last run ' + mins + ' min ago. Your edits are safe and queued.';
    } else if (age < 300) {
      bannerEl.classList.remove('editor-banner--warn');
      bannerMsgEl.textContent = 'Your edits go live automatically (~2 min).';
    } else {
      bannerEl.classList.remove('editor-banner--warn');
      bannerMsgEl.textContent = 'Your edits go live automatically.';
    }
  }

  /* ============================================================================
     10. pageshow / visibilitychange re-poll  (LIFTED from spike-blur-save)
     ============================================================================ */
  function installRepoll() {
    window.addEventListener('pageshow', function (e) {
      if (e.persisted) {
        // bfcache restore: a frozen SAVING would brick the bar — reset like chat.js
        Object.keys(sessions).forEach(function (ref) {
          var s = sessions[ref];
          if (s.state === ST.SAVING) { s.state = ST.EDITING; barDisable(false); }
          reconcileDraft(s);
        });
        repollPending();
        log('PAGESHOW persisted: reset SAVING + reconciled drafts + re-polled');
      }
    });
    document.addEventListener('visibilitychange', function () {
      if (document.visibilityState === 'visible') {
        Object.keys(sessions).forEach(function (ref) { reconcileDraft(sessions[ref]); });
        repollPending();
      } else {
        flushOnHide();   // tab hidden → flush unsent edits (keepalive)
      }
    });
    // pagehide is the reliable "leaving" signal (bfcache + real unload). Flush any
    // unsent dirty edit with a keepalive request so a mid-type navigation never
    // silently drops it; the localStorage draft remains the durable backup.
    window.addEventListener('pagehide', function () { flushOnHide(); });
  }

  // Best-effort keepalive flush of every unsent dirty block. Idempotent: the id is
  // reused so a later draft-wins reload replays the SAME payload (server dedupes on
  // the fingerprint). Never rotates here — the page is going away.
  function flushOnHide() {
    Object.keys(sessions).forEach(function (ref) {
      var s = sessions[ref];
      if (!s.editable || !s.dirty || s.state === ST.SAVING) return;
      var id = s.suggestionId || (s.suggestionId = uuid());
      var payload = { id: id, source_ref: s.ref, new_text: s.snapshot,
        original_hash: s.originalHash, page: PAGE };
      if (s.jsonPath) payload.json_path = s.jsonPath;
      saveDraft(s);   // draft is the durable backup regardless of the request outcome
      try { api('/suggest', { body: payload, keepalive: true }); } catch (e) {}
      log('FLUSH-ON-HIDE ref=' + s.ref + ' id=' + id.slice(0, 8));
    });
  }

  /* ============================================================================
     11. Boot
     ============================================================================ */
  var missing = [];
  function build() {
    var main = document.querySelector('main') || document.getElementById('main') || document.body;
    var cands = outermostCandidates(main);

    MAP.forEach(function (desc) {
      var elx = cands[desc.index];
      if (!elx) { missing.push(desc.index); return; }
      // WHAT IS EDITABLE — the client used to be stricter than everything behind
      // it, and nobody noticed until Damien asked why half the paragraphs on a
      // page had no pencil.
      //
      // It refused any json_scalar (captions, section titles, figures) and any
      // prose carrying inline formatting — which is most of a numbered activity
      // list, because each item opens with a bolded lead-in. But the Worker
      // rejects only kind === 'comment_only' (editor-endpoints.js), and the apply
      // engine handles BOTH of the kinds the client was hiding: json_scalar goes
      // parse -> surgical splice with a re-parse gate (WP5), and formatted prose
      // goes through span_splice, which preserves every span's raw markup and
      // declines to needs_human on any ambiguity rather than corrupting it (WP7).
      //
      // needs_human is not a failure mode to hide from — it is unmasked in the
      // page with a warning frame and a 'not applied' pill, so the worst case is
      // visible and recoverable. Hiding the pencil, by contrast, silently told a
      // reviewer the sentence could not be changed at all. Only genuinely
      // comment-only blocks stay comment-only now.
      var commentOnly = desc.kind === 'comment_only';
      var editable = !commentOnly;
      var s = makeSession(desc, elx, editable, commentOnly);
      wireBlock(s);
      reconcileDraft(s);
    });
    if (missing.length) log('WALKER MISMATCH: ' + missing.length + ' block index(es) not found — map/page drift');

    buildBanner();

    installLockedPassages();

    buildAddFact();
    buildOverridesView();
    buildBar();
    buildCommentUI();
    scheduleRailLayout();
    window.addEventListener('resize', scheduleRailLayout);
    // Everything that lands AFTER first paint changes the measurements: web
    // fonts swapping in, images settling, late stylesheets. Measuring once on
    // build left some blocks parked in flow with a 400px gutter going unused —
    // harmless but arbitrary, and arbitrary is what makes a layout feel broken.
    window.addEventListener('load', scheduleRailLayout);
    if (document.fonts && document.fonts.ready && document.fonts.ready.then) {
      document.fonts.ready.then(scheduleRailLayout).catch(function () {});
    }
    setTimeout(scheduleRailLayout, 400);
    installRepoll();

    // initial inline status from the island, then a live re-poll
    renderPending(INITIAL_PENDING);
    repollPending();

    log('EDITOR ready — ' + Object.keys(sessions).length + ' block(s), page=' + PAGE);
  }

  /* ============================================================================
     12. Harness surface (test-harness.html + puppeteer). Mirrors chat's
         window.SonstengChat + the two spikes' surfaces.
     ============================================================================ */
  window.SonstengEditor = {
    build: build,
    ready: function () { return Object.keys(sessions).length; },
    page: function () { return PAGE; },
    normalize: normalize,
    normHash: normHash,
    walkerCount: function () { var m = document.querySelector('main') || document.body; return outermostCandidates(m).length; },
    block: function (index) {
      var s = byIndex[index]; return s && {
        index: s.index, ref: s.ref, kind: s.kind, editable: s.editable, commentOnly: s.commentOnly,
        state: s.state, dirty: s.dirty, suggestionId: s.suggestionId, snapshot: s.snapshot,
        originalHash: s.originalHash, originalText: s.originalText,
        hydrated: !!s._hydrated, attribution: s._attr || '',
        statusText: s._status ? s._status.textContent : '',
        shared: s.shared, occurrenceCount: s.occurrences.length
      };
    },
    // hydration harness hooks: render an arbitrary #edits-data item set (the
    // SAME entry point boot + repoll use) and read a block's draft key.
    applyPending: function (items) { renderPending(items); },
    draftKeyFor: function (index) { var s = byIndex[index]; return s ? draftKey(s) : null; },
    blockText: function (index) { var s = byIndex[index]; return s ? s.el.textContent : ''; },
    statusText: function (index) { var s = byIndex[index]; return s && s._status ? s._status.textContent : ''; },
    noteText: function (index) { var s = byIndex[index]; return s && s._note && s._note.classList.contains('show') ? s._note.textContent : ''; },
    reauthShown: function (index) { var s = byIndex[index]; return !!(s && s._reauth && s._reauth.classList.contains('show')); },
    marginBubbles: function () { return marginBubbles.map(function (n) { return n.textContent; }); },
    bannerText: function () { return bannerMsgEl ? bannerMsgEl.textContent : ''; },
    bannerWarn: function () { return !!(bannerEl && bannerEl.classList.contains('editor-banner--warn')); },
    blockWarn: function (index) { var s = byIndex[index]; return !!(s && s.el.classList.contains('eb--warn')); },
    refreshBanner: function () { updateBanner(); },
    barVisible: function () { return !!(bar && !bar.hidden); },
    previewVisible: function () { return !!(barPreview && !barPreview.hidden); },
    previewText: function () { return barPreview && !barPreview.hidden ? barPreviewText.textContent : null; },
    barStatus: function () { return barStatusEl ? barStatusEl.textContent : ''; },
    // programmatic verbs (mirror the real user events)
    focusBlock: function (index) { var s = byIndex[index]; if (s) { makeEditable(s, false); enterEdit(s); } },
    typeInto: function (index, text) {
      var s = byIndex[index]; if (!s || !s.editable) return;   // comment-only has no edit path
      makeEditable(s, false); if (s.state === ST.IDLE) enterEdit(s);
      s.el.textContent = text; s.el.dispatchEvent(new Event('input', { bubbles: true }));
    },
    blurBlock: function (index) { var s = byIndex[index]; if (s) s.el.dispatchEvent(new Event('blur')); },
    clickSave: function (index) { var s = byIndex[index]; if (s) sendSuggestion(s); },                 // the guarded send
    sharedDialog: function () {
      if (!sharedReachDlg) return null;
      return { pages: Array.prototype.map.call(sharedReachDlg.querySelectorAll('.eb-shared-dialog__pages li'), function (n) { return n.textContent; }) };
    },
    chooseSharedEverywhere: function () { if (sharedReachDlg) sharedReachDlg.querySelector('.eb-shared-dialog__everywhere').click(); },
    chooseSharedThisPage: function () { if (sharedReachDlg) sharedReachDlg.querySelector('.eb-shared-dialog__here').click(); },
    repeatPageOverride: function (index) {
      var s = byIndex[index]; if (s) { requestPageOverride(s); requestPageOverride(s); }
    },
    dismissSharedDialog: closeSharedReachDialog,
    openLocked: function (index) {
      var locked = document.querySelectorAll('[data-eb-locked]');
      if (locked[index]) openLockedDialog(locked[index]);
    },
    closeLocked: closeLockedDialog,
    lockedDialog: function () {
      if (!lockedDialogEl) return null;
      var link = lockedDialogEl.querySelector('.eb-locked-dialog__passage');
      return { text: lockedDialogEl.textContent, href: link ? link.getAttribute('href') : null,
               target: link ? link.getAttribute('target') : null };
    },
    tripleClickSave: function (index) { var s = byIndex[index]; if (s) { sendSuggestion(s); sendSuggestion(s); sendSuggestion(s); } },
    openPreview: function (index) { var s = byIndex[index]; if (s) { activeRef = s.ref; showBar(s); openPreview(); } },
    confirmSend: function (index) { var s = byIndex[index]; if (s) { activeRef = s.ref; sendSuggestion(s); } },
    clickCancel: function (index) { var s = byIndex[index]; if (s) discard(s); },
    reauthResend: function (index) { var s = byIndex[index]; if (s) { hideReauth(s); sendSuggestion(s); } },
    // selection commenting
    captured: function () { return captured && { text: captured.text, source_ref: captured.source_ref }; },
    floatVisible: function () { return !!(floatBtn && floatBtn.classList.contains('show')); },
    bubbleOpen: function () { return bubble && bubble.classList.contains('show') && activeCommentSession ? { anchor: activeCommentSession.pendingComment && activeCommentSession.pendingComment.anchor_text, ref: activeCommentSession.ref } : null; },
    dragSelect: function (index, a, b) { selectWithin(index, a, b); document.dispatchEvent(new Event('mouseup')); return new Promise(function (r) { setTimeout(r, 140); }); },
    dragSelectAcross: function (i1, a, i2, b) { selectAcross(i1, a, i2, b); document.dispatchEvent(new Event('mouseup')); return new Promise(function (r) { setTimeout(r, 140); }); },
    clickFloat: function () { floatBtn.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, cancelable: true })); },
    clickBlockComment: function (index) { var s = byIndex[index]; if (s) openBubbleWhole(s); },
    closeBubble: function () { closeBubble(); },
    sendBubble: function (note) { bText.value = note || ''; sendComment(); },
    repoll: repollPending,
    log: function () { return LOG.slice(); }
  };

  /* harness selection helpers (single-text-node blocks) */
  function firstTextNode(index) {
    var s = byIndex[index]; if (!s) return null;
    var w = document.createTreeWalker(s.el, NodeFilter.SHOW_TEXT, null);
    return w.nextNode();
  }
  function selectWithin(index, a, b) {
    var node = firstTextNode(index); if (!node) return;
    var len = node.textContent.length;
    var x = Math.max(0, Math.min(a, len)), y = Math.max(x, Math.min(b, len));
    var range = document.createRange(); range.setStart(node, x); range.setEnd(node, y);
    var sel = window.getSelection(); sel.removeAllRanges(); sel.addRange(range);
  }
  function selectAcross(i1, a, i2, b) {
    var n1 = firstTextNode(i1), n2 = firstTextNode(i2); if (!n1 || !n2) return;
    var range = document.createRange();
    range.setStart(n1, Math.min(a, n1.textContent.length));
    range.setEnd(n2, Math.min(b, n2.textContent.length));
    var sel = window.getSelection(); sel.removeAllRanges(); sel.addRange(range);
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', build);
  else build();
})();
