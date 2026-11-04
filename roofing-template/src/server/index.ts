/**
 * Server runtime for the Whop-hosted site.
 *
 * Whop hosting attaches the app's API key to every server-side fetch to the
 * Whop API, so no key is stored or handled here. Routes:
 *
 *   POST /api/leads                    "Request an Estimate" → Whop Leads API
 *   POST /api/deposit/setup-intent     save card on file (Setup Intents API)
 *   GET  /api/deposit/setup-intent/:id poll a setup intent until it succeeds
 *   POST /api/deposit/charge           charge the $500 deposit to the saved card
 *   POST /api/admin/final-invoice      bill the final balance (Invoices API)  [ADMIN_TOKEN]
 *   GET  /api/health                   config sanity check
 *
 * Everything else falls through to the static client build.
 */

import { site } from "../site.config";

interface Env {
  ASSETS?: { fetch: (req: Request) => Promise<Response> };
  WHOP_API_ORIGIN?: string;
  WHOP_ACCOUNT_ID?: string;
  ADMIN_TOKEN?: string;
  /** Optional: only needed if the platform-injected key is unavailable (e.g. local dev without `whop apps dev`). */
  WHOP_API_KEY?: string;
}

const API_VERSION = "2026-10-30";

function readEnv(env: Env | undefined): Env {
  const proc = (globalThis as { process?: { env?: Record<string, string | undefined> } }).process;
  const p = proc?.env ?? {};
  return {
    ...env,
    WHOP_API_ORIGIN: env?.WHOP_API_ORIGIN ?? p.WHOP_API_ORIGIN ?? "https://api.whop.com",
    WHOP_ACCOUNT_ID: env?.WHOP_ACCOUNT_ID ?? p.WHOP_ACCOUNT_ID,
    ADMIN_TOKEN: env?.ADMIN_TOKEN ?? p.ADMIN_TOKEN,
    WHOP_API_KEY: env?.WHOP_API_KEY ?? p.WHOP_API_KEY,
  };
}

class HttpError extends Error {
  constructor(
    public status: number,
    message: string,
    public detail?: unknown,
  ) {
    super(message);
  }
}

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json", "cache-control": "no-store" },
  });

