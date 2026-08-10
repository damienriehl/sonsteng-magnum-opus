// editor-assets.js — the editor.css / editor.js the injector references and the
// Worker serves at /edit/assets/* (script-src 'self' requires same-origin, non-
// inline assets). These are MINIMAL, contract-establishing stubs: they render the
// persistent "You're editing" banner and expose the injected data islands. The
// editor CLIENT (app/editor/, built by a sibling agent) provides the full
// contenteditable + comment UX; when that bundle lands it is wired in here in
// place of these stubs. Kept server-owned so the CSP + no-inline guarantees hold
// regardless of client build state.

export const EDITOR_CSS = `:root{
  --pp-ink:#1a2b3a;--pp-paper:#faf7f0;--pp-accent:#7a1f2b;--pp-rule:#d8cfbf;
  --pp-focus:#c9a227;
}
*{box-sizing:border-box}
body{margin:0;font-family:"Iowan Old Style","Palatino Linotype",Palatino,Georgia,serif;
  color:var(--pp-ink);background:var(--pp-paper);line-height:1.55;font-size:18px}
.pp-edit-banner{position:sticky;top:0;z-index:9999;background:var(--pp-accent);color:#fff;
  padding:.6rem 1rem;font-size:1rem;display:flex;gap:.75rem;align-items:center;
  box-shadow:0 2px 6px rgba(0,0,0,.2)}
.pp-edit-banner strong{font-weight:700}
[data-eb]{outline:1px dashed transparent;transition:outline-color .15s}
[data-eb]:hover{outline-color:var(--pp-rule);cursor:text}
[data-eb]:focus{outline:2px solid var(--pp-focus);outline-offset:2px}
.pp-edit-status{margin-left:auto;font-size:.85rem;opacity:.9}
main{max-width:70ch;margin:1.5rem auto;padding:0 1.25rem}
@media (max-width:640px){body{font-size:20px}}
`;

// Minimal client: reads the injected islands, marks editable blocks per the
// walker contract, shows the banner + pending count. No network writes here —
// the full client (sibling agent) owns the contenteditable/comment/save flow.
export const EDITOR_JS = `(() => {
  "use strict";
  function island(id){ const el = document.getElementById(id);
    if(!el) return null; try { return JSON.parse(el.textContent); } catch { return null; } }
  const map = island("editor-map-data") || { blocks: [], version: "" };
  const edits = island("edits-data") || { items: [] };

  // Walker contract (mirror of tools/build_site.py): candidate elements within
  // <main>, in document order — block tags plus explicit .eb-candidate leaves.
  const TAGS = new Set(["P","LI","H1","H2","H3","H4","H5","H6","BLOCKQUOTE"]);
  const editable = new Map((map.blocks||[]).map(b => [b.index, b]));
  const main = document.querySelector("main");
  if (main) {
    const candidates = [];
    (function walk(node){
      for (const child of node.children){
        if (TAGS.has(child.tagName) || child.classList.contains("eb-candidate")) { candidates.push(child); }
        else walk(child);
      }
    })(main);
    candidates.forEach((el, i) => {
      const block = editable.get(i);
      if (!block) return;
      el.setAttribute("data-eb", block.source_ref);
      el.setAttribute("data-eb-kind", block.kind);
      el.setAttribute("data-eb-hash", block.original_hash);
    });
  }

  // Persistent banner (plain-language, large type).
  const banner = document.createElement("div");
  banner.className = "pp-edit-banner";
  banner.innerHTML = "<strong>You're editing.</strong> " +
    "<span>Your changes go to Damien for review.</span>" +
    "<span class='pp-edit-status'></span>";
  document.body.insertBefore(banner, document.body.firstChild);
  const status = banner.querySelector(".pp-edit-status");
  const pending = (edits.items||[]).filter(x => x.status === "pending").length;
  status.textContent = pending ? (pending + " change" + (pending===1?"":"s") + " pending review") : "No changes yet";
})();
`;

