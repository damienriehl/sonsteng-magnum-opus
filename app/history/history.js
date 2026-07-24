/* history.js — client for the editor-gated redline History browser.
 *
 * Served at /edit/assets/history.js (CSP script-src 'self'; no inline). Reads a
 * single-document data island written by the Worker's /edit/history/<doc> shell:
 *
 *   <script id="history-data" type="application/json"> ...doc slice... </script>
 *
 * shaped exactly like build_history.py's per-doc bundle object
 *   { doc, slug, revisions:[...newest-first], baselines:[...], diffs:{key:{...}} }
 *
 * Renders: baseline markers, an attributed revision timeline (JOS/RSH/DVR/APPLY
 * chips + edit/external/revert/baseline kind badges), a redline viewer, and a
 * COMPARE picker limited to the precomputed diff set (no arbitrary any-vs-any —
 * the pairs the generator did not pre-render are simply not offered).
 *
 * REVERT CONTRACT: a "Request revert" button per revision. If the Worker exposes
 * POST /edit/v1/revert-request (feature-detected via window.__HX_REVERT__), it
 * POSTs { doc, run:[first,last] } (admin-executed). That endpoint does NOT exist
 * yet (checked 2026-07-19), so the button renders DISABLED with the title
 * "lands with the daemon's next update" and the contract is documented here and
 * in docs/notes/history-browser.md. The redline HTML in diffs[*].html is
 * pre-rendered + fully escaped by tools/render_diff_lib.py (only <ins>/<del>/
 * <details>/<summary> tags survive), so assigning it to innerHTML is safe. */
