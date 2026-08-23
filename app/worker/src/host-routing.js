// Exact-host canonicalization that runs before the Worker's feature routers.

import { parseAllowedOrigins } from "./cors.js";

function permanentHostRedirect(url, targetHost, cacheControl) {
  const target = new URL(url.toString());
  target.protocol = "https:";
  target.host = targetHost;
  return new Response(null, {
    status: 308,
    headers: { Location: target.toString(), "Cache-Control": cacheControl },
  });
}

// Keep bookmarks to the retired editor hostname useful after its Access
// application is removed. Both hosts are deployment config so PROD, which has
// neither value, remains unaffected.
export function legacyEditorHostRedirect(env, url) {
  const legacyHost = typeof env?.EDIT_LEGACY_HOST === "string" ? env.EDIT_LEGACY_HOST.trim() : "";
  const accessHost = typeof env?.EDIT_ACCESS_HOST === "string" ? env.EDIT_ACCESS_HOST.trim() : "";
  if (!legacyHost || !accessHost || legacyHost === accessHost || url.host !== legacyHost) return null;
  return permanentHostRedirect(url, accessHost, "private, no-store");
}

// The redirect list is exact: suffix matching would let lookalike hostnames
// become redirect sources. The dominant canonical-host path allocates no Set.
export function publicHostRedirect(env, url) {
  const canonicalHost = typeof env?.PUBLIC_CANONICAL_HOST === "string"
    ? env.PUBLIC_CANONICAL_HOST.trim()
    : "";
  if (!canonicalHost || url.host === canonicalHost) return null;
  const redirectHosts = parseAllowedOrigins(
    typeof env?.PUBLIC_REDIRECT_HOSTS === "string" ? env.PUBLIC_REDIRECT_HOSTS : "",
  );
  if (!redirectHosts.has(url.host)) return null;
  return permanentHostRedirect(url, canonicalHost, "public, max-age=3600");
}
