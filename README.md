# Blog Monetization Worker

One Cloudflare Worker that sits in front of your Cloudflare Pages blog and adds six
monetization systems. It proxies every page from your Pages origin, injects
components with `HTMLRewriter`, exposes a few API endpoints, and runs a Cron-driven
abandoned-cart scan.

```
Visitor ─▶ Worker (route: astrotobby.site/*)
              ├─ /video/{id}          → generated landing page      (Task 3)
              ├─ /mz/click            → log + redirect to Shopify    (Task 1/6)
              ├─ /mz/lead             → email capture → make.com      (Task 2)
              ├─ /mz/track            → analytics beacon              (Task 5)
              ├─ /analytics/dashboard → JSON metrics (token-gated)    (Task 5)
              └─ everything else      → fetch Pages origin + inject:
                     ad slot (Task 4) + product card (Task 1) + popup (Task 2)
Cron */15m ─▶ scan KV for carts > 60 min old → make.com webhook       (Task 6)
```

| Task | What it does | Files |
|------|--------------|-------|
| 1 | Geo-aware, session-sticky Shopify product rotation | `handlers/products.js` |
| 2 | Exit-intent + 30s timer email popup, cookie dedupe | `handlers/popup.js` |
| 3 | `/video/{id}` landing pages w/ YouTube + products + schema | `handlers/videoPage.js` |
| 4 | Geo ad-network rotation w/ client-side fallback | `handlers/ads.js` |
| 5 | Tracking beacon + dashboard JSON | `handlers/analytics.js` |
| 6 | Abandoned-cart recovery via Cron scan | `handlers/cart.js` |

---

## Honest notes (read these — they affect what's realistic)

1. **One Worker, not six.** Only one Worker can own a route in front of your site.
   Six separate Workers would conflict. This is the correct architecture; each task
   is a module.
2. **The "1-hour delay" cart trigger** is implemented as a Cron scan every 15 min
   (Cloudflare cannot schedule a one-off job per visitor). A cart fires 60–75 min
   after the view. Tune `CART_RECOVERY_DELAY_MIN` and the cron in `wrangler.toml`.
3. **Ad "revenue per network" is estimated, not measured.** Ad networks tally real
   impressions/revenue on their side; a Worker can only count impressions and the
   clicks that pass through your own beacon. The dashboard reports impressions and
   beacon clicks — treat revenue as directional, and reconcile against each
   network's own dashboard.
4. **KV is eventually consistent and has no atomic counter.** The daily counters
   are read-modify-write, which is fine at blog scale (thousands/day) but will
   undercount under heavy concurrency. If you outgrow it, switch counters to
   **Cloudflare Analytics Engine** (see "Scaling").
5. **The "+20% / +30% / +15% in week one" targets are goals, not guarantees.** No
   code can promise a revenue lift — it depends on your traffic and offers. The
   dashboard gives you the numbers to measure whether you're hitting them.
6. Respect each ad network's and Shopify's ToS for your account. Geo ad rotation is
   standard; check your Adsterra/Monetag/Bidglow agreements allow concurrent tags.

---

## Deployment checklist

### 0. Prerequisites
- Node 18+ installed.
- A Cloudflare account with your blog domain already on Cloudflare and the site
  served by Cloudflare Pages.

### 1. Install + log in
```powershell
cd C:\Users\Admin\blog-monetization-worker
npm install
npx wrangler login        # opens browser → Authorize
```

### 2. Create the KV namespaces
```powershell
./setup-kv.ps1            # Windows
# or:  bash setup-kv.sh   # mac/linux/git-bash
```
Copy each printed `id` into the matching `[[kv_namespaces]]` block in `wrangler.toml`.

### 3. Fill in `config.js`
Set: `SITE_DOMAIN`, `ORIGIN` (your `*.pages.dev` URL), `SHOPIFY_STORE`,
`MAKE_LEAD_WEBHOOK`, `MAKE_CART_WEBHOOK`, `DASHBOARD_TOKEN`, then the real
`PRODUCTS`, `AD_NETWORKS` tags, and `AD_REGION_MAP`.

