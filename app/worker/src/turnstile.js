// turnstile.js — Cloudflare Turnstile bot-gate for the free session-mint endpoint.
//
// WP6: GET /v1/session is the one unauthenticated, spend-adjacent endpoint (it
// mints the signed token every /chat|/debrief|/critique call requires). A bot
// could farm tokens to drain the hosted demo pool or the per-IP mint ceiling.
// Turnstile (managed mode) sits in front: the browser solves an invisible/
// challenge widget, the Worker verifies the resulting token with siteverify
// BEFORE minting. No token / bad token -> no session.
//
// Two hard carve-outs keep the keyless demo flows working (WP9 rehearsal,
// professor bypass): a valid DEMO_BYPASS_TOKEN skips the gate entirely, and the
// TURNSTILE_ENABLED="false" flag disables it deployment-wide. Neither path calls
// siteverify. Failure modes fail *retryable*, never silently open and never a
// permanent brick — see gateSessionMint().

const SITEVERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify";

// The gate is ON unless explicitly disabled with the string "false". Any other
// value (including unset) is treated as enabled — fail-closed by default so a
// missing var can never silently drop the bot-gate.
export function turnstileEnabled(env) {
  return env.TURNSTILE_ENABLED !== "false";
}

// Call Turnstile siteverify with the client-supplied token. `fetchImpl` is
// injectable so unit tests can mock the network without a live challenge.
// Returns { success, reason } — never throws (a network/parse failure resolves
// to success:false so the caller rejects rather than crashing the mint).
export async function siteverify(secret, token, ip, fetchImpl = fetch) {
  if (!secret || typeof token !== "string" || token.length === 0) {
    return { success: false, reason: "missing_token" };
  }
  const form = new URLSearchParams();
  form.set("secret", secret);
  form.set("response", token);
  // remoteip is optional but tightens the check; skip the sentinel "unknown".
  if (ip && ip !== "unknown") form.set("remoteip", ip);

  let res;
  try {
    res = await fetchImpl(SITEVERIFY_URL, {
      method: "POST",
      headers: { "content-type": "application/x-www-form-urlencoded" },
      body: form.toString(),
    });
  } catch {
    return { success: false, reason: "network_error" };
  }
  let data = null;
  try {
    data = await res.json();
  } catch {
    return { success: false, reason: "parse_error" };
  }
  const codes = data && Array.isArray(data["error-codes"]) ? data["error-codes"].join(",") : "";
  return { success: !!(data && data.success === true), reason: codes };
}

// Decide whether a session mint may proceed. Pure enough to unit-test end to end
// (inject fetchImpl, assert whether siteverify was called). Returns:
//   { ok: true, skipped?: "bypass"|"disabled" }            -> proceed to mint
//   { ok: false, code, status, message, reason }           -> reject (envelope-shaped)
// Carve-outs (NO siteverify call): (a) a valid demo bypass, (b) the gate off.
export async function gateSessionMint({ env, token, isDemo, ip, fetchImpl = fetch }) {
  // (a) Demo bypass: keyless demo/professor flows must never see a challenge.
  if (isDemo) return { ok: true, skipped: "bypass" };
  // (b) Gate disabled deployment-wide.
  if (!turnstileEnabled(env)) return { ok: true, skipped: "disabled" };
  // Enabled but no secret configured = a deploy error. Fail retryable (503), not
  // open: we cannot verify, so we must not mint, but the client can reload once
  // the secret is set. (prod-enable.md documents setting TURNSTILE_SECRET.)
  if (!env.TURNSTILE_SECRET) {
    return {
      ok: false,
      code: "turnstile_failed",
      status: 503,
      message: "Verification is temporarily unavailable. Please reload the page and try again.",
      reason: "no_secret",
    };
  }
  const v = await siteverify(env.TURNSTILE_SECRET, token, ip, fetchImpl);
  if (v.success) return { ok: true };
  // Missing/failed token: 403. The frontend renders this as a retryable prompt
  // (reload re-runs the widget) — a widget that failed to load client-side lands
  // here too, so it degrades to "reload to retry", never a hard brick.
  return {
    ok: false,
    code: "turnstile_failed",
    status: 403,
    message: "Verification could not be completed. Please reload the page and try again.",
    reason: v.reason || "verify_failed",
  };
}
