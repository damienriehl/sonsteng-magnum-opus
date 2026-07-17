// cors.js — allowlist matcher + preflight + a withCors() helper that MUST wrap
// every response (success AND error). The classic demo-night failure is a
// cap/limit/5xx returned without CORS headers: the browser then masks the real
// error as an opaque "CORS error" and the UI shows a blank box instead of the
// in-character "bad phone connection". One helper on all returns prevents that.

// Parse "https://a.com,https://b.com" (env.ALLOWED_ORIGINS) into a Set.
export function parseAllowedOrigins(str) {
  return new Set(
    (str || "")
      .split(",")
      .map((s) => s.trim())
      .filter(Boolean)
  );
}

// Returns the request Origin iff it is in the allowlist, else null.
export function matchOrigin(request, allowedSet) {
  const origin = request.headers.get("Origin");
  if (origin && allowedSet.has(origin)) return origin;
  return null;
}

// CORS headers for a matched origin. We ECHO the matched origin (never "*") and
// set Vary: Origin so caches don't cross responses between origins.
export function corsHeaders(origin) {
  return {
    "Access-Control-Allow-Origin": origin,
    "Vary": "Origin",
    "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
    "Access-Control-Allow-Headers": "content-type, x-session-token",
    "Access-Control-Max-Age": "86400",
  };
}

// Attach CORS headers to an existing Response (if origin matched). Returns a new
// Response preserving status/body. If origin is null (not allowlisted) the
// response goes back WITHOUT ACAO, so the browser blocks it — the allowlist
// working as intended.
export function withCors(response, origin) {
  if (!origin) return response;
  const headers = new Headers(response.headers);
  for (const [k, v] of Object.entries(corsHeaders(origin))) headers.set(k, v);
  return new Response(response.body, {
    status: response.status,
    statusText: response.statusText,
    headers,
  });
}

// Handle an OPTIONS preflight. 204 with the matched origin's CORS headers, or a
// bare 403 (no ACAO) when the origin is not allowlisted.
export function handlePreflight(request, allowedSet) {
  const origin = matchOrigin(request, allowedSet);
  if (!origin) return new Response(null, { status: 403 });
  return new Response(null, { status: 204, headers: corsHeaders(origin) });
}
