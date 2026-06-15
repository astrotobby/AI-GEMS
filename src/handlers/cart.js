// TASK 6 — Abandoned cart recovery.
//
// HOW THE DELAY WORKS: Cloudflare cannot schedule a one-off job per visitor.
// The correct pattern is: record the product view in KV with a timestamp, then
// run a Cron-triggered scan (every 15 min) that finds views older than the
// delay that have not converted, fires the make.com webhook, and marks them
// recovered. This is implemented below in scanAbandonedCarts().
import { CONFIG, webhookConfigured } from "../../config.js";
import { bump } from "../lib/kv.js";

// Called when a visitor clicks/views a product but we have no purchase signal.
export async function markCartView(env, visitorId, productId, email) {
  const key = `cart:${visitorId}`;
  try {
    const existing = await env.MZ_CARTS.get(key);
    // Don't reset the timer if a pending cart already exists for this visitor.
    if (existing) {
      const rec = JSON.parse(existing);
      if (rec.status === "pending") {
        rec.productId = productId; // update to most recent product
        if (email) rec.email = email;
        await env.MZ_CARTS.put(key, JSON.stringify(rec), { expirationTtl: CONFIG.CART_KV_TTL_SECONDS });
        return;
      }
    }
    const record = {
      visitorId,
      productId,
      email: email || null,
      ts: Date.now(),
      status: "pending", // pending -> recovered | purchased
    };
    await env.MZ_CARTS.put(key, JSON.stringify(record), { expirationTtl: CONFIG.CART_KV_TTL_SECONDS });
    await bump(env, "cart_view");
  } catch (err) {
    console.log(`markCartView failed: ${err}`);
  }
}

// Attach a captured email to any pending cart for this visitor (called on lead).
export async function attachEmailToCart(env, visitorId, email) {
  const key = `cart:${visitorId}`;
  try {
    const raw = await env.MZ_CARTS.get(key);
    if (!raw) return;
    const rec = JSON.parse(raw);
    rec.email = email;
    await env.MZ_CARTS.put(key, JSON.stringify(rec), { expirationTtl: CONFIG.CART_KV_TTL_SECONDS });
  } catch (err) {
    console.log(`attachEmailToCart failed: ${err}`);
  }
}

// Mark a purchase so the scan skips it (call from a Shopify order webhook -> /mz/purchased).
export async function markPurchased(env, visitorId) {
  const key = `cart:${visitorId}`;
  try {
    const raw = await env.MZ_CARTS.get(key);
    if (!raw) return;
    const rec = JSON.parse(raw);
    rec.status = "purchased";
    await env.MZ_CARTS.put(key, JSON.stringify(rec), { expirationTtl: CONFIG.CART_KV_TTL_SECONDS });
  } catch (err) {
    console.log(`markPurchased failed: ${err}`);
  }
}

// Cron entrypoint. Scans pending carts older than the delay and fires webhooks.
export async function scanAbandonedCarts(env) {
  // No recovery webhook configured -> nothing to do. Cart views are still
  // tracked in KV; recovery simply activates once you set MAKE_CART_WEBHOOK.
  if (!webhookConfigured(CONFIG.MAKE_CART_WEBHOOK)) {
    console.log("scanAbandonedCarts skipped: MAKE_CART_WEBHOOK not configured");
    return 0;
  }
  const cutoff = Date.now() - CONFIG.CART_RECOVERY_DELAY_MIN * 60 * 1000;
  let cursor;
  let fired = 0;
  do {
    const list = await env.MZ_CARTS.list({ prefix: "cart:", cursor });
    for (const k of list.keys) {
      try {
        const raw = await env.MZ_CARTS.get(k.name);
        if (!raw) continue;
        const rec = JSON.parse(raw);
        if (rec.status !== "pending") continue;
        if (rec.ts > cutoff) continue; // not aged enough yet

        const payload = {
          event: "abandoned_cart",
          visitorId: rec.visitorId,
          productId: rec.productId,
          email: rec.email, // may be null — make.com can branch on this
          abandonedAt: new Date(rec.ts).toISOString(),
          productUrl: `https://${CONFIG.SHOPIFY_STORE}/products/`,
        };

        const resp = await fetch(CONFIG.MAKE_CART_WEBHOOK, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        });
        if (resp.ok) {
          rec.status = "recovered";
          rec.recoveredAt = Date.now();
          await env.MZ_CARTS.put(k.name, JSON.stringify(rec), { expirationTtl: CONFIG.CART_KV_TTL_SECONDS });
          await bump(env, "cart_recovered");
          fired++;
        } else {
          console.log(`cart webhook returned ${resp.status} for ${k.name}`);
        }
      } catch (err) {
        console.log(`scan error for ${k.name}: ${err}`);
      }
    }
    cursor = list.list_complete ? null : list.cursor;
  } while (cursor);
  console.log(`scanAbandonedCarts fired ${fired} webhooks`);
  return fired;
}
