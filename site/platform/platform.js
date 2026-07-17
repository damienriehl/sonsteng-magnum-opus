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
      var on = document.documentElement.classList.toggle('type-lg');
      try{ localStorage.setItem('sonsteng-type-lg', on ? '1':'0'); }catch(e){}
      syncTT();
    });
  }

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
})();
