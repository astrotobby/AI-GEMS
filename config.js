// =============================================================================
// config.js — EDIT THIS FILE ONCE. All site-specific values live here.
// Everything marked REPLACE_ must be filled in before deploying.
// =============================================================================

export const CONFIG = {
  // Your live blog domain — the hostname the Worker route is attached to.
  SITE_DOMAIN: "astrotobby.site",

  // Where the UNMODIFIED pages come from. The Worker fetches HTML from this
  // origin, then injects the monetization components. Use your Cloudflare
  // Pages *.pages.dev URL so the Worker never loops back into itself.
  ORIGIN: "https://astro-autoblog.pages.dev",

  // Shopify storefront host (used to build product links).
  SHOPIFY_STORE: "your-store.myshopify.com",

  // make.com "Custom webhook" module URLs.
  MAKE_LEAD_WEBHOOK: "https://hook.eu2.make.com/REPLACE_LEAD_WEBHOOK",
  MAKE_CART_WEBHOOK: "https://hook.eu2.make.com/REPLACE_CART_WEBHOOK",

  // Long random string. Required to read /analytics/dashboard.
  // Generate one with:  node -e "console.log(crypto.randomUUID()+crypto.randomUUID())"
  DASHBOARD_TOKEN: "REPLACE_WITH_LONG_RANDOM_STRING",

  // Shared secret make.com sends to POST /mz/video-upsert (auto-creates video pages).
  // Use a DIFFERENT long random string than DASHBOARD_TOKEN.
  INGEST_TOKEN: "REPLACE_WITH_DIFFERENT_LONG_RANDOM_STRING",

  // Behaviour tuning ----------------------------------------------------------
  SESSION_TTL_SECONDS: 60 * 60 * 24, // a visitor sees the same product for 24h
  POPUP_TIMER_MS: 30000,             // popup also fires after 30s
  AD_ROTATE_EVERY: 3,                // rotate ad network every N page views
  CART_RECOVERY_DELAY_MIN: 60,       // fire cart webhook 60 min after view
  CART_KV_TTL_SECONDS: 60 * 60 * 48, // keep cart records 48h
  EVENT_KV_TTL_SECONDS: 60 * 60 * 24 * 30, // keep raw events 30 days
};

// True only when a value has been filled in (not left as a REPLACE_ placeholder
// or empty). Used to make make.com forwarding optional, so the project runs
// fully standalone with no make.com dependency.
export function webhookConfigured(url) {
  return Boolean(url) && !url.includes("REPLACE");
}

// =============================================================================
// PRODUCT CATALOG — Task 1 / Task 3 / Task 6
// `regions` controls geo targeting. Valid regions:
//   EAST_AFRICA | TIER1 | ASIA | OTHER   (use ["*"] to show everywhere)
// `handle` is the Shopify product handle (the slug in the product URL).
// =============================================================================
export const PRODUCTS = [
  { id: "p1", handle: "crypto-starter-pack",  title: "Crypto Starter Pack",      image: "https://cdn.shopify.com/REPLACE1.jpg", price: "KSh 1,499", regions: ["EAST_AFRICA"], tags: ["crypto","gamefi"] },
  { id: "p2", handle: "trader-hoodie",         title: "Trader Hoodie",            image: "https://cdn.shopify.com/REPLACE2.jpg", price: "$39",      regions: ["TIER1"],       tags: ["merch"] },
  { id: "p3", handle: "gamefi-guide-ebook",    title: "GameFi Guide (eBook)",     image: "https://cdn.shopify.com/REPLACE3.jpg", price: "$9",       regions: ["*"],           tags: ["crypto","gamefi","ebook"] },
  { id: "p4", handle: "mobile-mining-kit",     title: "Mobile Mining Kit",        image: "https://cdn.shopify.com/REPLACE4.jpg", price: "KSh 2,999", regions: ["EAST_AFRICA","ASIA"], tags: ["crypto"] },
  { id: "p5", handle: "premium-signals-month", title: "Premium Signals (1 month)",image: "https://cdn.shopify.com/REPLACE5.jpg", price: "$19",      regions: ["TIER1","ASIA"], tags: ["signals"] },
];

// =============================================================================
// AD NETWORK TAGS — Task 4
// Paste the EXACT script/markup each network gives you. These are placeholders
// shaped like the real ones. The Worker injects the chosen network's `html`.
// =============================================================================
export const AD_NETWORKS = {
  adsterra: {
    name: "Adsterra",
    html: `<script type="text/javascript">atOptions={'key':'REPLACE_ADSTERRA_KEY','format':'iframe','height':250,'width':300,'params':{}};</script><script src="//www.highperformanceformat.com/REPLACE_ADSTERRA_KEY/invoke.js"></script>`,
  },
  monetag: {
    name: "Monetag",
    html: `<script src="//REPLACE_MONETAG_DOMAIN/REPLACE_MONETAG_ZONE.js" data-cfasync="false" async></script>`,
  },
  bidglow: {
    name: "Bidglow",
    html: `<ins class="bidglow-unit" data-zone="REPLACE_BIDGLOW_ZONE"></ins><script async src="//cdn.bidglow.net/loader.js"></script>`,
  },
};

// Which networks are eligible per region, in priority order.
// First entry is the FALLBACK if a rotated network fails to render.
export const AD_REGION_MAP = {
  EAST_AFRICA: ["adsterra"],
  TIER1:       ["adsterra", "monetag", "bidglow"],
  ASIA:        ["adsterra", "monetag"],
  OTHER:       ["adsterra"],
};