// ---- Review page assets (admin) ---------------------------------------------
export const REVIEW_CSS = `.rv-head{border-bottom:2px solid var(--pp-accent);margin-bottom:1rem;padding-bottom:.5rem}
.rv-sub{color:#5c5346;font-size:.95rem}
.rv-group{border:1px solid var(--pp-rule);border-radius:8px;margin:1rem 0;background:#fff;overflow:hidden}
.rv-group>summary{cursor:pointer;padding:.75rem 1rem;background:#f3ecdd;font-weight:700;list-style:none;display:flex;gap:.5rem;align-items:center}
.rv-group>summary::-webkit-details-marker{display:none}
.rv-src{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:.8rem;color:#6b5b46;word-break:break-all}
.rv-item{padding:.85rem 1rem;border-top:1px solid var(--pp-rule)}
.rv-meta{font-size:.8rem;color:#6b5b46;margin-bottom:.35rem;display:flex;gap:.6rem;flex-wrap:wrap;align-items:center}
.rv-badge{border-radius:99px;padding:.05rem .5rem;font-size:.72rem;background:#eee;color:#333}
.rv-attr{border-radius:99px;padding:.05rem .5rem;font-size:.72rem;font-weight:700;letter-spacing:.04em;background:#3a2f22;color:#f4efe4}
.rv-badge.pending{background:#fff3cd;color:#664d03}
.rv-badge.drift{background:#f8d7da;color:#842029}
.rv-badge.needs_human{background:#cfe2ff;color:#084298}
.rv-badge.accepted{background:#d1e7dd;color:#0f5132}
.rv-badge.accepted_blocked{background:#f8d7da;color:#842029}
.rv-diff{background:#faf7f0;border:1px solid var(--pp-rule);border-radius:6px;padding:.6rem .8rem;margin:.4rem 0;line-height:1.5}
.rv-diff del{background:#f8d0d4;text-decoration:line-through;color:#842029}
.rv-diff ins{background:#c7ecd3;text-decoration:none;color:#0f5132}
.rv-comment{font-style:italic;color:#3a2f22}
.rv-actions{display:flex;gap:.5rem;flex-wrap:wrap;margin-top:.5rem;align-items:center}
.rv-actions button{font:inherit;font-size:.9rem;padding:.4rem .8rem;border-radius:6px;border:1px solid var(--pp-rule);cursor:pointer;min-height:40px}
.rv-accept{background:#0f5132;color:#fff;border-color:#0f5132}
.rv-decline{background:#fff;color:#842029;border-color:#842029}
.rv-reanchor{background:#fff;color:#084298;border-color:#084298}
.rv-note{font:inherit;font-size:.9rem;padding:.4rem;border:1px solid var(--pp-rule);border-radius:6px;flex:1;min-width:12ch}
.rv-bulk{margin:.5rem 0 1rem;display:flex;gap:.5rem}
.rv-empty{padding:2rem;text-align:center;color:#6b5b46}
.rv-toast{position:fixed;bottom:1rem;left:50%;transform:translateX(-50%);background:var(--pp-ink);color:#fff;padding:.6rem 1rem;border-radius:8px;opacity:0;transition:opacity .2s}
.rv-toast.show{opacity:1}
`;

