// TASK 2 — Exit-intent + timer email capture popup (injected into every page).
import { CONFIG, webhookConfigured } from "../../config.js";
import { attachEmailToCart } from "./cart.js";

// Copy varies by page category. We classify from the URL path.
function copyFor(category) {
  switch (category) {
    case "video":
      return { h: "Get every new video first", p: "Join the list — fresh crypto & GameFi videos straight to your inbox.", cta: "Notify Me" };
    case "blog-post":
      return { h: "Don't miss the next drop", p: "Weekly alpha, guides and product deals. No spam.", cta: "Subscribe" };
    default:
      return { h: "Join the insiders", p: "Get our best posts and exclusive Shopify deals by email.", cta: "Join Free" };
  }
}

export function classifyCategory(pathname) {
  if (pathname.startsWith("/video/")) return "video";
  if (/^\/(posts?|blog|article)\//.test(pathname)) return "blog-post";
  return "blog";
}

// Returns the popup markup + CSS + JS as one injectable string.
// The JS self-suppresses if the mz_sub cookie is present (already subscribed).
export function renderPopup(category) {
  const c = copyFor(category);
  return `
<div id="mz-popup-overlay" hidden>
  <div id="mz-popup" role="dialog" aria-label="Email signup">
    <button id="mz-popup-close" aria-label="Close">&times;</button>
    <h3>${c.h}</h3>
    <p>${c.p}</p>
    <form id="mz-popup-form">
      <input type="email" name="email" placeholder="you@email.com" required autocomplete="email"/>
      <button type="submit">${c.cta}</button>
    </form>
    <small id="mz-popup-msg"></small>
  </div>
</div>
<style>
#mz-popup-overlay{position:fixed;inset:0;background:rgba(0,0,0,.55);display:flex;align-items:center;justify-content:center;z-index:99999;font-family:system-ui,sans-serif;padding:16px}
#mz-popup{background:#fff;max-width:420px;width:100%;border-radius:16px;padding:28px 24px;position:relative;text-align:center;box-shadow:0 20px 60px rgba(0,0,0,.3)}
#mz-popup h3{margin:0 0 8px;font-size:22px}
#mz-popup p{margin:0 0 18px;color:#444;font-size:15px}
#mz-popup-form{display:flex;flex-direction:column;gap:10px}
#mz-popup-form input{padding:13px 14px;border:1px solid #ccc;border-radius:10px;font-size:16px}
#mz-popup-form button{padding:13px;border:0;border-radius:10px;background:#111;color:#fff;font-weight:600;font-size:16px;cursor:pointer}
#mz-popup-close{position:absolute;top:10px;right:14px;border:0;background:none;font-size:26px;line-height:1;cursor:pointer;color:#888}
#mz-popup-msg{display:block;margin-top:10px;min-height:18px;font-size:13px}
@media(max-width:480px){#mz-popup{padding:22px 18px}}
</style>
<script>
(function(){
  function hasCookie(n){return document.cookie.split(';').some(function(c){return c.trim().indexOf(n+'=')===0});}
  if(hasCookie('mz_sub')) return; // already subscribed -> never show
  var shown=false, overlay=document.getElementById('mz-popup-overlay');
  if(!overlay) return;
  function show(){ if(shown) return; shown=true; overlay.hidden=false; }
  // (a) 30s timer
  var t=setTimeout(show, ${CONFIG.POPUP_TIMER_MS});
  // (b) exit intent: mouse leaves toward top of viewport
  document.addEventListener('mouseout', function(e){
    if(!e.relatedTarget && e.clientY <= 0){ show(); }
  });
  document.getElementById('mz-popup-close').addEventListener('click', function(){ overlay.hidden=true; clearTimeout(t); });
  document.getElementById('mz-popup-form').addEventListener('submit', async function(ev){
    ev.preventDefault();
    var email=ev.target.email.value, msg=document.getElementById('mz-popup-msg');
    msg.textContent='Saving...'; msg.style.color='#444';
    try{
      var r=await fetch('/mz/lead',{method:'POST',headers:{'Content-Type':'application/json'},
        body:JSON.stringify({email:email, category:${JSON.stringify(category)}, path:location.pathname})});
      if(!r.ok) throw new Error('bad status '+r.status);
      msg.textContent='You are in. Check your inbox!'; msg.style.color='#0a7d2c';
      document.cookie='mz_sub=1; Path=/; Max-Age=31536000; SameSite=Lax; Secure';
      setTimeout(function(){ overlay.hidden=true; }, 1500);
    }catch(err){ msg.textContent='Something went wrong, please retry.'; msg.style.color='#c0392b'; }
  });
})();
</script>`;
}

// Server-side handler for POST /mz/lead
export async function handleLead(request, env, visitorId) {
  let body;
  try {
    body = await request.json();
  } catch {
    return json({ ok: false, error: "invalid_json" }, 400);
  }
  const email = (body.email || "").trim().toLowerCase();
  if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email)) {
    return json({ ok: false, error: "invalid_email" }, 400);
  }

  const record = {
    email,
    category: body.category || "blog",
    path: body.path || "",
    visitorId,
    ts: Date.now(),
  };

  // Store in KV (email is the key so re-subs overwrite, not duplicate).
  try {
    await env.MZ_LEADS.put(`lead:${email}`, JSON.stringify(record));
  } catch (err) {
    console.log(`lead KV write failed: ${err}`);
  }

  // If this visitor has a pending cart, attach the email so recovery can reach them.
  await attachEmailToCart(env, visitorId, email);

  // Optional enrichment: forward to make.com ONLY if a real webhook is set.
  // If not configured, the lead is still safely stored in KV above — the
  // project works fully standalone and never depends on make.com.
  if (webhookConfigured(CONFIG.MAKE_LEAD_WEBHOOK)) {
    try {
      const resp = await fetch(CONFIG.MAKE_LEAD_WEBHOOK, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(record),
      });
      if (!resp.ok) console.log(`make lead webhook returned ${resp.status}`);
    } catch (err) {
      console.log(`make lead webhook failed: ${err}`);
    }
  }

  return json({ ok: true });
}

function json(obj, status = 200) {
  return new Response(JSON.stringify(obj), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}
