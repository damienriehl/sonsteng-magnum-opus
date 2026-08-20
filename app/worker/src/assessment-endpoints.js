// Scope-gated read, page, and override endpoints for U13. The store's literal
// capability is constructed here only after a current Access-authenticated
// damienadmin request passes the human-review gate.
import { json } from "./errors.js";
import { csrfOk, editError, uniform404 } from "./editor-http.js";
import { attributionLabel } from "./editor-auth.js";
import { renderAssessmentReviewPage } from "./assessment-view.js";

const STORE_REVIEW_SCOPES = Object.freeze({
  "assessment-review": Object.freeze({ granted: true, ver: 1 }),
});
const HEADING_IDS = new Set([
  "governing_law",
  "strengths_and_weaknesses_both_sides",
  "issues",
  "suggested_solutions",
  "theory_and_themes",
  "elements_to_prevail",
  "liabilities_and_remedies",
]);

function editorStub(env) {
  return env.EDITOR.getByName("global-v1");
}

export function assessmentReviewerScopes(auth) {
  const allowed = auth?.slot === "damienadmin" && auth?.editor === "slot:damienadmin" &&
    auth?.credential_channel === "access" && auth?.service !== true &&
    auth?.scopes?.admin?.granted === true && auth?.scopes?.instructor?.granted === true;
  return allowed ? STORE_REVIEW_SCOPES : null;
}

function assessmentIdFromPage(request) {
  const path = new URL(request.url).pathname;
  const prefix = "/edit/assessments/";
  if (!path.startsWith(prefix)) return null;
  try {
    return decodeURIComponent(path.slice(prefix.length).replace(/\/+$/, ""));
  } catch {
    return null;
  }
}

async function readRecord(env, id, scopes) {
  return editorStub(env).readAssessmentAudit({ id, scopes });
}

export async function assessmentReadEndpoint(request, env, auth) {
  const scopes = assessmentReviewerScopes(auth);
  if (!scopes) return uniform404();
  const id = new URL(request.url).searchParams.get("id");
  const result = await readRecord(env, id, scopes);
  if (!result?.ok) return uniform404();
  return json({ ok: true, record: result.record });
}

export async function assessmentPageEndpoint(request, env, auth) {
  const scopes = assessmentReviewerScopes(auth);
  if (!scopes) return uniform404();
  const id = assessmentIdFromPage(request);
  const result = await readRecord(env, id, scopes);
  if (!result?.ok) return uniform404();
  return renderAssessmentReviewPage(result.record, attributionLabel(auth.editor));
}

export async function assessmentOverrideEndpoint(request, env, auth) {
  const scopes = assessmentReviewerScopes(auth);
  if (!scopes) return uniform404();
  if (!csrfOk(request, env)) return editError("csrf_failed", "Bad request.", 403);
  let body;
  try {
    body = await request.json();
  } catch {
    return editError("validation_error", "Malformed JSON body.", 400);
  }
  const idOk = typeof body?.id === "string" && /^[A-Za-z0-9][A-Za-z0-9:_-]{0,127}$/.test(body.id);
  const assessmentIdOk = typeof body?.assessment_id === "string" &&
    /^[A-Za-z0-9][A-Za-z0-9:_-]{0,127}$/.test(body.assessment_id);
  const headingOk = HEADING_IDS.has(body?.heading_id);
  const scoreOk = Number.isInteger(body?.score) && body.score >= 1 && body.score <= 7;
  const note = typeof body?.note === "string" ? body.note.trim() : "";
  if (!idOk || !assessmentIdOk || !headingOk || !scoreOk || !note || note.length > 4000) {
    return editError("validation_error", "A valid heading, 1–7 score, and reason are required.", 400);
  }
  const result = await editorStub(env).recordAssessmentOverride({
    id: body.id,
    assessment_id: body.assessment_id,
    author: auth.editor,
    scopes,
    override: { heading_id: body.heading_id, score: body.score, note },
  });
  if (!result?.ok) {
    if (result?.reason === "not_found") return uniform404();
    return editError(result?.reason || "override_failed", "The override could not be recorded.", 409);
  }
  return json({ ok: true, override: result });
}

export { STORE_REVIEW_SCOPES };