// Review client: reads the escaped island, renders groups + text-node-only word
// diffs, wires Accept/Decline/re-anchor + bulk to POST /edit/v1/decide with the
// CSRF header. NO client-authored string is ever assigned to innerHTML.
export const REVIEW_JS = `(() => {
  "use strict";
  const el = document.getElementById("review-data");
  let data = { items: [] };
  try { data = JSON.parse(el.textContent); } catch {}
  const root = document.getElementById("rv-root");

  function toast(msg){ let t=document.querySelector(".rv-toast");
    if(!t){t=document.createElement("div");t.className="rv-toast";document.body.appendChild(t);}
    t.textContent=msg; t.classList.add("show"); setTimeout(()=>t.classList.remove("show"),1800); }

  // Word-level diff, rendered with textContent only (XSS-safe).
  function diff(oldStr, newStr){
    const a=(oldStr||"").split(/(\\s+)/), b=(newStr||"").split(/(\\s+)/);
    const frag=document.createElement("div"); frag.className="rv-diff";
    // Simple LCS-free token diff: longest common prefix/suffix, middle replaced.
    let i=0; while(i<a.length&&i<b.length&&a[i]===b[i]) i++;
    let j=0; while(j<a.length-i&&j<b.length-i&&a[a.length-1-j]===b[b.length-1-j]) j++;
    const common1=a.slice(0,i), delMid=a.slice(i,a.length-j), insMid=b.slice(i,b.length-j), common2=a.slice(a.length-j);
    const put=(tag,txt)=>{ if(!txt.length)return; const n=tag?document.createElement(tag):document.createTextNode("");
      if(tag){n.textContent=txt.join("");frag.appendChild(n);} else {frag.appendChild(document.createTextNode(txt.join("")));} };
    put(null,common1); put("del",delMid); put("ins",insMid); put(null,common2);
    return frag;
  }

  async function decide(action, key, note){
    const body = { action };
    if(key.id) body.id=key.id; if(key.group_id) body.group_id=key.group_id;
    if(note) body.note=note;
    const r = await fetch("/edit/v1/decide", {
      method:"POST", credentials:"same-origin",
      headers:{ "content-type":"application/json", "X-Edit-Request":"1" },
      body: JSON.stringify(body)
    });
    if(r.ok){ toast(action+" ok"); location.reload(); }
    else { toast("failed ("+r.status+")"); }
  }

  function itemNode(it){
    const wrap=document.createElement("div"); wrap.className="rv-item";
    const meta=document.createElement("div"); meta.className="rv-meta";
    const badge=document.createElement("span"); badge.className="rv-badge "+it.status; badge.textContent=it.status;
    meta.appendChild(badge);
    // Reviewer attribution ("RSH"/"JOS") as its own labelled chip — the WP2
    // second-editor signal must be visible on the page Damien reviews from.
    if(it.attribution){ const attr=document.createElement("span"); attr.className="rv-attr"; attr.textContent=it.attribution; attr.title="Suggested by "+it.attribution; meta.appendChild(attr); }
    const who=document.createElement("span"); who.textContent=(it.attribution?"":it.editor+" · ")+(it.origin||"human")+" · "+(it.kind||"prose"); meta.appendChild(who);
    if(it.page){ const pg=document.createElement("span"); pg.textContent=it.page; meta.appendChild(pg); }
    wrap.appendChild(meta);
    if(it.kind==="comment"){ const c=document.createElement("div"); c.className="rv-comment"; c.textContent="“"+(it.comment||"")+"”"; wrap.appendChild(c); }
    else { wrap.appendChild(diff(it.original_text, it.new_text)); }
    const actions=document.createElement("div"); actions.className="rv-actions";
    const note=document.createElement("input"); note.className="rv-note"; note.placeholder="Decline note (optional)";
    if(it.status!=="drift"){
      const acc=document.createElement("button"); acc.className="rv-accept"; acc.textContent="Accept";
      acc.disabled = !!it.group_id; if(it.group_id) acc.title="Accept the whole group above";
      acc.onclick=()=>decide("accept",{id:it.id});
      actions.appendChild(acc);
    } else {
      const re=document.createElement("button"); re.className="rv-reanchor"; re.textContent="Re-anchor (re-review)";
      re.onclick=()=>decide("reanchor",{id:it.id}); actions.appendChild(re);
    }
    const dec=document.createElement("button"); dec.className="rv-decline"; dec.textContent="Decline";
    dec.onclick=()=>decide("decline",{id:it.id}, note.value); actions.appendChild(dec);
    actions.appendChild(note);
    wrap.appendChild(actions);
    return wrap;
  }

  function render(){
    root.textContent="";
    const items=data.items||[];
    if(!items.length){ const e=document.createElement("div"); e.className="rv-empty"; e.textContent="Nothing pending. All caught up."; root.appendChild(e); return; }
    // Group by source_ref.
    const groups=new Map();
    for(const it of items){ if(!groups.has(it.source_ref)) groups.set(it.source_ref,[]); groups.get(it.source_ref).push(it); }
    for(const [src,its] of groups){
      const g=document.createElement("details"); g.className="rv-group"; g.open=true;
      const s=document.createElement("summary");
      const label=document.createElement("span"); label.textContent=its.length+" item"+(its.length===1?"":"s");
      const srcSpan=document.createElement("span"); srcSpan.className="rv-src"; srcSpan.textContent=src;
      s.appendChild(label); s.appendChild(srcSpan); g.appendChild(s);
      // group-level bulk (only for grouped companion sets sharing a group_id).
      const gid=its[0].group_id;
      if(gid && its.every(x=>x.group_id===gid)){
        const bulk=document.createElement("div"); bulk.className="rv-bulk";
        const ga=document.createElement("button"); ga.className="rv-accept"; ga.textContent="Accept group";
        ga.onclick=()=>decide("accept",{group_id:gid});
        const gd=document.createElement("button"); gd.className="rv-decline"; gd.textContent="Decline group";
        gd.onclick=()=>decide("decline",{group_id:gid});
        bulk.appendChild(ga); bulk.appendChild(gd); g.appendChild(bulk);
      }
      for(const it of its) g.appendChild(itemNode(it));
      root.appendChild(g);
    }
  }
  render();
})();
`;

