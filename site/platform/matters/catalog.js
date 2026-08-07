(function(){
'use strict';
var form=document.querySelector('[data-catalog-form]');if(!form)return;
var results=document.querySelector('[data-catalog-results]'),status=document.querySelector('[data-catalog-status]');
var empty=document.querySelector('[data-catalog-empty]'),heading=document.getElementById('catalog-results');
var nav=document.querySelector('.pagination');
var esc=function(s){return String(s||'').replace(/[&<>"']/g,function(c){return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c];});};
function locationParams(){var p=new URLSearchParams(location.search),m=location.pathname.match(/page-(\d+)\.html$/);if(!p.has('page')&&m)p.set('page',m[1]);return p;}
function syncForm(p){['q','shape','tier','fee'].forEach(function(k){if(form.elements[k])form.elements[k].value=p.get(k)||'';});}
var initial=locationParams();syncForm(initial);
fetch('catalog-index.json').then(function(r){if(!r.ok)throw Error('index');return r.json();}).then(function(index){
 function render(p,push){
  var page=Math.max(1,parseInt(p.get('page')||'1',10)||1),q=(p.get('q')||'').toLowerCase();
  var matches=index.matters.filter(function(m){return (!q||(m.caption+' '+m.history_summary+' '+m.shape_label+' '+m.jurisdiction).toLowerCase().includes(q))&&(!p.get('shape')||m.shape===p.get('shape'))&&(!p.get('tier')||m.tier===p.get('tier'))&&(!p.get('fee')||m.fee_type===p.get('fee'));});
  var pages=Math.max(1,Math.ceil(matches.length/index.page_size));page=Math.min(page,pages);p.set('page',page);
  results.innerHTML=matches.slice((page-1)*index.page_size,page*index.page_size).map(function(m){return '<article class="card catalog-card" data-catalog-id="'+esc(m.id)+'"><div class="chips"><span class="chip">'+esc(m.tier.toUpperCase())+'</span><span class="chip">'+esc(m.shape_label)+'</span><span class="chip">'+esc(m.fee_type.toUpperCase())+'</span></div><h2 class="matter-card__caption"><a href="'+encodeURIComponent(m.slug)+'/index.html">'+esc(m.caption)+'</a></h2><p class="matter-card__premise">'+esc(m.history_summary)+'</p><p><a class="btn" download href="../downloads/'+encodeURIComponent(m.slug)+'-student-materials.zip">Download student materials (.zip)</a></p></article>';}).join('');
  nav.innerHTML=Array.from({length:pages},function(_,i){var n=i+1,next=new URLSearchParams(p);next.set('page',n);var path=n===1?'index.html':'page-'+n+'.html';return '<a href="'+path+'?'+esc(next.toString())+'" aria-current="'+(n===page?'page':'false')+'">Page '+n+'</a>';}).join(' ');
  empty.hidden=matches.length!==0;status.textContent=matches.length+' matters · page '+page+' of '+pages;
  if(push){var canonical=location.pathname.replace(/page-\d+\.html$/,'index.html');history.pushState({},'',canonical+'?'+p.toString());heading.focus();}
 }
 function paramsFromForm(){var p=new URLSearchParams(location.search),fd=new FormData(form);['q','shape','tier','fee'].forEach(function(k){var v=String(fd.get(k)||'').trim();if(v)p.set(k,v);else p.delete(k);});p.set('page','1');return p;}
 render(initial,false);
 form.addEventListener('submit',function(e){e.preventDefault();render(paramsFromForm(),true);});
 window.addEventListener('popstate',function(){var p=locationParams();syncForm(p);render(p,false);});
}).catch(function(){status.textContent+=' · enhanced search unavailable; use page links.';});
})();