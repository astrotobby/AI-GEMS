// TASK 3 — Video landing page generator at /video/{id}.
//
// Video metadata is stored in KV namespace MZ_VIDEOS under key `video:{id}` as:
//   { youtubeId, title, summary, productIds:["p1","p3"], tiktok, instagram, facebook }
// Your make.com content pipeline writes these records (see README "Seeding videos").
import { CONFIG, PRODUCTS } from "../../config.js";
import { renderProductCard, escapeHtml } from "./products.js";
import { bump, bumpDim } from "../lib/kv.js";

export async function handleVideoPage(request, env, ctx, videoId) {
  let meta;
  try {
    const raw = await env.MZ_VIDEOS.get(`video:${videoId}`);
    if (!raw) return new Response("Video not found", { status: 404 });
    meta = JSON.parse(raw);
  } catch (err) {
    console.log(`video meta read failed: ${err}`);
    return new Response("Error loading video", { status: 500 });
  }

  // Track the view without blocking the response.
  ctx.waitUntil(Promise.all([bump(env, "video_view"), bumpDim(env, "video", videoId)]));

  const products = PRODUCTS.filter((p) => (meta.productIds || []).includes(p.id)).slice(0, 3);
  const pageUrl = `https://${CONFIG.SITE_DOMAIN}/video/${videoId}`;
  const title = escapeHtml(meta.title || "Video");
  const desc = escapeHtml((meta.summary || "").slice(0, 155));

  const cards = products
    .map((p) => renderProductCard(p, `video:${videoId}`))
    .join("\n");

  const schema = {
    "@context": "https://schema.org",
    "@type": "VideoObject",
    name: meta.title || "Video",
    description: meta.summary || "",
    thumbnailUrl: `https://i.ytimg.com/vi/${meta.youtubeId}/hqdefault.jpg`,
    uploadDate: meta.uploadDate || new Date().toISOString(),
    embedUrl: `https://www.youtube.com/embed/${meta.youtubeId}`,
    contentUrl: `https://www.youtube.com/watch?v=${meta.youtubeId}`,
  };

  const share = shareButtons(meta, pageUrl);

  const html = `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>${title}</title>
<meta name="description" content="${desc}"/>
<link rel="canonical" href="${pageUrl}"/>
<meta property="og:type" content="video.other"/>
<meta property="og:title" content="${title}"/>
<meta property="og:description" content="${desc}"/>
<meta property="og:url" content="${pageUrl}"/>
<meta property="og:image" content="https://i.ytimg.com/vi/${escapeHtml(meta.youtubeId)}/hqdefault.jpg"/>
<meta name="twitter:card" content="player"/>
<script type="application/ld+json">${JSON.stringify(schema)}</script>
<style>
body{font-family:system-ui,sans-serif;margin:0;color:#1a1a1a;background:#fafafa}
.mz-wrap{max-width:760px;margin:0 auto;padding:18px}
.mz-video{position:relative;padding-top:56.25%;border-radius:14px;overflow:hidden;background:#000}
.mz-video iframe{position:absolute;inset:0;width:100%;height:100%;border:0}
h1{font-size:24px;margin:18px 0 10px}
.mz-summary{line-height:1.6;color:#333}
.mz-share{display:flex;gap:10px;flex-wrap:wrap;margin:18px 0}
.mz-share a{flex:1;min-width:120px;text-align:center;padding:11px;border-radius:10px;color:#fff;text-decoration:none;font-weight:600;font-size:14px}
.mz-yt{background:#ff0000}.mz-tt{background:#000}.mz-ig{background:#c13584}.mz-fb{background:#1877f2}
.mz-products-title{margin:26px 0 6px;font-size:18px}
</style>
</head>
<body>
<div class="mz-wrap">
  <div class="mz-video"><iframe src="https://www.youtube.com/embed/${escapeHtml(meta.youtubeId)}" title="${title}" allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture" allowfullscreen></iframe></div>
  <h1>${title}</h1>
  <p class="mz-summary">${escapeHtml(meta.summary || "")}</p>
  <div class="mz-share">${share}</div>
  ${products.length ? `<h2 class="mz-products-title">Featured products</h2>${cards}` : ""}
</div>
</body>
</html>`;

  return new Response(html, {
    headers: { "Content-Type": "text/html; charset=utf-8", "Cache-Control": "public, max-age=300" },
  });
}