// Publisher assets are deliberately separate from review.js: review decisions
// cannot accidentally acquire production authority, and a no-JS Publisher page
// remains a read-only immutable preview.
export const PUBLISHER_CSS = `.pub{max-width:66rem;margin:0 auto;padding:1.5rem 1.25rem 4rem}
.pub nav{margin-bottom:1.5rem}.pub header{border-bottom:2px solid var(--pp-accent);margin-bottom:1rem}
.pub-eyebrow{text-transform:uppercase;letter-spacing:.1em;font-size:.78rem;font-weight:700}
.pub-state,.pub-authority,.pub-targets{border:1px solid var(--pp-rule);border-radius:8px;padding:1rem;margin:1rem 0;background:#fff}
.pub-status{font-weight:700}.pub-targets{display:grid;gap:.7rem}.pub-targets label{display:block}
.pub-help,.pub-nojs,.pub-empty{color:#5c5346}.pub-binding{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:.65rem}
.pub-binding div{border-top:1px solid var(--pp-rule);padding-top:.35rem;min-width:0}.pub-binding dt{font-size:.75rem;text-transform:uppercase}
.pub-binding dd{margin:0;font-family:ui-monospace,SFMono-Regular,Menlo,monospace;overflow-wrap:anywhere}
.pub-change{border:1px solid var(--pp-rule);border-radius:8px;margin:1rem 0;overflow:hidden}.pub-change header{padding:.7rem;margin:0;background:#f3ecdd;display:flex;justify-content:space-between;gap:.5rem;flex-wrap:wrap}
.pub-redline{display:grid;grid-template-columns:1fr 1fr}.pub-redline section{padding:.8rem;min-width:0}.pub-redline section+section{border-left:1px solid var(--pp-rule)}
.pub-redline h4{margin:0 0 .4rem}.pub-redline p{white-space:pre-wrap;overflow-wrap:anywhere}.pub-redline del{text-decoration:line-through}.pub-redline ins{text-decoration:none;border-bottom:3px double currentColor}
.pub-confirm{display:block;margin:1rem 0;font-weight:700}.pub-authority button{font:inherit;padding:.65rem 1rem;min-height:44px}.pub-authority button:disabled{cursor:not-allowed;opacity:.6}
.pub-review{margin:1.5rem 0}.pub-counts,.pub-jump{display:flex;gap:.5rem;flex-wrap:wrap;margin:.8rem 0}
.pub-counts button,.pub-jump button,.pub-retry,.pub-submit button{font:inherit;min-height:44px;padding:.45rem .75rem;border:1px solid var(--pp-rule);border-radius:6px;background:#fff;color:var(--pp-ink)}
.pub-counts button[aria-pressed="true"]{background:var(--pp-ink);color:#fff}.pub-source{border:1px solid var(--pp-rule);border-radius:8px;margin:1rem 0;background:#fff;overflow:hidden}
.pub-source>header{padding:.75rem 1rem;margin:0;background:#f3ecdd}.pub-source h3,.pub-page{margin:.1rem 0;overflow-wrap:anywhere}.pub-page{font-size:.8rem;text-transform:uppercase;letter-spacing:.06em}
.pub-operation{padding:1rem;border-top:1px solid var(--pp-rule)}.pub-operation>header{display:flex;align-items:center;justify-content:space-between;gap:.5rem;margin:0 0 .5rem;border:0}.pub-operation h4{margin:0}
.pub-change-status{font-weight:700}.pub-excerpt{white-space:pre-wrap;overflow-wrap:anywhere;background:#faf7f0;border-left:4px solid var(--pp-rule);padding:.7rem;line-height:1.7}
.pub-sr-label{font-size:.75rem;font-weight:700;margin:0 .2rem}.pub-excerpt del{color:#8b1e2d;background:#fbd9df;text-decoration-thickness:2px}.pub-excerpt ins{color:#064d8c;background:#dbeeff;text-decoration:underline;text-decoration-thickness:2px}
.pub-operation:has([aria-label="Moved from"]),.pub-operation:has([aria-label="Moved to"]){border-left:4px solid #247346}.pub-operation [aria-label^="Moved"]{color:#175b35;background:#d9f1e3}
.pub-decision{border:0;padding:0;margin:.75rem 0}.pub-decision legend{font-weight:700;margin-bottom:.35rem}.pub-decision>label{display:inline-flex;align-items:center;gap:.25rem;margin:0 1rem .5rem 0;min-height:44px}
.pub-note{margin:.5rem 0}.pub-note label{display:block;font-weight:700}.pub-note textarea{font:inherit;width:100%;max-width:45rem;border:1px solid #675c4d;border-radius:5px;padding:.5rem}.pub-note[hidden]{display:none}
.pub-inline-error,.pub-error-summary{color:#721c24}.pub-error-summary{border:3px solid #b42331;background:#fff1f2;padding:.75rem;margin:1rem 0}.pub-error-summary a{color:inherit;font-weight:700}
.pub-save-state,.pub-retry,.pub-context{margin:.75rem 1rem}.pub-save-state[data-state="failed"]{color:#8b1e2d;font-weight:700}.pub-context p{white-space:pre-wrap;overflow-wrap:anywhere}.pub-submit{border:2px solid var(--pp-ink);padding:1rem;margin-top:1.5rem}.pub-submit h3{margin-top:0}
.pub :focus-visible{outline:3px solid var(--pp-focus);outline-offset:3px}.pub-live:focus{outline:none}
@media (max-width:640px){.pub-binding,.pub-redline{grid-template-columns:1fr}.pub-redline section+section{border-left:0;border-top:1px solid var(--pp-rule)}.pub-change header{display:block}}
@media (max-width:480px){.pub{padding:1rem .65rem 3rem}.pub-counts button,.pub-jump button{flex:1 1 9rem}.pub-operation{padding:.75rem}.pub-decision>label{display:flex;margin-right:0}.pub-operation>header{align-items:flex-start}.pub-submit button{width:100%}}
@media (forced-colors:active){.pub-excerpt del,.pub-excerpt ins,.pub-operation [aria-label^="Moved"]{forced-color-adjust:auto;color:CanvasText;background:Canvas;text-decoration-thickness:3px}.pub-operation:has([aria-label^="Moved"]){border-left:4px solid CanvasText}.pub-counts button[aria-pressed="true"]{outline:3px solid Highlight}}
`;

