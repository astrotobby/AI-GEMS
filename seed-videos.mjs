// seed-videos.mjs — standalone video-page seeder. Independent of make.com.
// Reads ./videos.json (an array of video objects) and POSTs each to the Worker's
// /mz/video-upsert endpoint. Run:  node seed-videos.mjs
//
// Requires Node 18+ (global fetch). No dependencies.

import { readFile } from "node:fs/promises";

// --- config: set these (or pass via env vars) -------------------------------
const SITE = process.env.MZ_SITE || "https://astrotobby.site";
const TOKEN = process.env.MZ_INGEST_TOKEN || "REPLACE_WITH_DIFFERENT_LONG_RANDOM_STRING";
const FILE = process.env.MZ_VIDEOS_FILE || "./videos.json";
// ---------------------------------------------------------------------------

if (TOKEN.startsWith("REPLACE_")) {
  console.error("Set MZ_INGEST_TOKEN (or edit TOKEN in this file) to your INGEST_TOKEN.");
  process.exit(1);
}

let videos;
try {
  videos = JSON.parse(await readFile(FILE, "utf8"));
} catch (err) {
  console.error(`Could not read ${FILE}: ${err.message}`);
  process.exit(1);
}
if (!Array.isArray(videos)) {
  console.error(`${FILE} must contain a JSON array of video objects.`);
  process.exit(1);
}

let ok = 0;
let failed = 0;
for (const v of videos) {
  try {
    const resp = await fetch(`${SITE}/mz/video-upsert`, {
      method: "POST",
      headers: { Authorization: `Bearer ${TOKEN}`, "Content-Type": "application/json" },
      body: JSON.stringify(v),
    });
    const data = await resp.json().catch(() => ({}));
    if (resp.ok && data.ok) {
      ok++;
      console.log(`OK   ${data.url}`);
    } else {
      failed++;
      console.log(`FAIL ${v.title || v.youtubeId}: ${resp.status} ${JSON.stringify(data)}`);
    }
  } catch (err) {
    failed++;
    console.log(`FAIL ${v.title || v.youtubeId}: ${err.message}`);
  }
}
console.log(`\nDone. ${ok} created/updated, ${failed} failed.`);
