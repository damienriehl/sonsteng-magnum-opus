// Human Publisher surface. Preparation remains a release-service operation;
// this page can authorize only the exact immutable `prepared` record returned
// by the store. With JavaScript absent it is deliberately read-only.
import { escapeHtml, escapeJsonIsland } from "./editor-map.js";
import { attributionLabel } from "./editor-auth.js";

const STATE = Object.freeze({
  draft: ["Draft", "Choose the end of the next contiguous DEV apply frontier. Preparation freezes the evidence before release is possible."],
  prepared: ["Prepared", "An immutable production preview is ready for Publisher review."],
  authorized: ["Authorized", "A Publisher authorized this exact batch. Release automation may now claim it."],
  executing: ["Publishing", "Automation is releasing the authorized manifest. No further Publisher action is available."],
  delayed: ["Delayed", "The authorized release is waiting. Its membership remains frozen."],
  failed_fenced: ["Failed and fenced", "Publishing stopped and later releases are fenced. An operator must reconcile the recorded manifest."],
  "failed-fenced": ["Failed and fenced", "Publishing stopped and later releases are fenced. An operator must reconcile the recorded manifest."],
  restoring: ["Restoring", "Automation is restoring the recorded production manifest."],
  restored: ["Restored", "Production was restored. Publishing a different target requires a new Publisher authorization."],
  verified: ["Verified", "Pages and the production Worker agree on the authorized release identity."],
  complete: ["Published", "The authorized batch is verified and complete in production."],
});

function short(value) {
  const s = String(value || "");
  return s.length > 16 ? `${s.slice(0, 12)}…${s.slice(-4)}` : s;
}

export function publisherViewModel(context = {}) {
  const batches = Array.isArray(context.batches) ? context.batches : [];
  let enclosedChanges = 0;
  const targets = batches.map((batch) => {
    enclosedChanges += Array.isArray(batch.changes) ? batch.changes.length : 0;
    return { ...batch, enclosedChanges };
  });
  const release = context.release && typeof context.release === "object" ? context.release : null;
  const state = release?.state || "draft";
  return {
    batches, targets, release, state,
    eligibleChanges: enclosedChanges,
    productionStatus: enclosedChanges && (!release || state === "prepared")
      ? "Available on DEV — waiting for Publisher"
      : (STATE[state]?.[0] || String(state).replace(/_/g, " ")),
  };
}

function renderTargets(vm) {
  if (!vm.targets.length) return "<p class=\"pub-empty\">No complete DEV apply batch is eligible for production.</p>";
  return "<fieldset class=\"pub-targets\"><legend>Next contiguous frontier</legend>" +
    vm.targets.map((b, i) => "<label><input type=\"radio\" name=\"target_batch_id\" value=\"" +
      escapeHtml(b.batch_id) + "\"" + (i === vm.targets.length - 1 ? " checked" : "") + "> " +
      "Through <strong>" + escapeHtml(b.batch_id) + "</strong> — " + b.enclosedChanges +
      " enclosed change" + (b.enclosedChanges === 1 ? "" : "s") + "</label>").join("") +
    "<button type=\"button\" disabled aria-describedby=\"pub-prepare-help\">Prepare immutable preview</button>" +
    "<p class=\"pub-help\" id=\"pub-prepare-help\">Choosing a target does not publish it. The trusted release service will enable preparation when it can freeze the exact evidence; authorization is unavailable until then.</p></fieldset>";
}

function renderChanges(vm) {
  const releaseIds = new Set(vm.release?.suggestion_ids || []);
  const batches = vm.release
    ? vm.batches.filter((b) => (vm.release.batches || []).some((rb) => rb.batch_id === b.batch_id))
    : vm.batches;
  const rows = [];
  for (const batch of batches) for (const change of (batch.changes || [])) {
    if (vm.release && !releaseIds.has(change.id)) continue;
    rows.push("<article class=\"pub-change\"><header><strong>" + escapeHtml(change.source_ref || change.id) +
      "</strong><span>Suggested by " + escapeHtml(attributionLabel(change.editor) || "Unknown") +
      (change.group_id ? " · group " + escapeHtml(change.group_id) : "") + "</span></header>" +
      "<div class=\"pub-redline\"><section><h4>Before</h4><p><del>" + escapeHtml(change.original_text || "") +
      "</del></p></section><section><h4>After</h4><p><ins>" + escapeHtml(change.new_text || "") +
      "</ins></p></section></div></article>");
  }
  return rows.length ? rows.join("") : "<p class=\"pub-empty\">No enclosed change detail is available.</p>";
}