export const PUBLISHER_JS = `(() => {
  "use strict";
  const live=document.getElementById("pub-live");
  const reviewForm=document.getElementById("publisher-review-form");
  const pendingSaves=new Map(), saveTimers=new Map(), failedSaves=new Set(), dirtyRevisions=new Set();
  function sourceFor(node){return node&&node.closest(".pub-source")}
  function decisionFor(card){
    const checked=card.querySelector("input[type=radio]:checked");if(!checked)return null;
    const note=card.querySelector("[data-note-for="+checked.value+"]");
    return {operation_id:card.dataset.operationId,decision:checked.value,note:note?note.value.trim():""};
  }
  function payloadFor(source){return {review_revision_id:source.dataset.reviewRevision,
    source_revision:source.dataset.sourceRevision,prod_base:source.dataset.prodBase,
    decisions:Array.from(source.querySelectorAll(".pub-operation")).map(decisionFor).filter(Boolean)};}
  function setSaveState(source,state){const status=source.querySelector(".pub-save-state"),retry=source.querySelector(".pub-retry");
    status.dataset.state=state;status.textContent=state==="saving"?"Saving…":state==="saved"?"Saved":"Couldn’t save";
    retry.hidden=state!=="failed";}
  async function save(source){
    const id=source.dataset.reviewRevision;clearTimeout(saveTimers.get(id));saveTimers.delete(id);
    setSaveState(source,"saving");dirtyRevisions.add(id);
    const promise=fetch("/edit/v1/publisher/review/draft",{method:"POST",credentials:"same-origin",
      headers:{"content-type":"application/json","X-Edit-Request":"1"},body:JSON.stringify(payloadFor(source))});
    pendingSaves.set(id,promise);
    try{const response=await promise;if(!response.ok)throw new Error(String(response.status));
      if(pendingSaves.get(id)===promise){failedSaves.delete(id);dirtyRevisions.delete(id);setSaveState(source,"saved");}return true;
    }catch{if(pendingSaves.get(id)===promise){failedSaves.add(id);setSaveState(source,"failed");}return false}
    finally{if(pendingSaves.get(id)===promise)pendingSaves.delete(id)}
  }
  function schedule(source){const id=source.dataset.reviewRevision;dirtyRevisions.add(id);setSaveState(source,"saving");
    clearTimeout(saveTimers.get(id));saveTimers.set(id,setTimeout(()=>save(source),450));}
  function syncNotes(card){const value=card.querySelector("input[type=radio]:checked")?.value;
    for(const note of card.querySelectorAll(".pub-note"))note.hidden=note.querySelector("textarea").dataset.noteFor!==value;
    const question=card.querySelector('[data-note-for="questioned"]');if(question)question.required=value==="questioned";}
  function problem(card,message){const p=card.querySelector(".pub-inline-error"),question=card.querySelector('[data-note-for="questioned"]');
    p.hidden=!message;p.textContent=message||"";if(question)question.setAttribute("aria-invalid",message?"true":"false");return !message;}
  function validate(){const errors=[];for(const card of reviewForm.querySelectorAll(".pub-operation")){
    const decision=decisionFor(card);const bad=decision?.decision==="questioned"&&!decision.note;
    problem(card,bad?"Enter a question before submitting this change.":"");if(bad)errors.push(card);}
    const summary=document.getElementById("error-summary"),list=summary.querySelector("ul");list.textContent="";
    errors.forEach((card,index)=>{const li=document.createElement("li"),a=document.createElement("a");a.href="#"+card.id;
      a.textContent="Change "+(index+1)+" needs a question";li.appendChild(a);list.appendChild(li)});
    summary.hidden=!errors.length;if(errors.length)summary.focus();return !errors.length;}
  function refreshCounts(){const cards=Array.from(reviewForm.querySelectorAll(".pub-operation"));
    const values={all:cards.length,reviewed:0,unreviewed:0,accepted:0,rejected:0,questioned:0};
    for(const card of cards){const decision=decisionFor(card);const state=card.dataset.reviewStatus==="stale"?"stale":decision?.decision||"unreviewed";card.dataset.reviewStatus=state;
      card.querySelector(".pub-change-status").textContent=state.replace(/^./,c=>c.toUpperCase());if(state in values)values[state]+=1;if(decision)values.reviewed+=1;}
    for(const button of document.querySelectorAll("[data-filter]")){const count=button.querySelector("span");if(count)count.textContent=String(values[button.dataset.filter]||0)}}
  async function flush(){for(const source of reviewForm.querySelectorAll(".pub-source")){
    if(!source.querySelector(".pub-submitted"))await save(source);}
    await Promise.all(Array.from(pendingSaves.values()));return failedSaves.size===0;}
  async function digest(value){const bytes=await crypto.subtle.digest("SHA-256",new TextEncoder().encode(value));
    return Array.from(new Uint8Array(bytes),b=>b.toString(16).padStart(2,"0")).join("")}
  if(reviewForm){
    for(const card of reviewForm.querySelectorAll(".pub-operation")){syncNotes(card);
      card.addEventListener("change",()=>{syncNotes(card);problem(card,"");refreshCounts();schedule(sourceFor(card))});
      card.addEventListener("input",()=>schedule(sourceFor(card)));}
    for(const retry of reviewForm.querySelectorAll(".pub-retry"))retry.addEventListener("click",()=>save(sourceFor(retry)));
    window.addEventListener("beforeunload",event=>{if(dirtyRevisions.size||pendingSaves.size||failedSaves.size){event.preventDefault();event.returnValue=""}});
    reviewForm.addEventListener("submit",async event=>{event.preventDefault();const submit=document.getElementById("pub-submit-review");
      if(!validate())return;submit.disabled=true;submit.ariaBusy="true";live.textContent="Saving draft decisions before submission…";
      if(!await flush()){live.textContent="Couldn’t save every draft. Retry save before submitting.";submit.disabled=false;submit.ariaBusy="false";live.focus();return}
      try{const sources=Array.from(reviewForm.querySelectorAll(".pub-source")).filter(source=>!source.querySelector(".pub-submitted")).map(payloadFor);
        const body={sources},hash=await digest(JSON.stringify({sources}));body.id="review-"+hash;body.idempotency_key="review-submit-"+hash;
        const response=await fetch("/edit/v1/publisher/review/submit",{method:"POST",credentials:"same-origin",
          headers:{"content-type":"application/json","X-Edit-Request":"1"},body:JSON.stringify(body)});if(!response.ok)throw new Error(String(response.status));
        live.textContent="Review submitted. Accepted changes are eligible for a separate production preview; production is not authorized.";location.reload();
      }catch{live.textContent="Review submission failed. Your saved drafts remain available; correct any marked problem and try again.";submit.disabled=false;live.focus()}
      finally{submit.ariaBusy="false"}});
    function next(selector){const nodes=Array.from(reviewForm.querySelectorAll(selector)).filter(x=>!x.hidden);if(!nodes.length)return;
      const after=nodes.find(x=>x.getBoundingClientRect().top>8)||nodes[0];after.scrollIntoView({block:"center"});after.querySelector("input,textarea,button")?.focus()}
    document.getElementById("pub-next-unreviewed")?.addEventListener("click",()=>next('[data-review-status="unreviewed"]'));
    document.getElementById("pub-next-problem")?.addEventListener("click",()=>next('[data-review-status="questioned"],[data-review-status="stale"],.pub-operation:has([aria-invalid="true"])'));
    for(const filter of document.querySelectorAll("[data-filter]"))filter.addEventListener("click",()=>{for(const b of document.querySelectorAll("[data-filter]"))b.setAttribute("aria-pressed",String(b===filter));
      for(const card of reviewForm.querySelectorAll(".pub-operation"))card.hidden=filter.dataset.filter!=="all"&&card.dataset.reviewStatus!==filter.dataset.filter;});
  }
  const button=document.getElementById("pub-authorize"), confirm=document.getElementById("pub-confirm");
  if(!button||!confirm)return;
  let binding={}, pending=false;
  try{binding=JSON.parse(document.getElementById("publisher-binding").textContent)}catch{}
  const required=["id","target_batch_id","base_sha","candidate_sha","generator_id","evidence_hash","manifest_hash","membership_hash"];
  const complete=required.every(k=>typeof binding[k]==="string"&&binding[k]);
  async function authorizationKey(){
    const bound=[binding.id,binding.target_batch_id,binding.base_sha,binding.candidate_sha,binding.membership_hash].join("\\0");
    const bytes=await crypto.subtle.digest("SHA-256",new TextEncoder().encode(bound));
    return "publisher-"+Array.from(new Uint8Array(bytes),b=>b.toString(16).padStart(2,"0")).join("");
  }
  confirm.addEventListener("change",()=>{button.disabled=pending||!confirm.checked||!complete});
  button.addEventListener("click",async()=>{
    if(pending||button.disabled||!confirm.checked||!complete)return;
    pending=true;button.disabled=true; button.ariaBusy="true"; live.textContent="Authorizing the exact prepared release…";
    try{
      const body={...binding,idempotency_key:await authorizationKey()};
      const response=await fetch("/edit/v1/prod/releases/authorize",{method:"POST",credentials:"same-origin",
        headers:{"content-type":"application/json","X-Edit-Request":"1"},body:JSON.stringify(body)});
      let result={};try{result=await response.json()}catch{}
      if(response.ok){live.textContent=result.replay?"This exact release was already authorized.":"Release authorized. Automation may now publish only this frozen batch.";button.textContent="Authorized";confirm.disabled=true;}
      else{live.textContent=response.status===409?"The prepared preview changed or conflicts with an active release. Reload and review again.":response.status===403?"Authorization denied. A current human Publisher sign-in is required.":"Authorization failed. Production was not released.";}
    }catch{live.textContent="Authorization could not be sent. Production was not released.";}
    finally{pending=false;button.ariaBusy="false";if(button.textContent!=="Authorized")button.disabled=!confirm.checked||!complete;live.focus();}
  });
})();`;

