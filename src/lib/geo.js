// geo.js — visitor location detection from Cloudflare request headers.

export const REGIONS = {
  EAST_AFRICA: ["KE", "TZ", "UG", "RW", "BI", "ET", "SS", "SO"],
  TIER1: ["US", "CA", "GB", "AU", "IE", "NZ", "DE", "FR", "NL", "SE", "NO", "DK", "CH"],
  ASIA: ["IN", "PK", "BD", "PH", "ID", "MY", "TH", "VN", "SG"],
};

// Cloudflare exposes the country two ways; prefer request.cf, fall back to header.
export function detectCountry(request) {
  const fromCf = request.cf && request.cf.country;
  const fromHeader = request.headers.get("cf-ipcountry");
  return (fromCf || fromHeader || "XX").toUpperCase();
}

export function detectRegion(country) {
  for (const [region, list] of Object.entries(REGIONS)) {
    if (list.includes(country)) return region;
  }
  return "OTHER";
}
