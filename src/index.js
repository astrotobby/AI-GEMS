// =============================================================================
// Blog Monetization Worker — single Worker, six concerns.
//  - fetch()     : routes API endpoints + proxies/injects into Pages HTML
//  - scheduled() : Cron-triggered abandoned-cart scan (Task 6)
// =============================================================================
import { CONFIG } from "../config.js";
import { detectCountry, detectRegion } from "./lib/geo.js";
import { getVisitor, buildCookie } from "./lib/session.js";
import { bump, bumpDim } from "./lib/kv.js";

import { pickProduct, renderProductCard, shopifyUrl } from "./handlers/products.js";
import { renderPopup, classifyCategory, handleLead } from "./handlers/popup.js";
import { pickNetwork, renderAdSlot } from "./handlers/ads.js";
import { handleVideoPage, handleVideoUpsert } from "./handlers/videoPage.js";
import { handleTrack, handleDashboard } from "./handlers/analytics.js";
import { scanAbandonedCarts, markCartView, markPurchased } from "./handlers/cart.js";
import { PRODUCTS } from "../config.js";

export default {
  async fetch(request, env, ctx) {
    try {
      return await route(request, env, ctx);
    } catch (err) {
      console.log(`unhandled error: ${err && err.stack ? err.stack : err}`);
      // On any failure, fall back to the raw origin so the site never breaks.
      return fetch(new Request(CONFIG.ORIGIN + new URL(request.url).pathname, request));
    }
  },

  // Cron trigger -> abandoned cart scan.
  async scheduled(event, env, ctx) {
    ctx.waitUntil(scanAbandonedCarts(env));
  },
};

async function route(request, env, ctx) {
  const url = new URL(request.url);
  const path = url.pathname;
  const { visitorId, isNew } = getVisitor(request);

  // ---- API endpoints -------------------------------------------------------
  if (path === "/mz/track") return withVid(await handleTrack(request, env, ctx, visitorId), visitorId, isNew);
  if (path === "/mz/lead") {
    const resp = await handleLead(request, env, visitorId); // also attaches email to cart
    if (resp.status === 200) ctx.waitUntil(bump(env, "lead"));
    return withVid(resp, visitorId, isNew);
  }
  if (path === "/mz/click") return handleClickRedirect(request, env, ctx, visitorId, isNew);
  if (path === "/mz/purchased") {
    ctx.waitUntil(markPurchased(env, url.searchParams.get("vid") || visitorId));
    return new Response("ok");
  }
  if (path === "/analytics/dashboard") return handleDashboard(request, env);
  if (path === "/mz/video-upsert") return handleVideoUpsert(request, env);

  // ---- Video landing pages (Task 3) ---------------------------------------
  if (path.startsWith("/video/")) {
    const videoId = path.slice("/video/".length).split("/")[0];
    if (videoId) return withVid(await handleVideoPage(request, env, ctx, videoId), visitorId, isNew);
  }

  // ---- Everything else: proxy origin + inject (Tasks 1, 2, 4) --------------
  return proxyAndInject(request, env, ctx, visitorId, isNew);
}

// Redirect /mz/click -> real Shopify URL, logging the click + starting a cart timer.
async function handleClickRedirect(request, env, ctx, visitorId, isNew) {
  const url = new URL(request.url);
  const pid = url.searchParams.get("pid");
  const ctxLabel = url.searchParams.get("ctx") || "page";
  const product = PRODUCTS.find((p) => p.id === pid);
  const dest = product ? shopifyUrl(product) : `https://${CONFIG.SHOPIFY_STORE}`;

  ctx.waitUntil(
    Promise.all([
      bump(env, "product_click"),
      pid ? bumpDim(env, "product", pid) : Promise.resolve(),
      pid ? bumpDim(env, "product_ctx", `${pid}:${ctxLabel}`) : Promise.resolve(),
      pid ? markCartView(env, visitorId, pid) : Promise.resolve(),
    ])
  );

  const resp = Response.redirect(dest, 302);
  return withVid(resp, visitorId, isNew);
}

// Fetch the page from origin and inject components via HTMLRewriter.
async function proxyAndInject(request, env, ctx, visitorId, isNew) {
  const url = new URL(request.url);
  const originReq = new Request(CONFIG.ORIGIN + url.pathname + url.search, request);
  const originResp = await fetch(originReq);

  const ctype = originResp.headers.get("Content-Type") || "";
  if (!ctype.includes("text/html")) return originResp; // assets pass straight through

  const country = detectCountry(request);
  const region = detectRegion(country);
  const category = classifyCategory(url.pathname);

  // Per-visitor ad view counter (drives rotation).
  let viewCount = 0;
  try {
    viewCount = parseInt((await env.MZ_SESSIONS.get(`views:${visitorId}`)) || "0", 10);
    ctx.waitUntil(env.MZ_SESSIONS.put(`views:${visitorId}`, String(viewCount + 1), { expirationTtl: CONFIG.SESSION_TTL_SECONDS }));
  } catch (err) {
    console.log(`view counter failed: ${err}`);
  }

  const product = await pickProduct(env, region, visitorId);
  const network = pickNetwork(region, viewCount);

  ctx.waitUntil(Promise.all([bump(env, "pageview"), bumpDim(env, "country", country)]));

  const injection =
    `\n<!-- monetization -->\n` +
    `<div class="mz-injected">` +
    renderAdSlot(region, network) +
    renderProductCard(product, `page:${category}`) +
    `</div>` +
    renderPopup(category) +
    cartClickScript();

  const rewriter = new HTMLRewriter().on("body", {
    element(el) {
      el.append(injection, { html: true });
    },
  });

  const out = new Response(rewriter.transform(originResp).body, originResp);
  // Body length changed after injection — drop the stale length header.
  out.headers.delete("Content-Length");
  // Don't let the CDN cache a per-visitor injected page.
  out.headers.set("Cache-Control", "private, no-store");
  return withVid(out, visitorId, isNew);
}

// Client script: any click on a link to the Shopify store counts as a cart view.
function cartClickScript() {
  return `
<script>
(function(){
  document.addEventListener('click', function(e){
    var a=e.target.closest && e.target.closest('a');
    if(!a || !a.href) return;
    if(a.href.indexOf(${JSON.stringify(CONFIG.SHOPIFY_STORE)})>-1){
      try{ navigator.sendBeacon('/mz/track', JSON.stringify({type:'cart_view'})); }catch(_){}
    }
  }, true);
})();
</script>`;
}

// Attach the visitor-id cookie to any response when the visitor is new.
function withVid(resp, visitorId, isNew) {
  if (!isNew) return resp;
  const r = new Response(resp.body, resp);
  r.headers.append("Set-Cookie", buildCookie("mz_vid", visitorId, CONFIG.SESSION_TTL_SECONDS * 30));
  return r;
}