async function whopApi<T = Record<string, unknown>>(
  env: Env,
  method: "GET" | "POST",
  path: string,
  body?: unknown,
  idempotencyKey?: string,
): Promise<T> {
  const headers: Record<string, string> = {
    "content-type": "application/json",
    "Api-Version-Date": API_VERSION,
  };
  // Hosted runtime injects the key automatically; a local key is only a fallback.
  if (env.WHOP_API_KEY) headers.authorization = `Bearer ${env.WHOP_API_KEY}`;
  if (idempotencyKey) headers["Idempotency-Key"] = idempotencyKey;

  const res = await fetch(`${env.WHOP_API_ORIGIN}/api/v1${path}`, {
    method,
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const text = await res.text();
  let data: unknown = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    data = { raw: text };
  }
  if (!res.ok) {
    const msg =
      (data as { error?: { message?: string } } | null)?.error?.message ?? `Whop API ${method} ${path} failed (${res.status})`;
    console.error("whop_api_error", method, path, res.status, text.slice(0, 500));
    throw new HttpError(res.status >= 500 ? 502 : 400, msg, data);
  }
  return data as T;
}

function requireAccount(env: Env): string {
  if (!env.WHOP_ACCOUNT_ID) throw new HttpError(500, "WHOP_ACCOUNT_ID is not available in this runtime.");
  return env.WHOP_ACCOUNT_ID;
}

const str = (v: unknown, max = 500) => (typeof v === "string" ? v.trim().slice(0, max) : "");
const isEmail = (v: string) => /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(v);

async function readJson(req: Request): Promise<Record<string, unknown>> {
  try {
    const body = await req.json();
    return body && typeof body === "object" ? (body as Record<string, unknown>) : {};
  } catch {
    throw new HttpError(400, "Invalid JSON body.");
  }
}

const planId = (key: "inspection" | "deposit" | "flatRate") => site.plans.find((p) => p.key === key)!.planId;

/* ------------------------------------------------------------------ */
/* Leads                                                               */
/* ------------------------------------------------------------------ */

async function createLead(req: Request, env: Env) {
  const b = await readJson(req);
  const name = str(b.name, 120);
  const email = str(b.email, 200);
  const phone = str(b.phone, 40);
  const address = str(b.address, 300);
  const service = str(b.service, 80);
  const message = str(b.message, 2000);
  if (str(b.company_website)) return json({ ok: true }); // honeypot
  if (!name || !isEmail(email) || !phone) throw new HttpError(400, "Name, a valid email and a phone number are required.");

  const accountId = requireAccount(env);
  // The Leads API records a lead against a Whop user, so resolve the visitor's
  // email to one first: the pixel's person record (linked by the `lead` event
  // the form fires with this email), then the account's members.
  const userId = await findWhopUserId(env, accountId, email);
  if (!userId) {
    // No Whop account behind this email: the pixel `lead` event (fired client
    // side with name/email/phone) still records the request in Whop's People.
    console.log("lead_without_whop_user", email);
    return json({ ok: true, lead_id: null, recorded_as: "pixel_lead_event" });
  }

  const lead = await whopApi<{ id: string }>(env, "POST", "/leads", {
    account_id: accountId,
    user_id: userId,
    product_id: site.plans[0].productId.includes("REPLACE") ? undefined : site.plans[0].productId,
    referrer: req.headers.get("referer") ?? undefined,
    metadata: {
      source: "estimate_form",
      name,
      email,
      phone,
      address,
      service,
      message,
      submitted_at: new Date().toISOString(),
    },
  });
  return json({ ok: true, lead_id: lead.id, recorded_as: "whop_lead" });
}

async function findWhopUserId(env: Env, accountId: string, email: string): Promise<string | null> {
  const qs = (o: Record<string, string>) => new URLSearchParams(o).toString();
  try {
    const person = await whopApi<{ user?: { id?: string } | null; user_ids?: string[] }>(
      env,
      "GET",
      `/people/${encodeURIComponent(email)}?${qs({ account_id: accountId })}`,
    );
    const id = person.user?.id ?? person.user_ids?.[0];
    if (id) return id;
  } catch {
    /* not tracked yet */
  }
  try {
    const members = await whopApi<{ data?: { user?: { id?: string } | null }[] }>(
      env,
      "GET",
      `/members?${qs({ account_id: accountId, query: email, first: "1" })}`,
    );
    const id = members.data?.[0]?.user?.id;
    if (id) return id;
  } catch {
    /* no member match */
  }
  return null;
}

/* ------------------------------------------------------------------ */
/* Deposit: card on file via setup intent, then charge the deposit     */
/* ------------------------------------------------------------------ */

interface SetupIntent {
  id: string;
  status: "processing" | "succeeded" | "canceled" | "requires_action";
  client_secret: string | null;
  member_id: string | null;
  payment_method_id: string | null;
  last_setup_error: { code: string | null; message: string | null } | null;
}

async function createSetupIntent(req: Request, env: Env) {
  const b = await readJson(req);
  const confirmationToken = str(b.confirmation_token, 200);
  const email = str(b.email, 200);
  if (!confirmationToken) throw new HttpError(400, "confirmation_token is required.");

  const origin = new URL(req.url).origin;
  const si = await whopApi<SetupIntent>(
    env,
    "POST",
    "/setup_intents",
    {
      account_id: requireAccount(env),
      confirmation_token: confirmationToken,
      email: isEmail(email) ? email : undefined,
      currency: "usd",
      return_url: `${origin}/?deposit=return#deposit`,
      metadata: {
        purpose: "roofing_deposit_card_on_file",
        job_address: str(b.job_address, 300),
      },
    },
    `si_${confirmationToken}`,
  );
  return json(publicSetupIntent(si));
}

const publicSetupIntent = (si: SetupIntent) => ({
  id: si.id,
  status: si.status,
  client_secret: si.client_secret,
  has_payment_method: Boolean(si.payment_method_id),
  error: si.last_setup_error?.message ?? null,
});

async function getSetupIntent(id: string, env: Env) {
  if (!/^sint_[A-Za-z0-9]+$/.test(id)) throw new HttpError(400, "Invalid setup intent id.");
  const si = await whopApi<SetupIntent>(env, "GET", `/setup_intents/${id}`);
  return json(publicSetupIntent(si));
}

async function chargeDeposit(req: Request, env: Env) {
  const b = await readJson(req);
  const setupIntentId = str(b.setup_intent_id, 80);
  if (!/^sint_[A-Za-z0-9]+$/.test(setupIntentId)) throw new HttpError(400, "setup_intent_id is required.");

  const si = await whopApi<SetupIntent>(env, "GET", `/setup_intents/${setupIntentId}`);
  if (si.status !== "succeeded" || !si.payment_method_id || !si.member_id) {
    throw new HttpError(409, `Card is not saved yet (status: ${si.status}).`);
  }
  const payment = await whopApi<{ id: string; status: string }>(
    env,
    "POST",
    "/payments",
    {
      account_id: requireAccount(env),
      variant_id: planId("deposit"),
      member_id: si.member_id,
      payment_method_id: si.payment_method_id,
      metadata: { purpose: "roofing_project_deposit", setup_intent_id: si.id },
    },
    `deposit_${si.id}`,
  );
  return json({ ok: true, payment_id: payment.id, status: payment.status });
}

/* ------------------------------------------------------------------ */
/* Final balance: invoice the saved card, or email a pay link          */
/* ------------------------------------------------------------------ */

async function createFinalInvoice(req: Request, env: Env) {
  const auth = req.headers.get("authorization") ?? "";
  if (!env.ADMIN_TOKEN || auth !== `Bearer ${env.ADMIN_TOKEN}`) throw new HttpError(401, "Unauthorized.");

  const b = await readJson(req);
  const setupIntentId = str(b.setup_intent_id, 80);
  const email = str(b.email, 200);
  const amount = Number(b.amount);
  const description = str(b.description, 1000) || "Final balance — roofing project";
  const mode = b.mode === "send" ? "send" : "charge"; // charge = auto-charge saved card, send = email pay link
  if (!Number.isFinite(amount) || amount < 1) throw new HttpError(400, "amount (USD) must be at least 1.");

  let memberId: string | undefined;
  let paymentMethodId: string | undefined;
  if (setupIntentId) {
    const si = await whopApi<SetupIntent>(env, "GET", `/setup_intents/${setupIntentId}`);
    memberId = si.member_id ?? undefined;
    paymentMethodId = si.payment_method_id ?? undefined;
  }
  if (!memberId && !isEmail(email)) throw new HttpError(400, "Provide setup_intent_id (card on file) or a customer email.");

  const accountId = requireAccount(env);
  const invoice = await whopApi<{ id: string; status: string; checkout_link?: string | null }>(env, "POST", "/invoices", {
    account_id: accountId,
    ...(memberId ? { member_id: memberId } : { email_address: email }),
    ...(mode === "charge" && paymentMethodId ? { payment_method_id: paymentMethodId } : {}),
    items: [
      {
        variant: {
          currency: "usd",
          billing_type: "one_time",
          initial_price: Math.round(amount * 100) / 100,
          title: "Final balance",
          product: { external_identifier: "roofing-final-balance", title: `${site.company.name} — Project balance` },
        },
        description,
      },
    ],
    memo: `Thank you for choosing ${site.company.name}. Your deposit has already been applied.`,
    metadata: { purpose: "roofing_final_balance", setup_intent_id: setupIntentId || "" },
  });

  // charge: issue → auto-charges the saved payment method right away.
  // send:   send  → emails the invoice with its payment link.
  const action = mode === "charge" && paymentMethodId ? "issue" : "send";
  const finalized = await whopApi<{ id: string; status: string; checkout_link?: string | null }>(
    env,
    "POST",
    `/invoices/${invoice.id}/${action}`,
    {},
  );
  return json({ ok: true, invoice_id: finalized.id, status: finalized.status, action, checkout_link: finalized.checkout_link ?? null });
}

/* ------------------------------------------------------------------ */

async function route(req: Request, env: Env): Promise<Response | null> {
  const url = new URL(req.url);
  const { pathname } = url;
  if (!pathname.startsWith("/api/")) return null;

  if (pathname === "/api/health" && req.method === "GET") {
    return json({
      ok: true,
      account_configured: Boolean(env.WHOP_ACCOUNT_ID),
      plans_configured: site.plans.every((p) => !p.planId.includes("REPLACE")),
      admin_configured: Boolean(env.ADMIN_TOKEN),
    });
  }
  if (pathname === "/api/leads" && req.method === "POST") return createLead(req, env);
  if (pathname === "/api/deposit/setup-intent" && req.method === "POST") return createSetupIntent(req, env);
  const m = pathname.match(/^\/api\/deposit\/setup-intent\/([^/]+)$/);
  if (m && req.method === "GET") return getSetupIntent(m[1], env);
  if (pathname === "/api/deposit/charge" && req.method === "POST") return chargeDeposit(req, env);
  if (pathname === "/api/admin/final-invoice" && req.method === "POST") return createFinalInvoice(req, env);
  return json({ error: "Not found" }, 404);
}

export default {
  async fetch(req: Request, rawEnv?: Env): Promise<Response> {
    const env = readEnv(rawEnv);
    try {
      const res = await route(req, env);
      if (res) return res;
    } catch (err) {
      if (err instanceof HttpError) return json({ error: err.message }, err.status);
      console.error("unhandled_error", err);
      return json({ error: "Something went wrong. Please call us instead." }, 500);
    }
    // Static assets / SPA fallback.
    if (env.ASSETS) {
      const asset = await env.ASSETS.fetch(req);
      if (asset.status !== 404) return asset;
      const url = new URL(req.url);
      return env.ASSETS.fetch(new Request(new URL("/index.html", url.origin), req));
    }
    return new Response("Not found", { status: 404 });
  },
};
