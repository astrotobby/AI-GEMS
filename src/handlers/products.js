// TASK 1 — Dynamic Shopify product rotation (geo-aware, session-sticky).
import { CONFIG, PRODUCTS } from "../../config.js";

function eligible(region) {
  const list = PRODUCTS.filter((p) => p.regions.includes("*") || p.regions.includes(region));
  return list.length ? list : PRODUCTS; // never return empty
}

// Deterministic-ish random pick of one product, sticky per visitor for 24h.
export async function pickProduct(env, region, visitorId) {
  const key = `sess:prod:${visitorId}`;
  try {
    const cached = await env.MZ_SESSIONS.get(key);
    if (cached) {
      const found = PRODUCTS.find((p) => p.id === cached);
      if (found) return found;
    }
  } catch (err) {
    console.log(`pickProduct cache read failed: ${err}`);
  }

  const pool = eligible(region);
  // randomly select from 3-5 candidates to avoid banner fatigue
  const candidates = pool.slice().sort(() => Math.random() - 0.5).slice(0, Math.min(5, pool.length));
  const chosen = candidates[Math.floor(Math.random() * candidates.length)];

  try {
    await env.MZ_SESSIONS.put(key, chosen.id, { expirationTtl: CONFIG.SESSION_TTL_SECONDS });
  } catch (err) {
    console.log(`pickProduct cache write failed: ${err}`);
  }
  return chosen;
}

// Renders a self-contained, mobile-first product card.
// The Buy button routes through /mz/click so we can attribute the click.
export function renderProductCard(product, ctx = "page") {
  const buyUrl = `/mz/click?type=product&pid=${encodeURIComponent(product.id)}&ctx=${encodeURIComponent(ctx)}`;
  return `
<div class="mz-product-card" data-pid="${product.id}">
  <a href="${buyUrl}" class="mz-product-link">
    <img src="${product.image}" alt="${escapeHtml(product.title)}" loading="lazy" class="mz-product-img"/>
    <div class="mz-product-body">
      <span class="mz-product-title">${escapeHtml(product.title)}</span>
      <span class="mz-product-price">${escapeHtml(product.price)}</span>
      <span class="mz-buy-btn">Buy Now</span>
    </div>
  </a>
</div>
<style>
.mz-product-card{max-width:340px;margin:16px auto;border:1px solid #e7e7e7;border-radius:14px;overflow:hidden;font-family:system-ui,sans-serif;box-shadow:0 4px 18px rgba(0,0,0,.07);background:#fff}
.mz-product-link{text-decoration:none;color:inherit;display:block}
.mz-product-img{width:100%;height:auto;display:block}
.mz-product-body{padding:14px 16px;display:flex;flex-direction:column;gap:6px}
.mz-product-title{font-weight:600;font-size:16px}
.mz-product-price{color:#0a7d2c;font-weight:700}
.mz-buy-btn{margin-top:8px;background:#111;color:#fff;text-align:center;padding:11px;border-radius:10px;font-weight:600}
@media(max-width:480px){.mz-product-card{margin:12px 8px}}
</style>`;
}

export function escapeHtml(s = "") {
  return String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

// Build the real Shopify product URL for redirects.
export function shopifyUrl(product) {
  return `https://${CONFIG.SHOPIFY_STORE}/products/${product.handle}`;
}