(() => {
  "use strict";

  function island(id) {
    const el = document.getElementById(id);
    if (!el) return null;
    try { return JSON.parse(el.textContent); } catch { return null; }
  }

  const DATA = island("history-data");
  const root = document.getElementById("history-root");
  if (!root) return;
  if (!DATA || !Array.isArray(DATA.revisions)) {
    root.textContent = "No history available for this document.";
    return;
  }

  const REVERT_ENDPOINT =
    (typeof window !== "undefined" && window.__HX_REVERT__) || null; // absent today

  const el = (tag, cls, text) => {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  };
  const fmt = (iso) => {
    const d = new Date(iso);
    return isNaN(d) ? iso : d.toLocaleString(undefined,
      { year: "numeric", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
  };
  const key = (from, to) => from + ".." + to;
  const diffs = DATA.diffs || {};

  function toast(msg) {
    let t = document.querySelector(".hx-toast");
    if (!t) { t = el("div", "hx-toast"); document.body.appendChild(t); }
    t.textContent = msg; t.classList.add("show");
    setTimeout(() => t.classList.remove("show"), 2200);
  }

  // ---- header ----
  const head = el("div", "hx-head");
  head.appendChild(el("h1", null, "Change history · " + DATA.doc));
  const sub = el("div", "hx-sub");
  sub.textContent = DATA.revisions.length + " revision" +
    (DATA.revisions.length === 1 ? "" : "s") +
    (DATA.baselines && DATA.baselines.length ? " · " + DATA.baselines.length + " baseline(s)" : "") +
    (DATA.dropped_pairs ? " · " + DATA.dropped_pairs + " older any-vs-any comparisons not pre-rendered" : "");
  head.appendChild(sub);
  root.appendChild(head);

  const layout = el("div", "hx-layout");
  const rail = el("div", "hx-rail");
  const viewer = el("div", "hx-viewer");
  layout.appendChild(rail);
  layout.appendChild(viewer);
  root.appendChild(layout);

  // ---- baselines ----
  if (DATA.baselines && DATA.baselines.length) {
    const box = el("div", "hx-baselines");
    box.appendChild(el("h2", null, "Baselines"));
    DATA.baselines.forEach((b) => {
      const tag = el("span", "hx-baseline-tag", "★ " + b.name);
      tag.title = (b.message || "") + " · " + (b.date || "");
      tag.setAttribute("role", "button");
      tag.tabIndex = 0;
      const head_tip = DATA.revisions.length ? DATA.revisions[0].tip : null;
      const act = () => head_tip && showDiff(b.name, head_tip,
        "Baseline “" + b.name + "” → current");
      tag.addEventListener("click", act);
      tag.addEventListener("keydown", (e) => { if (e.key === "Enter") act(); });
      box.appendChild(tag);
    });
    rail.appendChild(box);
  }

  // ---- timeline ----
  const revNodes = [];
  DATA.revisions.forEach((r, i) => {
    const node = el("div", "hx-rev");
    node.setAttribute("role", "button");
    node.tabIndex = 0;
    node.setAttribute("aria-selected", "false");

    const top = el("div", "hx-rev-top");
    const chip = el("span", "hx-chip", r.attribution || "?");
    chip.setAttribute("data-who", r.attribution || "");
    chip.title = r.author || "";
    top.appendChild(chip);
    const kind = el("span", "hx-kind", r.kind);
    kind.setAttribute("data-kind", r.kind);
    top.appendChild(kind);
    if (r.baselines && r.baselines.length) {
      const bl = el("span", "hx-rev-baselines");
      bl.appendChild(el("span", "hx-mini", "★ " + r.baselines.join(", ")));
      top.appendChild(bl);
    }
    node.appendChild(top);
    node.appendChild(el("div", "hx-rev-summary", r.summary || "(no summary)"));
    const when = el("div", "hx-rev-when");
    when.textContent = fmt(r.ts_end) +
      (r.n_commits > 1 ? " · " + r.n_commits + " commits" : "");
    node.appendChild(when);

    const select = () => {
      revNodes.forEach((n) => n.setAttribute("aria-selected", "false"));
      node.setAttribute("aria-selected", "true");
      showDiff(r.parent, r.tip, revLabel(r), r);
    };
    node.addEventListener("click", select);
    node.addEventListener("keydown", (e) => { if (e.key === "Enter") select(); });
    revNodes.push(node);
    rail.appendChild(node);
  });

  function revLabel(r) {
    return (r.parent === "EMPTY" ? "First revision" : "Revision") +
      " · " + (r.attribution || "?") + " · " + fmt(r.ts_end);
  }

  // ---- viewer + compare controls ----
  const controls = el("div", "hx-controls");
  controls.appendChild(el("label", null, "Compare:"));
  const selFrom = el("select");
  const selTo = el("select");
  // Options come ONLY from the precomputed diff set (cap-honoring). Each option
  // value is a "from..to" key; label describes the endpoints.
  const optionsByKey = buildCompareOptions();
  optionsByKey.forEach((label, k) => {
    const o1 = el("option", null, label); o1.value = k; selFrom.appendChild(o1);
  });
  // selTo mirrors selFrom (choosing either drives the same key list); we keep a
  // single unified picker: selFrom picks the comparison, selTo is hidden helper.
  controls.appendChild(selFrom);
  const goBtn = el("button", null, "Show");
  goBtn.addEventListener("click", () => {
    const k = selFrom.value;
    const d = diffs[k];
    if (d) renderRedline(d, optionsByKey.get(k)); else toast("comparison not pre-rendered");
  });
  controls.appendChild(goBtn);
  viewer.appendChild(controls);

  const legend = el("div", "hx-legend",
    "Green = added, red = removed. Long unchanged stretches collapse — click to expand.");
  viewer.appendChild(legend);

  const counts = el("div", "hx-counts");
  viewer.appendChild(counts);
  const redline = el("div", "hx-redline");
  redline.appendChild(el("div", "hx-empty", "Select a revision on the left to see its redline."));
  viewer.appendChild(redline);

  const actions = el("div", "hx-actions");
  const revertBtn = el("button", "hx-revert", "Request revert");
  const revertNote = el("span", "hx-note");
  if (!REVERT_ENDPOINT) {
    revertBtn.disabled = true;
    revertBtn.title = "lands with the daemon's next update";
    revertNote.textContent =
      "Revert is admin-executed. Endpoint POST /edit/v1/revert-request lands with the daemon; " +
      "contract: { doc, run:[first,last] }.";
  }
  actions.appendChild(revertBtn);
  actions.appendChild(revertNote);
  viewer.appendChild(actions);

  let currentRun = null; // [first,last] of the selected revision (for revert)

  function buildCompareOptions() {
    // Map key -> human label, restricted to keys present in diffs. Prefer showing
    // revision/baseline/adjacent categories with readable endpoints.
    const shortSha = (s) => (s === "EMPTY" ? "∅ (empty)" : /^[0-9a-f]{7,}$/i.test(s) ? s.slice(0, 8) : s);
    const m = new Map();
    Object.keys(diffs).forEach((k) => {
      const d = diffs[k];
      const cat = d.category || "";
      const label = "[" + cat + "] " + shortSha(d.from) + " → " + shortSha(d.to);
      m.set(k, label);
    });
    return m;
  }

  function showDiff(from, to, label, rev) {
    currentRun = rev ? rev.run : null;
    revertBtn.disabled = !REVERT_ENDPOINT || !currentRun;
    if (REVERT_ENDPOINT && currentRun) {
      revertBtn.title = "Request admin-executed revert of this revision";
    }
    const d = diffs[key(from, to)];
    if (!d) {
      // fall back: some viewers may request a pair outside the precomputed set.
      redline.textContent = "";
      redline.appendChild(el("div", "hx-empty",
        "This comparison was not pre-rendered (older than the last " +
        (DATA.anyvsany_cap || 20) + " revisions). Use a per-revision or baseline redline."));
      counts.textContent = "";
      return;
    }
    renderRedline(d, label);
  }

  function renderRedline(d, label) {
    counts.textContent = (label ? label + "  —  " : "") +
      d.n_ins + " insertion region(s) · " + d.n_del + " deletion region(s)";
    const pre = el("pre");
    // SAFE: d.html is build-time, escaped by render_diff_lib (only ins/del/
    // details/summary tags). Assigned to innerHTML to render the markup.
    pre.innerHTML = d.html || "";
    redline.textContent = "";
    redline.appendChild(pre);
  }

  revertBtn.addEventListener("click", async () => {
    if (!REVERT_ENDPOINT || !currentRun) return;
    try {
      const r = await fetch(REVERT_ENDPOINT, {
        method: "POST", credentials: "same-origin",
        headers: { "content-type": "application/json", "X-Edit-Request": "1" },
        body: JSON.stringify({ doc: DATA.doc, run: currentRun }),
      });
      toast(r.ok ? "Revert requested" : "Revert failed (" + r.status + ")");
    } catch { toast("Revert request errored"); }
  });

  // Auto-select the newest revision so the viewer is never empty.
  if (revNodes.length) revNodes[0].click();
})();