// Minimal History-browser stubs — used only when the app/history/ client bundle
// is absent at build (unit tests). The real client (app/history/history.{js,css},
// 253/77 lines) overrides these via CLIENT.HISTORY_JS/HISTORY_CSS. The stub still
// reads the #history-data island and renders a plain-text timeline so the route
// is honest even without the built asset.
export const HISTORY_CSS = `#history-root{max-width:70ch;margin:1.5rem auto;padding:0 1.25rem}`;
export const HISTORY_JS = `(() => {"use strict";
  const el=document.getElementById("history-data");if(!el)return;
  let d;try{d=JSON.parse(el.textContent)}catch{return}
  const root=document.getElementById("history-root");if(!root)return;
  root.textContent=(d&&d.revisions?d.revisions.length:0)+" revision(s) — history client asset not built.";
})();`;

// The real editor client, inlined at build by bundle-editor-data.mjs. When the
// app/editor/ bundle is present these override the stubs above; when absent
// (unit tests) the module is empty and the stubs are used.
import * as CLIENT from "../editor-data/editor-client.generated.js";

export function serveAsset(name) {
  const map = {
    "editor.css": [CLIENT.EDITOR_CSS || EDITOR_CSS, "text/css; charset=utf-8"],
    "editor.js": [CLIENT.EDITOR_JS || EDITOR_JS, "text/javascript; charset=utf-8"],
    "review.css": [REVIEW_CSS, "text/css; charset=utf-8"],
    "review.js": [REVIEW_JS, "text/javascript; charset=utf-8"],
    "publisher.css": [PUBLISHER_CSS, "text/css; charset=utf-8"],
    "publisher.js": [PUBLISHER_JS, "text/javascript; charset=utf-8"],
    "history.css": [CLIENT.HISTORY_CSS || HISTORY_CSS, "text/css; charset=utf-8"],
    "history.js": [CLIENT.HISTORY_JS || HISTORY_JS, "text/javascript; charset=utf-8"],
  };
  const hit = map[name];
  if (!hit) return null;
  return new Response(hit[0], { status: 200, headers: { "content-type": hit[1] } });
}
