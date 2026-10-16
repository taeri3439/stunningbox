// Behind a reverse proxy (Railway, etc.), `new URL(request.url).origin` resolves to the
// INTERNAL host the container listens on (e.g. http://localhost:8080), so redirects built
// from it send the browser to a dead address. Reconstruct the public origin from the proxy's
// forwarded headers instead — but guard against a spoofed X-Forwarded-Host so a post-login
// redirect can't be turned into an open redirect:
//   - take the LAST (proxy-appended) value, never the client's spoofable first hop;
//   - require a well-formed host[:port];
//   - if APP_PUBLIC_HOSTS is set (comma-separated), the host must be in that allowlist.
// Falls back to the request's own origin when there's no proxy (local dev) or on any reject,
// so a rejected/spoofed value yields a dead-but-harmless redirect, never an attacker origin.
const HOST_RE = /^[a-zA-Z0-9.-]+(:\d+)?$/;

function allowedHosts(): Set<string> {
  return new Set(
    (process.env.APP_PUBLIC_HOSTS ?? "")
      .split(",")
      .map((h) => h.trim().toLowerCase())
      .filter(Boolean),
  );
}

function lastHop(value: string): string | undefined {
  return value
    .split(",")
    .map((v) => v.trim())
    .filter(Boolean)
    .pop();
}

export function publicOrigin(request: Request): string {
  const url = new URL(request.url);
  const forwarded = request.headers.get("x-forwarded-host");
  if (!forwarded) return url.origin; // no proxy in front → local dev, request origin is correct

  const host = lastHop(forwarded);
  if (!host || !HOST_RE.test(host)) return url.origin;

  const allow = allowedHosts();
  if (allow.size > 0 && !allow.has(host.toLowerCase())) return url.origin;

  const proto = lastHop(request.headers.get("x-forwarded-proto") ?? "https");
  const safeProto = proto === "http" || proto === "https" ? proto : "https";
  return `${safeProto}://${host}`;
}
