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
  // <main>, in document order — p,li,h1-h6,blockquote (outermost). Assign index.
  const TAGS = new Set(["P","LI","H1","H2","H3","H4","H5","H6","BLOCKQUOTE"]);
  const editable = new Map((map.blocks||[]).map(b => [b.index, b]));
  const main = document.querySelector("main");
  if (main) {
    const candidates = [];
    (function walk(node){
      for (const child of node.children){
        if (TAGS.has(child.tagName)) { candidates.push(child); }
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
.pr-review{margin:2rem 0;border-top:3px solid var(--pp-accent);padding-top:1rem}
.pr-lane,.pr-candidate{border:1px solid var(--pp-rule);border-radius:8px;background:#fff;padding:1rem;margin:.75rem 0}
.pr-lane[data-health="stalled"],.pr-lane[data-health="unavailable"],.pr-lane[data-health="restore_failed"]{border-left:6px solid #842029}
.pr-stage{font-weight:700;text-transform:capitalize}.pr-secondary{color:#5c5346}.pr-delayed{color:#842029;font-weight:700}
.pr-timeline{padding-left:1.4rem}.pr-timeline li{margin:.45rem 0}.pr-actions{display:flex;gap:.65rem;flex-wrap:wrap;align-items:center}
.pr-actions button,.pr-actions a{font:inherit;min-height:44px;padding:.5rem .8rem;border-radius:6px}
.pr-rationale{display:block;width:100%;min-height:5rem;font:inherit;padding:.55rem;border:1px solid #6b5b46;border-radius:6px}
.pr-error{color:#842029}.pr-alert{border:2px solid #842029;background:#fff3f3;padding:.8rem;margin:.8rem 0}
.pr-confirm{display:flex;gap:.5rem;align-items:flex-start}.pr-evidence{border-left:3px solid var(--pp-rule);padding-left:1rem}
.pr-review :focus-visible{outline:3px solid var(--pp-focus);outline-offset:3px}
@media (max-width:640px){.pr-actions{display:grid;grid-template-columns:1fr}.pr-actions>*{width:100%}.pr-confirm{min-height:44px}.pr-review{overflow-wrap:anywhere}}
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
  const promotionEl = document.getElementById("promotion-data");
  let promotionData = { candidates: [], lane: null, manifest_epoch: "" };
  try { if(promotionEl) promotionData=JSON.parse(promotionEl.textContent); } catch {}
  const promotionRoot=document.getElementById("pr-root"), promotionAlert=document.getElementById("pr-alert");

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
  const stageInfo={saved:["Saved","Waiting for the promotion lane"],validating:["Validating","Deterministic gates and bounded risk review are running"],
    preview_ready:["Preview ready","Bound preview and evidence are ready"],awaiting_approval:["Awaiting approval","An admin decision is required"],
    publishing:["Publishing","Activation and live verification are in progress"],published:["Published","Live verification and main completion are verified"],
    failed:["Failed","Not published; review the evidence before retrying"]};
  function stamp(ms){if(!Number.isFinite(ms))return "Timestamp unavailable";return new Date(ms).toISOString().replace("T"," ").replace(".000Z"," UTC");}
  function lifecycleSecondary(c,lane){
    const code=String(lane&&lane.reason_code||"");
    if(code.includes("live_verification"))return "Live verification is checking the exact Pages and Worker release.";
    if(code.includes("maintenance")||lane&&lane.paused&&c.stage==="publishing")return "Activation maintenance: writes are temporarily paused; reads and recovery remain available.";
    if(code.includes("restor")&&lane&&lane.health==="restore_failed")return "Restoration could not be verified. The lane is fenced; no publication or editor write is allowed.";
    if(code.includes("restor")&&code.includes("verified"))return "The prior known-good release was restored and verified.";
    if(code.includes("restor"))return "Restoring the prior known-good Pages and Worker pair.";
    return (stageInfo[c.stage]||[c.stage,"Status reported by the promotion ledger"])[1];
  }
  function announceChanged(){if(!promotionAlert)return;promotionAlert.hidden=false;promotionAlert.textContent="Changed evidence: this candidate was revalidated. Your rationale is preserved; review the refreshed evidence and confirm again.";promotionAlert.focus();}
  async function loadDetail(c){
    const q=new URLSearchParams({id:c.id,attempt_id:c.attempt_id,base_sha:c.base_sha,evidence_hash:c.evidence_hash,manifest_hash:c.manifest_hash});
    const r=await fetch("/edit/v1/prod/candidate?"+q.toString(),{credentials:"same-origin"});
    return r.ok?(await r.json()).candidate:null;
  }
  async function refreshPromotions(){
    const r=await fetch("/edit/v1/prod/candidates",{credentials:"same-origin"});
    if(r.ok){promotionData.candidates=(await r.json()).candidates||[];renderPromotions();}
  }
  async function promotionDecision(c,decision,rationale,confirmed,controls,error){
    if(!confirmed){error.textContent="Confirm that you reviewed this exact evidence before deciding.";return;}
    for(const button of controls)button.disabled=true;error.textContent="";
    const body={candidate_id:c.id,attempt_id:c.attempt_id,decision,base_sha:c.base_sha,evidence_hash:c.evidence_hash,
      manifest_hash:c.manifest_hash,rationale,idempotency_key:c.id+":"+c.attempt_id+":"+decision+":"+Date.now(),manifest_epoch:promotionData.manifest_epoch};
    const r=await fetch("/edit/v1/prod/decision",{method:"POST",credentials:"same-origin",headers:{"content-type":"application/json","X-Edit-Request":"1"},body:JSON.stringify(body)});
    if(r.ok){await refreshPromotions();const next=promotionRoot&&promotionRoot.querySelector("button");if(next)next.focus();return;}
    for(const button of controls)button.disabled=false;
    if(r.status===409){await refreshPromotions();announceChanged();return;}
    error.textContent="The decision was not recorded. Nothing was published; try again.";
  }
  async function openPromotion(c,card,trigger){
    if(card.dataset.promotionBusy==="1")return;card.dataset.promotionBusy="1";trigger.disabled=true;
    let detail;try{detail=await loadDetail(c);}catch{card.dataset.promotionBusy="0";trigger.disabled=false;announceChanged();return;}
    if(!detail){await refreshPromotions();announceChanged();return;}
    let panel=card.querySelector(".pr-detail");panel.hidden=false;panel.textContent="";
    const evidence=document.createElement("section");evidence.className="pr-evidence";
    const h=document.createElement("h4");h.textContent="Validation evidence";evidence.appendChild(h);
    const ul=document.createElement("ul");for(const g of (detail.evidence&&detail.evidence.gates||[])){const li=document.createElement("li");li.textContent=(g.name||"Gate")+": "+(g.status||"unknown")+(g.summary?" — "+g.summary:"");ul.appendChild(li);}evidence.appendChild(ul);
    if(detail.score){const p=document.createElement("p");p.textContent="Confidence: "+(detail.score.confidence==null?"unavailable":detail.score.confidence);evidence.appendChild(p);}
    panel.appendChild(evidence);
    const label=document.createElement("label");label.textContent="Decision rationale";const rationale=document.createElement("textarea");rationale.className="pr-rationale";rationale.name="rationale";label.appendChild(rationale);panel.appendChild(label);
    const confirmLabel=document.createElement("label");confirmLabel.className="pr-confirm";const confirm=document.createElement("input");confirm.type="checkbox";confirmLabel.appendChild(confirm);confirmLabel.appendChild(document.createTextNode("I reviewed this exact attempt and evidence."));panel.appendChild(confirmLabel);
    const error=document.createElement("p");error.className="pr-error";error.setAttribute("role","alert");panel.appendChild(error);
    const actions=document.createElement("div");actions.className="pr-actions";
    for(const d of ["approve","decline"]){const b=document.createElement("button");b.type="button";b.textContent=d==="approve"?"Approve promotion":"Decline promotion";b.onclick=()=>promotionDecision(c,d,rationale.value,confirm.checked,actions.querySelectorAll("button"),error);actions.appendChild(b);}panel.appendChild(actions);
    const close=document.createElement("button");close.type="button";close.textContent="Return to queue";close.onclick=()=>{panel.hidden=true;card.dataset.promotionBusy="0";trigger.disabled=false;trigger.focus();};actions.appendChild(close);rationale.focus();
  }
  function renderPromotions(){
    if(!promotionRoot)return;promotionRoot.textContent="";const lane=promotionData.lane;
    const laneBox=document.createElement("section");laneBox.className="pr-lane";laneBox.dataset.health=lane&&lane.health||"unavailable";
    laneBox.setAttribute("aria-label","Promotion lane status");laneBox.textContent=lane?("Lane: "+(lane.paused?"maintenance paused":lane.health||"unknown")+" — updated "+stamp(lane.updated_at)):"Lane unavailable. Saves cannot advance until service returns.";promotionRoot.appendChild(laneBox);
    const cs=(promotionData.candidates||[]).slice().sort((a,b)=>(a.created_at||0)-(b.created_at||0)||String(a.id).localeCompare(String(b.id)));
    if(!cs.length){const p=document.createElement("p");p.className="rv-empty";p.textContent="No promotion candidates need attention.";promotionRoot.appendChild(p);return;}
    for(const c of cs){const card=document.createElement("article");card.className="pr-candidate";const h=document.createElement("h3");h.textContent=c.source_ref||"Promotion candidate";card.appendChild(h);
      const info=stageInfo[c.stage]||[String(c.stage||"Unknown").replace(/_/g," "),"Status reported by the promotion ledger"];
      const p=document.createElement("p");p.className="pr-stage";p.textContent=info[0]+" — "+stamp(c.stage_at);card.appendChild(p);
      const sec=document.createElement("p");sec.className="pr-secondary";sec.textContent=lifecycleSecondary(c,lane);card.appendChild(sec);
      if(c.stage!=="awaiting_approval"&&c.stage!=="published"&&Number.isFinite(c.created_at)&&Date.now()-c.created_at>300000){const delay=document.createElement("p");delay.className="pr-delayed";delay.textContent="Delayed beyond the normal five-minute publication window.";card.appendChild(delay);}
      const timeline=document.createElement("ol");timeline.className="pr-timeline";timeline.setAttribute("aria-label","Promotion timeline");for(const s of ["saved","validating","preview_ready","awaiting_approval","publishing","published"]){const li=document.createElement("li");li.textContent=(stageInfo[s]||[s])[0];if(s===c.stage)li.setAttribute("aria-current","step");timeline.appendChild(li);}card.appendChild(timeline);
      const actions=document.createElement("div");actions.className="pr-actions";if(c.preview_href){const a=document.createElement("a");a.href=c.preview_href;a.target="_blank";a.rel="noopener";a.textContent="Open bound preview";actions.appendChild(a);}if(c.stage==="awaiting_approval"){const b=document.createElement("button");b.type="button";b.textContent="Review evidence and decide";b.onclick=()=>openPromotion(c,card,b);actions.appendChild(b);}card.appendChild(actions);
      const detail=document.createElement("div");detail.className="pr-detail";detail.hidden=true;card.appendChild(detail);promotionRoot.appendChild(card);}
  }
  render();
  renderPromotions();
})();
`;

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
    "history.css": [CLIENT.HISTORY_CSS || HISTORY_CSS, "text/css; charset=utf-8"],
    "history.js": [CLIENT.HISTORY_JS || HISTORY_JS, "text/javascript; charset=utf-8"],
  };
  const hit = map[name];
  if (!hit) return null;
  return new Response(hit[0], { status: 200, headers: { "content-type": hit[1] } });
}
