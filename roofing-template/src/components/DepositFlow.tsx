import { useEffect, useRef, useState } from "react";
import { BrandingElement, EmailElement, PaymentElement, Payments, usePayments, useWhop } from "@whop/elements-react";
import { planByKey, plansConfigured, site } from "../site.config";
import { track, sessionId } from "../lib/track";

/**
 * Deposit → job → final invoice, step 1.
 *
 *  1. Payment Elements in `setup` mode collect a card (or wallet) and save it
 *     on file via a Whop setup intent — nothing is charged yet.
 *  2. Once the setup intent succeeds, the server charges the $500 deposit
 *     variant to that saved method.
 *  3. After the job, the office bills the final balance with the Invoices API
 *     against the same saved method (see AdminInvoice / POST /api/admin/final-invoice).
 */

type Step =
  | { kind: "form" }
  | { kind: "saving" }
  | { kind: "charging"; setupIntentId: string }
  | { kind: "done"; setupIntentId: string; paymentId: string }
  | { kind: "error"; message: string; setupIntentId?: string };

interface SetupIntentResponse {
  id: string;
  status: "processing" | "succeeded" | "canceled" | "requires_action";
  client_secret: string | null;
  has_payment_method: boolean;
  error: string | null;
  // set when the server answers with an error
}

async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, { ...init, headers: { "content-type": "application/json", ...init?.headers } });
  const body = (await res.json().catch(() => ({}))) as T & { error?: string };
  if (!res.ok) throw new Error(body.error ?? `Request failed (${res.status})`);
  return body;
}

async function waitForSetup(id: string, tries = 20): Promise<SetupIntentResponse> {
  for (let i = 0; i < tries; i++) {
    const si = await api<SetupIntentResponse>(`/api/deposit/setup-intent/${id}`);
    if (si.status === "succeeded" || si.status === "canceled") return si;
    await new Promise((r) => setTimeout(r, 1500));
  }
  throw new Error("Saving your card is taking longer than expected. Please refresh in a minute.");
}

async function chargeDeposit(setupIntentId: string) {
  return api<{ payment_id: string; status: string }>("/api/deposit/charge", {
    method: "POST",
    body: JSON.stringify({ setup_intent_id: setupIntentId }),
  });
}