// POST /mz/video-upsert — called by make.com to create/update a video page.
// Auth: header  Authorization: Bearer <CONFIG.INGEST_TOKEN>
// Body: { videoId?, youtubeId, title, summary?, productIds?, tiktok?, instagram?, facebook?, uploadDate? }
//   productIds may be an array OR a comma-separated string (make.com friendly).
export async function handleVideoUpsert(request, env) {
  const token = (request.headers.get("Authorization") || "").replace(/^Bearer\s+/i, "");
  if (!CONFIG.INGEST_TOKEN || CONFIG.INGEST_TOKEN.startsWith("REPLACE_") || token !== CONFIG.INGEST_TOKEN) {
    return upsertJson({ ok: false, error: "unauthorized" }, 401);
  }

  let body;
  try {
    body = await request.json();
  } catch {
    return upsertJson({ ok: false, error: "invalid_json" }, 400);
  }

  const youtubeId = String(body.youtubeId || "").trim();
  const title = String(body.title || "").trim();
  if (!youtubeId || !title) return upsertJson({ ok: false, error: "missing youtubeId or title" }, 400);

  const videoId = slugify(body.videoId || body.slug || title);
  if (!videoId) return upsertJson({ ok: false, error: "could not derive slug" }, 400);

  let productIds = [];
  if (Array.isArray(body.productIds)) productIds = body.productIds;
  else if (typeof body.productIds === "string" && body.productIds.trim())
    productIds = body.productIds.split(",").map((s) => s.trim()).filter(Boolean);

  const record = {
    youtubeId,
    title,
    summary: String(body.summary || ""),
    productIds: productIds.slice(0, 3),
    tiktok: String(body.tiktok || ""),
    instagram: String(body.instagram || ""),
    facebook: String(body.facebook || ""),
    uploadDate: body.uploadDate || new Date().toISOString(),
  };

  try {
    await env.MZ_VIDEOS.put(`video:${videoId}`, JSON.stringify(record));
  } catch (err) {
    console.log(`video upsert write failed: ${err}`);
    return upsertJson({ ok: false, error: "kv_write_failed" }, 500);
  }

  return upsertJson({ ok: true, videoId, url: `https://${CONFIG.SITE_DOMAIN}/video/${videoId}` });
}

function slugify(s) {
  return String(s)
    .toLowerCase()
    .trim()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 80);
}

function upsertJson(obj, status = 200) {
  return new Response(JSON.stringify(obj), {
    status,
    headers: { "Content-Type": "application/json", "Cache-Control": "no-store" },
  });
}

function shareButtons(meta, pageUrl) {
  const enc = encodeURIComponent(pageUrl);
  const yt = meta.youtubeId ? `https://www.youtube.com/watch?v=${meta.youtubeId}` : pageUrl;
  const out = [
    `<a class="mz-yt" href="${escapeHtml(yt)}" target="_blank" rel="noopener">YouTube</a>`,
    `<a class="mz-fb" href="https://www.facebook.com/sharer/sharer.php?u=${enc}" target="_blank" rel="noopener">Facebook</a>`,
  ];
  if (meta.tiktok) out.push(`<a class="mz-tt" href="${escapeHtml(meta.tiktok)}" target="_blank" rel="noopener">TikTok</a>`);
  if (meta.instagram) out.push(`<a class="mz-ig" href="${escapeHtml(meta.instagram)}" target="_blank" rel="noopener">Instagram</a>`);
  return out.join("");
}
