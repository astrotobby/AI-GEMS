// session.js — cookie parsing + visitor identity helpers (no external deps).

export function parseCookies(request) {
  const header = request.headers.get("Cookie") || "";
  const out = {};
  header.split(";").forEach((part) => {
    const idx = part.indexOf("=");
    if (idx > -1) {
      const k = part.slice(0, idx).trim();
      const v = part.slice(idx + 1).trim();
      if (k) out[k] = decodeURIComponent(v);
    }
  });
  return out;
}

// Returns { visitorId, isNew }. Stable per browser via the mz_vid cookie.
export function getVisitor(request) {
  const cookies = parseCookies(request);
  if (cookies.mz_vid) return { visitorId: cookies.mz_vid, isNew: false };
  return { visitorId: crypto.randomUUID(), isNew: true };
}

// Build a Set-Cookie value. maxAge in seconds.
export function buildCookie(name, value, maxAge, extra = {}) {
  const parts = [
    `${name}=${encodeURIComponent(value)}`,
    `Path=/`,
    `Max-Age=${maxAge}`,
    `SameSite=Lax`,
    `Secure`,
  ];
  if (extra.httpOnly) parts.push("HttpOnly");
  return parts.join("; ");
}
