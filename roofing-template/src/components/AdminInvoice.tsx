import { useState, type FormEvent } from "react";

/**
 * Office-only page at /#admin: bill the final balance with the Whop Invoices API.
 * Requires the ADMIN_TOKEN app secret. "Charge card on file" auto-charges the
 * payment method saved at deposit; "Email invoice" sends a pay link instead.
 */
export function AdminInvoice() {
  const [result, setResult] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const lastSetup = (() => {
    try {
      return localStorage.getItem("roof_deposit_setup_intent") ?? "";
    } catch {
      return "";
    }
  })();

  async function onSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const f = Object.fromEntries(new FormData(e.currentTarget).entries()) as Record<string, string>;
    setBusy(true);
    setError(null);
    setResult(null);
    try {
      const res = await fetch("/api/admin/final-invoice", {
        method: "POST",
        headers: { "content-type": "application/json", authorization: `Bearer ${f.token}` },
        body: JSON.stringify({
          setup_intent_id: f.setup_intent_id,
          email: f.email,
          amount: Number(f.amount),
          description: f.description,
          mode: f.mode,
        }),
      });
      const body = await res.json();
      if (!res.ok) throw new Error(body.error ?? "Invoice failed");
      setResult(JSON.stringify(body, null, 2));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Invoice failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="section">
      <div className="container narrow">
        <h2>Bill final balance</h2>
        <p className="section-sub">Creates a Whop invoice for the remaining project balance.</p>
        <form className="card form" onSubmit={onSubmit}>
          <label>
            Admin token
            <input name="token" type="password" required />
          </label>
          <label>
            Card-on-file reference (setup intent, <code>sint_…</code>)
            <input name="setup_intent_id" defaultValue={lastSetup} placeholder="sint_…" />
          </label>
          <label>
            Customer email (if no card on file)
            <input name="email" type="email" />
          </label>
          <div className="row">
            <label>
              Balance (USD)
              <input name="amount" type="number" min={1} step="0.01" defaultValue={8600} required />
            </label>
            <label>
              Collection
              <select name="mode" defaultValue="charge">
                <option value="charge">Charge card on file</option>
                <option value="send">Email invoice to pay</option>
              </select>
            </label>
          </div>
          <label>
            Line item description
            <input name="description" defaultValue="Roof replacement — final balance (deposit applied)" />
          </label>
          {error && <p className="error">{error}</p>}
          <button className="btn btn-primary" disabled={busy}>
            {busy ? "Creating…" : "Create invoice"}
          </button>
        </form>
        {result && <pre className="card result">{result}</pre>}
        <p className="small">
          <a href="#">← Back to site</a>
        </p>
      </div>
    </section>
  );
}