function renderEvidence(release) {
  if (!release) return "";
  const fields = [["Release", release.id], ["Target", release.target_environment],
    ["Last apply batch", release.target_batch_id], ["Production base", release.base_sha],
    ["Candidate", release.candidate_sha], ["Generator", release.generator_id],
    ["Membership", release.membership_hash], ["Evidence", release.evidence_hash],
    ["Manifest", release.manifest_hash]];
  const batches = (release.batches || []).map((b) => "<li><strong>" + escapeHtml(b.batch_id) +
    "</strong> — commit " + escapeHtml(short(b.commit_sha)) + "</li>").join("");
  const events = (release.events || []).map((event) => {
    const iso = Number.isFinite(Number(event.created_at)) ? new Date(Number(event.created_at)).toISOString() : "";
    return "<li><strong>" + escapeHtml(String(event.type || "event").replace(/_/g, " ")) + "</strong> by " +
      escapeHtml(attributionLabel(event.actor) || event.actor || "system") +
      (iso ? " at <time datetime=\"" + escapeHtml(iso) + "\">" + escapeHtml(iso) + "</time>" : "") + "</li>";
  }).join("");
  return "<dl class=\"pub-binding\">" + fields.map(([k, v]) => "<div><dt>" + k +
    "</dt><dd title=\"" + escapeHtml(v || "") + "\">" + escapeHtml(short(v)) + "</dd></div>").join("") + "</dl>" +
    (batches ? "<h3>Complete enclosed apply batches</h3><ol class=\"pub-batches\">" + batches + "</ol>" : "") +
    (events ? "<h3>Release events</h3><ol class=\"pub-events\">" + events + "</ol>" : "");
}

export function renderPublisherPage(context = {}, viewerLabel = "") {
  const vm = publisherViewModel(context);
  const release = vm.release;
  const [label, explanation] = STATE[vm.state] || [vm.state, "No release action is available for this state."];
  const binding = release ? escapeJsonIsland({ id: release.id, target_batch_id: release.target_batch_id,
    base_sha: release.base_sha, candidate_sha: release.candidate_sha, generator_id: release.generator_id,
    evidence_hash: release.evidence_hash, manifest_hash: release.manifest_hash,
    membership_hash: release.membership_hash }) : "{}";
  const authorize = vm.state === "prepared" ?
    "<section class=\"pub-authority\"><h2>Authorize this exact production release</h2>" +
    "<p><strong>Consequence:</strong> automation may publish only release <strong>" + escapeHtml(release.id) +
    "</strong>, through batch <strong>" + escapeHtml(release.target_batch_id) + "</strong>, containing <strong>" +
    (release.suggestion_ids || []).length + " changes</strong>, from <strong>" + escapeHtml(short(release.base_sha)) +
    "</strong> to <strong>" + escapeHtml(short(release.candidate_sha)) + "</strong>.</p>" +
    "<label class=\"pub-confirm\"><input id=\"pub-confirm\" type=\"checkbox\"> I reviewed every enclosed change and authorize this exact production batch.</label>" +
    "<button type=\"button\" id=\"pub-authorize\" disabled>Authorize release to production</button>" +
    "<p class=\"pub-nojs\">JavaScript is required for the signed, same-origin authorization request. Without it this page remains review-only.</p></section>" : "";
  const html = "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">" +
    "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\"><title>Production Publisher — Sonsteng</title>" +
    "<link rel=\"stylesheet\" href=\"/edit/assets/editor.css\"><link rel=\"stylesheet\" href=\"/edit/assets/publisher.css\"></head>" +
    "<body><main class=\"pub\" data-release-state=\"" + escapeHtml(vm.state) + "\"><nav aria-label=\"Editor\"><a href=\"/edit/review\">Suggestion review</a> · <a href=\"/edit/history/\">History</a></nav>" +
    "<header><p class=\"pub-eyebrow\">Human publication gate" + (viewerLabel ? " · " + escapeHtml(viewerLabel) : "") +
    "</p><h1>Production Publisher</h1><p>Approval and DEV availability do not publish production.</p></header>" +
    "<section class=\"pub-state\" aria-labelledby=\"pub-state-title\"><h2 id=\"pub-state-title\">" + escapeHtml(label) +
    "</h2><p>" + escapeHtml(explanation) + "</p><p class=\"pub-status\">" + escapeHtml(vm.productionStatus) + "</p></section>" +
    (!release ? renderTargets(vm) : "") +
    "<section><h2>" + (release ? "Immutable prepared preview" : "Eligible DEV changes") + "</h2>" +
    renderChanges(vm) +
    "<details><summary>Evidence, manifest, and event record</summary>" + renderEvidence(release) +
    "<p>Secondary evidence is disclosed here; the exact identity and consequence remain above the release control.</p></details></section>" +
    authorize + "<p id=\"pub-live\" class=\"pub-live\" aria-live=\"polite\" tabindex=\"-1\"></p></main>" +
    "<script type=\"application/json\" id=\"publisher-binding\">" + binding + "</script>" +
    "<script src=\"/edit/assets/publisher.js\" defer></script></body></html>";
  return new Response(html, { status: 200, headers: { "content-type": "text/html; charset=utf-8" } });
}