### 4. Point the route at your domain
In `wrangler.toml`, set `pattern` and `zone_name` to your domain. The Worker
fetches unmodified HTML from `ORIGIN`, so `ORIGIN` **must** be the `pages.dev` URL,
never the apex domain (that would loop).

### 5. Deploy
```powershell
npx wrangler deploy
```

### 6. Confirm the route in the dashboard
Cloudflare dashboard → **Workers & Pages** → open **blog-monetization-worker** →
**Settings** → **Domains & Routes** → confirm `astrotobby.site/*` is listed. If not:
**Add → Route**, pattern `astrotobby.site/*`, zone = your domain.

### 7. Confirm the Cron
Same Worker → **Settings** → **Triggers** → **Cron Triggers** → confirm
`*/15 * * * *` is present.

### 8. Seed video metadata (Task 3)
For each video your pipeline produces, write a KV record:
```powershell
npx wrangler kv key put --binding=MZ_VIDEOS "video:my-first-video" `
  '{\"youtubeId\":\"dQw4w9WgXcQ\",\"title\":\"My First Video\",\"summary\":\"Short summary from the blog post.\",\"productIds\":[\"p1\",\"p3\"],\"tiktok\":\"https://tiktok.com/@you/video/123\"}'
```
Then `https://astrotobby.site/video/my-first-video` is live. The line above is the
manual one-off; for the pipeline use the automated endpoint below.

### 8b. Auto-generate video pages via the ingest API (independent of make.com)
The Worker exposes `POST /mz/video-upsert` so video pages can be created
programmatically **without touching your existing make.com publishing scenario.**
Call it from anywhere you like — a one-line script, a cron box, a Postman collection,
or a brand-new standalone automation. Your daily RSS-publish scenario is never edited.

- **URL:** `https://astrotobby.site/mz/video-upsert`
- **Method:** `POST`
- **Headers:** `Authorization: Bearer <INGEST_TOKEN>`, `Content-Type: application/json`
- **Body:**
```json
{
  "youtubeId": "dQw4w9WgXcQ",
  "title": "My Video Title",
  "summary": "First paragraph of the blog post.",
  "productIds": "p1,p3",
  "tiktok": "https://tiktok.com/@you/video/123"
}
```
`productIds` accepts a comma string or a JSON array. The Worker derives the slug from
the title (or pass your own `videoId`), applies defaults, and returns
`{ "ok": true, "url": "https://astrotobby.site/video/<slug>" }`.

Example standalone seeder (run locally, no scenario changes):
```powershell
node seed-videos.mjs    # reads videos.json and POSTs each one
```

> The lead-capture (Task 2) and cart-recovery (Task 6) make.com webhooks are
> **separate, new** webhook-triggered scenarios you create — they do **not** modify
> your existing daily-publishing scenario. And they're optional: if
> `MAKE_LEAD_WEBHOOK` / `MAKE_CART_WEBHOOK` are left blank, leads and carts are still
> tracked in KV and the whole project runs standalone. make.com is enrichment, never
> a dependency.

---

## make.com integration

- **Lead capture (Task 2):** create a *Custom webhook* in make.com → paste its URL
  into `MAKE_LEAD_WEBHOOK`. Incoming JSON: `{ email, category, path, visitorId, ts }`.
- **Cart recovery (Task 6):** another *Custom webhook* → `MAKE_CART_WEBHOOK`.
  Incoming JSON: `{ event:"abandoned_cart", visitorId, productId, email, abandonedAt }`.
  Branch on whether `email` is null; if present, send the recovery email with a
  discount code + product link.
- **Pulling analytics (Task 5):** add an *HTTP → Make a request* module:
  `GET https://astrotobby.site/analytics/dashboard?token=YOUR_DASHBOARD_TOKEN&days=7`.
  Schedule it daily to push the JSON into Airtable/Sheets/Notion.
