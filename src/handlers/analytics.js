// TASK 5 — Tracking beacon + analytics dashboard endpoint.
import { CONFIG } from "../../config.js";
import { bump, bumpDim, logEvent, sumMetric, topDim } from "../lib/kv.js";
import { markCartView } from "./cart.js";

// POST /mz/track — generic beacon. Body: { type, ... }.
// Accepts: pageview, ad_impression, ad_fallback, ad_click, product_click,
//          cart_view, video_view (plus optional pid/network/videoId).
export async function handleTrack(request, env, ctx, visitorId) {
  let body = {};
  try {
    const text = await request.text(); // sendBeacon sends text/plain
    body = text ? JSON.parse(text) : {};
  } catch {
    body = {};
  }
  const type = String(body.type || "").slice(0, 40);
  if (!type) return new Response("ok"); // never error a beacon

  const jobs = [bump(env, type)];
  if (body.network) jobs.push(bumpDim(env, "network", `${body.network}:${type}`));
  if (body.pid) jobs.push(bumpDim(env, "product", body.pid));
  if (body.videoId) jobs.push(bumpDim(env, "video", body.videoId));
  jobs.push(logEvent(env, type, { visitorId, ...body }, CONFIG.EVENT_KV_TTL_SECONDS));

  // A product view starts an abandoned-cart timer.
  if (type === "cart_view" && body.pid) {
    jobs.push(markCartView(env, visitorId, body.pid));
  }

  ctx.waitUntil(Promise.all(jobs));
  return new Response("ok", { headers: { "Cache-Control": "no-store" } });
}

// GET /analytics/dashboard?token=...&days=7  -> JSON metrics (for make.com pull)
export async function handleDashboard(request, env) {
  const url = new URL(request.url);
  const token = url.searchParams.get("token");
  if (!token || token !== CONFIG.DASHBOARD_TOKEN) {
    return json({ ok: false, error: "unauthorized" }, 401);
  }
  const days = Math.min(parseInt(url.searchParams.get("days") || "7", 10) || 7, 90);

  const [pageviews, productClicks, leads, adClicks, adImpr, videoViews, cartViews, cartRecovered] =
    await Promise.all([
      sumMetric(env, "pageview", days),
      sumMetric(env, "product_click", days),
      sumMetric(env, "lead", days),
      sumMetric(env, "ad_click", days),
      sumMetric(env, "ad_impression", days),
      sumMetric(env, "video_view", days),
      sumMetric(env, "cart_view", days),
      sumMetric(env, "cart_recovered", days),
    ]);

  const [topProducts, topVideos, networkStats] = await Promise.all([
    topDim(env, "product", days, 10),
    topDim(env, "video", days, 10),
    topDim(env, "network", days, 20),
  ]);

  const pv = pageviews.total || 0;
  const rate = (n) => (pv ? +((n / pv) * 100).toFixed(2) : 0);

  return json({
    ok: true,
    window_days: days,
    generated_at: new Date().toISOString(),
    totals: {
      pageviews: pv,
      product_clicks: productClicks.total,
      email_signups: leads.total,
      ad_impressions: adImpr.total,
      ad_clicks: adClicks.total,
      video_views: videoViews.total,
      cart_views: cartViews.total,
      carts_recovered: cartRecovered.total,
    },
    conversion: {
      product_ctr_pct: rate(productClicks.total),
      signup_rate_pct: rate(leads.total),
      ad_ctr_pct: adImpr.total ? +((adClicks.total / adImpr.total) * 100).toFixed(2) : 0,
      cart_recovery_rate_pct: cartViews.total
        ? +((cartRecovered.total / cartViews.total) * 100).toFixed(2)
        : 0,
    },
    top_products: topProducts,
    top_videos: topVideos,
    ad_networks: networkStats,
    daily: { pageviews: pageviews.byDay, product_clicks: productClicks.byDay, signups: leads.byDay },
  });
}

function json(obj, status = 200) {
  return new Response(JSON.stringify(obj, null, 2), {
    status,
    headers: { "Content-Type": "application/json", "Cache-Control": "no-store" },
  });
}
