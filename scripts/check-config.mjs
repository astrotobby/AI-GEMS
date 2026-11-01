// Lists every value still left as a placeholder, so a deploy never ships a half-configured Worker.
// Runs automatically before `npm run deploy`. Exit code 1 = something required is missing.
//   node scripts/check-config.mjs            (warnings + errors)
//   node scripts/check-config.mjs --strict   (warnings also fail)
import { readFileSync } from "node:fs";
import { CONFIG, PRODUCTS, AD_NETWORKS, AD_REGION_MAP, isPlaceholder } from "../config.js";

const errors = [];
const warnings = [];

// Required: the Worker can't run safely without these.
if (isPlaceholder(CONFIG.ORIGIN) || CONFIG.ORIGIN.includes(CONFIG.SITE_DOMAIN))
  errors.push(`ORIGIN must be your *.pages.dev URL (got ${CONFIG.ORIGIN}) - the apex domain would loop`);
for (const key of ["DASHBOARD_TOKEN", "INGEST_TOKEN"]) {
  if (isPlaceholder(CONFIG[key]) || CONFIG[key].length < 24)
    errors.push(`${key} is a placeholder or too short - anyone could read/write your data`);
}
if (CONFIG.DASHBOARD_TOKEN === CONFIG.INGEST_TOKEN) errors.push("DASHBOARD_TOKEN and INGEST_TOKEN must differ");

const wrangler = readFileSync(new URL("../wrangler.toml", import.meta.url), "utf8");
const kv = [...wrangler.matchAll(/binding = "(\w+)"\s*\nid = "([^"]+)"/g)];
for (const [, binding, id] of kv) if (isPlaceholder(id)) errors.push(`wrangler.toml: KV ${binding} id not set (run setup-kv)`);

// Revenue: missing these means that part simply stays off (the Worker skips placeholders).
if (isPlaceholder(CONFIG.SHOPIFY_STORE)) warnings.push("SHOPIFY_STORE not set - no product cards will show");
const badProducts = PRODUCTS.filter((p) => isPlaceholder(p.image) || isPlaceholder(p.handle));
if (badProducts.length)
  warnings.push(`${badProducts.length}/${PRODUCTS.length} products have placeholder images/handles (hidden): ${badProducts.map((p) => p.id).join(", ")}`);
const liveNets = Object.entries(AD_NETWORKS).filter(([, n]) => !isPlaceholder(n.html)).map(([k]) => k);
if (!liveNets.length) warnings.push("no ad network tag pasted in AD_NETWORKS - no ads will show");
for (const [region, nets] of Object.entries(AD_REGION_MAP)) {
  if (!nets.some((n) => liveNets.includes(n))) warnings.push(`AD_REGION_MAP.${region}: none of [${nets}] is configured - no ads there`);
}
if (isPlaceholder(CONFIG.MAKE_LEAD_WEBHOOK)) warnings.push("MAKE_LEAD_WEBHOOK not set - leads stay in KV only (fine)");
if (isPlaceholder(CONFIG.MAKE_CART_WEBHOOK)) warnings.push("MAKE_CART_WEBHOOK not set - abandoned carts are tracked but no email goes out");

for (const w of warnings) console.log(`⚠️  ${w}`);
for (const e of errors) console.log(`❌ ${e}`);
const strict = process.argv.includes("--strict");
if (errors.length || (strict && warnings.length)) {
  console.log(`\n${errors.length} error(s), ${warnings.length} warning(s) - fix config.js / wrangler.toml before deploying.`);
  process.exit(1);
}
console.log(`✅ config OK (${warnings.length} warning(s))`);