export function DepositFlow() {
  const deposit = planByKey("deposit");
  const [step, setStep] = useState<Step>({ kind: "form" });

  // Returning from an off-site step (3DS / bank page): ?setup=sint_…&status=…
  useEffect(() => {
    const params = new URLSearchParams(location.search);
    const setupId = params.get("setup");
    if (!setupId?.startsWith("sint_")) return;
    history.replaceState(null, "", location.pathname + location.hash);
    if (params.get("status") !== "succeeded") {
      setStep({ kind: "error", message: "Card verification was not completed. Please try again." });
      return;
    }
    void finish(setupId);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function finish(setupIntentId: string) {
    try {
      setStep({ kind: "charging", setupIntentId });
      const si = await waitForSetup(setupIntentId);
      if (si.status !== "succeeded") throw new Error(si.error ?? "Your card could not be saved.");
      const payment = await chargeDeposit(setupIntentId);
      setStep({ kind: "done", setupIntentId, paymentId: payment.payment_id });
      try {
        localStorage.setItem("roof_deposit_setup_intent", setupIntentId);
      } catch {
        /* storage unavailable */
      }
    } catch (err) {
      setStep({ kind: "error", message: err instanceof Error ? err.message : "Deposit failed.", setupIntentId });
    }
  }

  return (
    <section className="section" id="deposit">
      <div className="container deposit">
        <div>
          <p className="eyebrow">Ready to schedule?</p>
          <h2>Lock your install date with a ${deposit.price} deposit</h2>
          <ol className="steps">
            <li>
              <strong>Deposit today.</strong> Your card is saved securely on file and the ${deposit.price} deposit is charged.
            </li>
            <li>
              <strong>We do the job.</strong> Materials ordered, crew scheduled, photo report on completion.
            </li>
            <li>
              <strong>One final invoice.</strong> The remaining balance is billed to the card on file — or emailed to you to pay
              however you like.
            </li>
          </ol>
          <p className="muted small">The deposit is applied to your project total. Eligible financing options are offered on the final invoice.</p>
        </div>

        <div className="card">
          {!plansConfigured ? (
            <p className="error">Plans are not configured yet. Run <code>npm run setup:plans</code> and redeploy.</p>
          ) : step.kind === "done" ? (
            <div className="success" role="status">
              <h3>Deposit received — you're on the schedule!</h3>
              <p>Your card is on file for the final balance. We'll call within one business day with your crew date.</p>
              <p className="muted small">
                Payment: {step.paymentId} · Card on file: {step.setupIntentId}
              </p>
            </div>
          ) : step.kind === "charging" ? (
            <p role="status">Card saved. Charging your ${deposit.price} deposit…</p>
          ) : (
            <Payments
              accountId={site.whopAccountId}
              mode="setup"
              currency="usd"
              setupFutureUsage="off_session"
              returnUrl={`${location.origin}/#deposit`}
            >
              <DepositForm
                busy={step.kind === "saving"}
                error={step.kind === "error" ? step.message : null}
                onSaving={() => setStep({ kind: "saving" })}
                onSaved={finish}
                onError={(message) => setStep({ kind: "error", message })}
              />
            </Payments>
          )}
        </div>
      </div>
    </section>
  );
}

function DepositForm(props: {
  busy: boolean;
  error: string | null;
  onSaving: () => void;
  onSaved: (setupIntentId: string) => void;
  onError: (message: string) => void;
}) {
  const payments = usePayments();
  const whop = useWhop();
  const deposit = planByKey("deposit");
  const [complete, setComplete] = useState(false);
  const [email, setEmail] = useState("");
  const [jobAddress, setJobAddress] = useState("");
  const started = useRef(false);

  const markStarted = () => {
    if (started.current) return;
    started.current = true;
    track("deposit_started", { value: deposit.price, currency: "USD", event_id: `deposit_started_${sessionId()}` });
    track("initiate_checkout", { variant_id: deposit.planId, product_id: deposit.productId, value: deposit.price, currency: "USD" });
  };

  async function submit() {
    if (!payments || !whop) return;
    markStarted();
    props.onSaving();
    try {
      const { confirmationToken } = await payments.createConfirmationToken({ billingDetails: { email } });
      const si = await api<SetupIntentResponse>("/api/deposit/setup-intent", {
        method: "POST",
        body: JSON.stringify({ confirmation_token: confirmationToken, email, job_address: jobAddress }),
      });
      if (si.status === "requires_action" && si.client_secret) {
        const next = await whop.payments.handleNextAction({ clientSecret: si.client_secret });
        if (next.redirected) return; // continues on return via ?setup=…
        if (next.lastPaymentError) throw new Error(next.lastPaymentError.message ?? "Card verification failed.");
      }
      if (si.status === "canceled") throw new Error(si.error ?? "Your card could not be saved.");
      props.onSaved(si.id);
    } catch (err) {
      const code = (err as { code?: string }).code;
      if (code === "payment_request_cancelled") {
        props.onError("Payment sheet closed — no charge was made.");
        return;
      }
      props.onError(err instanceof Error ? err.message : "Could not save your card.");
    }
  }

  return (
    <div className="deposit-form" onFocusCapture={markStarted}>
      <label>
        Job address
        <input value={jobAddress} onChange={(e) => setJobAddress(e.target.value)} placeholder="Street, city, ZIP" />
      </label>
      <EmailElement onChange={(p) => setEmail(p.email)} />
      <PaymentElement onChange={(p) => setComplete(p.complete)} />
      <BrandingElement />
      {props.error && (
        <p className="error" role="alert">
          {props.error}
        </p>
      )}
      <button className="btn btn-primary btn-block" disabled={!payments || !complete || props.busy} onClick={submit}>
        {props.busy ? "Processing…" : `Save card & pay $${deposit.price} deposit`}
      </button>
    </div>
  );
}
