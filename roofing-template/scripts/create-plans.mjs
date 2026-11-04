#!/usr/bin/env node
/**
 * One-time setup: creates the product and the three plans (variants) on your
 * Whop business, then writes their IDs into src/site.config.ts.
 *
 *   WHOP_API_KEY=... WHOP_ACCOUNT_ID=biz_... npm run setup:plans
 *
 * Prices and names are read from src/site.config.ts, so edit that first.
 * Re-running is safe: existing IDs in the config are left alone.
 */
import { readFileSync, writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const configPath = join(root, "src", "site.config.ts");
const API = process.env.WHOP_API_ORIGIN ?? "https://api.whop.com";
const key = process.env.WHOP_API_KEY;
const accountId = process.env.WHOP_ACCOUNT_ID;

if (!key || !accountId?.startsWith("biz_")) {
  console.error("Set WHOP_API_KEY and WHOP_ACCOUNT_ID (biz_...) first.");
  process.exit(1);
}

async function whop(method, path, body) {
  const res = await fetch(`${API}/api/v1${path}`, {
    method,
    headers: {
      authorization: `Bearer ${key}`,
      "content-type": "application/json",
      "Api-Version-Date": "2026-10-30",
    },
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(`${method} ${path} → ${res.status}: ${JSON.stringify(data)}`);
  return data;
}

let config = readFileSync(configPath, "utf8");

// Pull name/price/description for each plan out of the config file.
function planSpec(planKey) {
  const block = config.split(`key: "${planKey}"`)[1]?.split("cta:")[0] ?? "";
  const pick = (field) => block.match(new RegExp(`${field}: "([^"]*)"`))?.[1];
  const price = Number(block.match(/price: (\d+(?:\.\d+)?)/)?.[1]);
  return { name: pick("name"), description: pick("description"), price };
}
const companyName = config.match(/name: "([^"]*)"/)?.[1] ?? "Roofing";

const existingProduct = config.match(/productId: "(prod_(?!REPLACE)[A-Za-z0-9]+)"/)?.[1];
const productId =
  existingProduct ??
  (
    await whop("POST", "/products", {
      account_id: accountId,
      title: `${companyName} Services`.slice(0, 80),
      description: "Roof inspections, project deposits and flat-rate repairs.",
      visibility: "hidden",
    })
  ).id;
console.log("product", productId);

const placeholders = { inspection: "plan_REPLACE_INSPECTION", deposit: "plan_REPLACE_DEPOSIT", flatRate: "plan_REPLACE_FLATRATE" };
for (const [planKey, placeholder] of Object.entries(placeholders)) {
  if (!config.includes(placeholder)) {
    console.log(planKey, "already configured — skipped");
    continue;
  }
  const spec = planSpec(planKey);
  const variant = await whop("POST", "/variants", {
    account_id: accountId,
    product_id: productId,
    title: spec.name.slice(0, 30),
    description: spec.description,
    billing_type: "one_time",
    currency: "usd",
    initial_price: spec.price,
    visibility: "hidden",
  });
  console.log(planKey, variant.id, `$${spec.price}`);
  config = config.replace(placeholder, variant.id);
}

config = config.replaceAll('productId: "prod_REPLACE"', `productId: "${productId}"`);
config = config.replace('whopAccountId: "biz_REPLACE"', `whopAccountId: "${accountId}"`);
writeFileSync(configPath, config);
console.log("\nUpdated src/site.config.ts — now run: whop apps deploy");