- **Purchase confirmation (optional, improves Task 6 accuracy):** a Shopify "order
  created" webhook → make.com → `GET https://astrotobby.site/mz/purchased?vid={visitorId}`
  to mark the cart purchased so no recovery email is sent. (Requires passing the
  visitor id into checkout, e.g. as a cart attribute.)

---

## Testing

| Test | How | Expected |
|------|-----|----------|
| Local run | `npx wrangler dev` then open the printed URL | Page loads with product card + ad slot + popup |
| Product rotation | Hard-refresh in normal vs. incognito windows | Same product within one browser for 24h; different browsers differ |
| Geo targeting | `curl -H "cf-ipcountry: KE" <url>` vs `US` (deployed only) | East-Africa products vs Tier-1 products |
| Popup timer | Wait 30s on a page | Popup appears |
| Popup exit-intent | Move mouse above the browser viewport | Popup appears |
| Popup dedupe | Submit email, reload | Popup does **not** reappear (mz_sub cookie) |
| Lead → make.com | Submit email | 200 from `/mz/lead`; row arrives in make.com; `lead:<email>` in KV |
| Click tracking | Click "Buy Now" | Redirect to Shopify product; `product_click` counter rises |
| Video page | Seed a video, open `/video/{id}` | YouTube embeds, products show, view "Page source" shows JSON-LD |
| Ad fallback | Put a bad tag in a non-primary network | Slot swaps to region primary after 2.5s |
| Dashboard | `GET /analytics/dashboard?token=...` | JSON metrics; wrong token → 401 |
| Cart scan | Seed a `cart:<id>` with old `ts`, run `npx wrangler dev --test-scheduled` then hit `/__scheduled` | make.com cart webhook fires; `cart_recovered` rises |

Watch live logs while testing: `npx wrangler tail`.

### Troubleshooting
- **Redirect loop / 522:** `ORIGIN` is pointing at the apex domain. Point it at the
  `*.pages.dev` URL.
- **Injection not showing:** the origin response wasn't `text/html` (e.g. cached as a
  different type), or the page has no `<body>`. Check `wrangler tail`.
- **Popup never shows:** `mz_sub` cookie already set — clear cookies, or you're past
  it. Exit-intent doesn't fire on touch devices (no mouse); the 30s timer covers them.
- **Dashboard 401:** token mismatch between the URL and `config.js`.
- **No cart webhooks:** carts younger than `CART_RECOVERY_DELAY_MIN` are skipped;
  wait, or lower the value for testing.

---

## Maintenance guide

**Monitor KV usage** — Dashboard → **Storage & Databases** → **KV** → each namespace
shows key count and storage. The free plan allows 100k reads/day & 1k writes/day per
namespace; high traffic may need the paid Workers plan. Counters self-expire (90d),
events (30d), carts (48h), sessions (24h), so storage stays bounded.

**Update products** — edit the `PRODUCTS` array in `config.js`, then
`npx wrangler deploy`. Set `regions` to control geo targeting; `["*"]` = everywhere.

**Adjust ad rotation** — `AD_ROTATE_EVERY` in `config.js` controls how many page
views before the network switches; `AD_REGION_MAP` controls which networks are
eligible per region (first entry is the fallback). Redeploy after changes.

**Analyze analytics** — `GET /analytics/dashboard?token=...&days=30` returns totals,
conversion rates, `top_products`, `top_videos`, and `ad_networks` (impressions/clicks
per network). Compare `product_ctr_pct`, `signup_rate_pct`, and ad CTR week over week
to judge the lift targets. Reconcile ad numbers against each network's own dashboard.

**Rotate the dashboard token** — change `DASHBOARD_TOKEN` in `config.js`, redeploy,
update the make.com HTTP module URL.

### Scaling (when KV counters aren't enough)
Add an Analytics Engine binding in `wrangler.toml`:
```toml
[[analytics_engine_datasets]]
binding = "MZ_AE"
dataset = "mz_events"
```
Then replace `bump()/bumpDim()` calls with `env.MZ_AE.writeDataPoint({...})` and query
via the GraphQL Analytics API. This gives accurate, high-throughput counters that KV
cannot. The rest of the Worker is unchanged.
