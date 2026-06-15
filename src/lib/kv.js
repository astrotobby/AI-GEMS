// kv.js — analytics primitives over KV.
//
// NOTE ON CONSISTENCY: Workers KV is eventually consistent and has no atomic
// increment. The read-modify-write counter below is fine at blog scale
// (thousands of views/day). If you grow past that, move counters to
// Cloudflare Analytics Engine or a Durable Object — see README "Scaling".

const todayKey = () => new Date().toISOString().slice(0, 10); // YYYY-MM-DD

// Increment a daily counter, e.g. bump(env, "pageview") -> stat:pageview:2026-06-15
export async function bump(env, metric, n = 1) {
  const key = `stat:${metric}:${todayKey()}`;
  try {
    const cur = parseInt((await env.MZ_ANALYTICS.get(key)) || "0", 10);
    await env.MZ_ANALYTICS.put(key, String(cur + n), {
      expirationTtl: 60 * 60 * 24 * 90,
    });
  } catch (err) {
    console.log(`bump failed for ${metric}: ${err}`);
  }
}

// Increment a named dimension counter, e.g. dimension product clicks.
// bumpDim(env, "product", "p3") -> dim:product:p3:2026-06-15
export async function bumpDim(env, dim, value, n = 1) {
  if (!value) return;
  const key = `dim:${dim}:${value}:${todayKey()}`;
  try {
    const cur = parseInt((await env.MZ_ANALYTICS.get(key)) || "0", 10);
    await env.MZ_ANALYTICS.put(key, String(cur + n), {
      expirationTtl: 60 * 60 * 24 * 90,
    });
  } catch (err) {
    console.log(`bumpDim failed for ${dim}:${value}: ${err}`);
  }
}

// Store a raw event for audit/debug. Auto-expires.
export async function logEvent(env, type, data, ttl) {
  const key = `evt:${type}:${Date.now()}:${crypto.randomUUID().slice(0, 8)}`;
  try {
    await env.MZ_ANALYTICS.put(
      key,
      JSON.stringify({ type, ts: Date.now(), ...data }),
      { expirationTtl: ttl || 60 * 60 * 24 * 30 }
    );
  } catch (err) {
    console.log(`logEvent failed for ${type}: ${err}`);
  }
}

// Aggregate every daily counter for a metric over the last N days.
export async function sumMetric(env, metric, days = 7) {
  let total = 0;
  const byDay = {};
  const now = Date.now();
  for (let i = 0; i < days; i++) {
    const d = new Date(now - i * 86400000).toISOString().slice(0, 10);
    const v = parseInt((await env.MZ_ANALYTICS.get(`stat:${metric}:${d}`)) || "0", 10);
    byDay[d] = v;
    total += v;
  }
  return { total, byDay };
}

// Top values for a dimension (e.g. top products) over last N days.
export async function topDim(env, dim, days = 7, limit = 10) {
  const totals = {};
  let cursor;
  do {
    const list = await env.MZ_ANALYTICS.list({ prefix: `dim:${dim}:`, cursor });
    for (const k of list.keys) {
      // key shape: dim:product:p3:2026-06-15
      const parts = k.name.split(":");
      const day = parts[parts.length - 1];
      const value = parts.slice(2, -1).join(":");
      const ageDays = (Date.now() - new Date(day).getTime()) / 86400000;
      if (ageDays > days) continue;
      const v = parseInt((await env.MZ_ANALYTICS.get(k.name)) || "0", 10);
      totals[value] = (totals[value] || 0) + v;
    }
    cursor = list.list_complete ? null : list.cursor;
  } while (cursor);

  return Object.entries(totals)
    .map(([value, count]) => ({ value, count }))
    .sort((a, b) => b.count - a.count)
    .slice(0, limit);
}
