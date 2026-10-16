// Returns a safe SAME-ORIGIN relative path for post-auth navigation, or the fallback
// if the input is missing or unsafe. Rejects absolute URLs and protocol-relative values
// ("//evil.com", "/\\evil.com") to prevent open-redirect attacks via the ?redirect= param.
export function safeRedirect(raw: string | null | undefined, fallback = "/app"): string {
  if (!raw || !raw.startsWith("/")) return fallback; // must be a relative path
  if (raw.startsWith("//") || raw.startsWith("/\\")) return fallback; // not protocol-relative
  return raw;
}
