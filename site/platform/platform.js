/* platform.js — progressive enhancement for the Practicum Press site. */
(function(){
  'use strict';

  /* ---- large-type toggle ---- */
  var tt = document.getElementById('type-toggle');
  function syncTT(){
    var on = document.documentElement.classList.contains('type-lg');
    if (tt){ tt.setAttribute('aria-pressed', on ? 'true':'false'); }
  }
  syncTT();
  if (tt){
    tt.addEventListener('click', function(){
      var on = !document.documentElement.classList.contains('type-lg');
      if (window.SonstengTypePreference) window.SonstengTypePreference.set(on);
      else document.documentElement.classList.toggle('type-lg', on);
      syncTT();
    });
  }
  if (window.SonstengTypePreference) window.SonstengTypePreference.subscribe(syncTT);

  /* ---- matter-library tier toggle ---- */
  var seg = document.querySelector('[data-tier-toggle]');
  if (seg){
    var lib = document.querySelector('[data-tier-active]');
    seg.querySelectorAll('button').forEach(function(b){
      b.addEventListener('click', function(){
        var tier = b.getAttribute('data-tier');
        seg.querySelectorAll('button').forEach(function(x){ x.setAttribute('aria-pressed', x===b ? 'true':'false'); });
        seg.setAttribute('data-side', tier==='real' ? 'real':'meridian');
        if (lib){
          lib.setAttribute('data-tier-active', tier);
          var shown = 0;
          lib.querySelectorAll('[data-tier-card]').forEach(function(c){
            var hide = (tier !== 'all') && (c.getAttribute('data-tier-card') !== tier);
            c.classList.toggle('tier-hidden', hide);
            if (!hide) shown++;
          });
          var cnt = document.querySelector('[data-tier-count]');
          if (cnt) cnt.textContent = (tier==='all' ? 'ALL ' : (tier==='meridian' ? 'MERIDIAN ' : 'REAL-STATE ')) + shown;
        }
      });
    });
  }

  /* ---- viz table twins ---- */
  document.querySelectorAll('.viz-toggle').forEach(function(btn){
    btn.addEventListener('click', function(){
      var id = btn.getAttribute('data-target');
      var tbl = document.getElementById(id);
      if (!tbl) return;
      var open = tbl.hasAttribute('hidden');
      if (open){ tbl.removeAttribute('hidden'); btn.setAttribute('aria-expanded','true'); }
      else { tbl.setAttribute('hidden',''); btn.setAttribute('aria-expanded','false'); }
    });
  });

  /* ---- packet scroll-spy TOC ---- */
  var toc = document.querySelector('.toc-rail');
  if (toc && 'IntersectionObserver' in window){
    var links = {};
    toc.querySelectorAll('a').forEach(function(a){
      var id = a.getAttribute('href');
      if (id && id.charAt(0)==='#') links[id.slice(1)] = a;
    });
    var current = null;
    var obs = new IntersectionObserver(function(entries){
      entries.forEach(function(en){
        if (en.isIntersecting){
          var id = en.target.id;
          if (current) current.removeAttribute('aria-current');
          if (links[id]){ links[id].setAttribute('aria-current','true'); current = links[id]; }
        }
      });
    }, { rootMargin:'-10% 0px -75% 0px', threshold:0 });
    document.querySelectorAll('.part[id]').forEach(function(p){ obs.observe(p); });
  }

  /* ---- firm dashboard: PATTERNS toggle ---- */
  var root = document.documentElement;
  var patBtn = document.getElementById('viz-patterns');
  if (patBtn){
    var forced = window.matchMedia ? window.matchMedia('(forced-colors: active)') : null;
    function applyPatterns(on){
      root.classList.toggle('patterns-on', on);
      patBtn.setAttribute('aria-pressed', on ? 'true' : 'false');
    }
    function storedPref(){
      try{ return localStorage.getItem('sonsteng-viz-patterns') === '1'; }catch(e){ return false; }
    }
    /* Auto-on under forced-colors; otherwise honor the saved preference (off by default). */
    applyPatterns((forced && forced.matches) || storedPref());
    patBtn.addEventListener('click', function(){
      var on = !root.classList.contains('patterns-on');
      applyPatterns(on);
      try{ localStorage.setItem('sonsteng-viz-patterns', on ? '1' : '0'); }catch(e){}
    });
    if (forced && forced.addEventListener){
      forced.addEventListener('change', function(e){ applyPatterns(e.matches || storedPref()); });
    }
  }

  /* ---- firm dashboard: shared tooltip (value leads, label follows, swatch) ---- */
  var tip = document.getElementById('viz-tip');
  var marks = document.querySelectorAll('.viz-mark');
  if (tip && marks.length){
    var tSw = tip.querySelector('.viz-tip__sw');
    var tV  = tip.querySelector('.viz-tip__v');
    var tL  = tip.querySelector('.viz-tip__l');
    var tapMark = null;   // set only when a tooltip is pinned open by a touch tap
    var lastPtr = 'mouse';
    function place(cx, cy){
      var pad = 10, r = tip.getBoundingClientRect();
      var x = cx + 14, y = cy + 16;
      if (x + r.width + pad > window.innerWidth) x = cx - r.width - 14;
      if (x < pad) x = pad;
      if (y + r.height + pad > window.innerHeight) y = cy - r.height - 16;
      if (y < pad) y = pad;
      tip.style.left = x + 'px';
      tip.style.top = y + 'px';
    }
    function show(mark, cx, cy){
      tV.textContent = mark.getAttribute('data-v') || '';   // textContent only — names are untrusted
      tL.textContent = mark.getAttribute('data-l') || '';
      tSw.style.background = mark.getAttribute('data-c') || 'transparent';
      tip.hidden = false;
      place(cx, cy);
    }
    function hide(){ tip.hidden = true; tapMark = null; }
    function edgeTop(mark){ var b = mark.getBoundingClientRect(); return [b.left + b.width / 2, b.top]; }
    function isMark(el){ return el && el.classList && el.classList.contains('viz-mark'); }
    marks.forEach(function(mark){
      mark.addEventListener('pointerdown', function(ev){ lastPtr = ev.pointerType || 'mouse'; });
      mark.addEventListener('pointerenter', function(ev){
        if (ev.pointerType === 'touch') return;   // touch is handled via tap toggle
        show(mark, ev.clientX, ev.clientY);
      });
      mark.addEventListener('pointermove', function(ev){
        if (ev.pointerType === 'touch' || tip.hidden) return;
        place(ev.clientX, ev.clientY);
      });
      mark.addEventListener('pointerleave', function(ev){
        if (ev.pointerType === 'touch' || tapMark) return;
        hide();
      });
      // Keyboard focus mirrors hover (skip when the focus was driven by a touch —
      // touch uses the tap toggle below so the two don't fight).
      mark.addEventListener('focus', function(){
        if (lastPtr === 'touch') return;
        var c = edgeTop(mark); show(mark, c[0], c[1]);
      });
      mark.addEventListener('blur', function(){ if (!tapMark) hide(); });
      mark.addEventListener('click', function(){          // touch tap toggles open/closed
        if (lastPtr !== 'touch') return;
        if (tapMark === mark && !tip.hidden){ hide(); }
        else { tapMark = mark; var c = edgeTop(mark); show(mark, c[0], c[1]); }
      });
    });
    document.addEventListener('click', function(e){   // tap outside any mark dismisses
      if (!isMark(e.target)) hide();
    });
    document.addEventListener('scroll', function(){
      if (tip.hidden) return;
      // A focused mark keeps its tooltip pinned (the browser's focus-scroll must
      // not dismiss it); mouse/touch tooltips dismiss on scroll.
      if (isMark(document.activeElement) && !tapMark){
        var c = edgeTop(document.activeElement); place(c[0], c[1]);
      } else { hide(); }
    }, true);
    window.addEventListener('keydown', function(e){ if (e.key === 'Escape'){ hide();
      if (isMark(document.activeElement)) document.activeElement.blur(); } });
  }
})();
