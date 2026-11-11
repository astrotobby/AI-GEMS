import { useState, type FormEvent } from "react";
import { site } from "../site.config";
import { track } from "../lib/track";

type Status = { kind: "idle" } | { kind: "sending" } | { kind: "done"; leadId: string } | { kind: "error"; message: string };

export function EstimateForm() {
  const [status, setStatus] = useState<Status>({ kind: "idle" });

  async function onSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const form = e.currentTarget;
    const data = Object.fromEntries(new FormData(form).entries());
    setStatus({ kind: "sending" });
    // Customer matching: hand the pixel the contact details first, so Whop can
    // link this visitor to their Whop account before the server records the lead.
    track("lead", { name: String(data.name ?? ""), email: String(data.email ?? ""), phone: String(data.phone ?? "") });
    try {
      const res = await fetch("/api/leads", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(data),
      });
      const body = (await res.json()) as { lead_id?: string | null; error?: string };
      if (!res.ok) throw new Error(body.error ?? "Could not send your request.");
      // Fire after the request is confirmed so the event matches a real submission.
      track("estimate_requested", { service: String(data.service ?? ""), event_id: body.lead_id ?? undefined });
      setStatus({ kind: "done", leadId: body.lead_id ?? "" });
      form.reset();
    } catch (err) {
      setStatus({ kind: "error", message: err instanceof Error ? err.message : "Something went wrong." });
    }
  }

  return (
    <section className="section alt" id="estimate">
      <div className="container estimate">
        <div>
          <p className="eyebrow">Free, no-obligation</p>
          <h2>Request an estimate</h2>
          <p className="lead">Tell us about your roof. A project manager will call within one business day to schedule a visit.</p>
          <p>
            Prefer to talk? <a href={site.company.phoneHref}>{site.company.phone}</a>
          </p>
        </div>

        {status.kind === "done" ? (
          <div className="card success" role="status">
            <h3>Thanks — we got it!</h3>
            <p>Your request is in our queue. Expect a call within one business day.</p>
            {status.leadId && <p className="muted small">Reference: {status.leadId}</p>}
            <button className="btn btn-outline" onClick={() => setStatus({ kind: "idle" })}>
              Send another request
            </button>
          </div>
        ) : (
          <form className="card form" onSubmit={onSubmit} noValidate={false}>
            <div className="row">
              <label>
                Full name
                <input name="name" required autoComplete="name" />
              </label>
              <label>
                Phone
                <input name="phone" type="tel" required autoComplete="tel" />
              </label>
            </div>
            <label>
              Email
              <input name="email" type="email" required autoComplete="email" />
            </label>
            <label>
              Property address
              <input name="address" autoComplete="street-address" placeholder="Street, city, ZIP" />
            </label>
            <label>
              What do you need?
              <select name="service" defaultValue={site.estimateServices[0]}>
                {site.estimateServices.map((s) => (
                  <option key={s}>{s}</option>
                ))}
              </select>
            </label>
            <label>
              Details
              <textarea name="message" rows={4} placeholder="Leak location, roof age, insurance claim, timeline…" />
            </label>
            {/* Honeypot: hidden from people, filled by bots. */}
            <input name="company_website" tabIndex={-1} autoComplete="off" className="hp" aria-hidden="true" />
            {status.kind === "error" && (
              <p className="error" role="alert">
                {status.message}
              </p>
            )}
            <button className="btn btn-primary" disabled={status.kind === "sending"}>
              {status.kind === "sending" ? "Sending…" : "Request my estimate"}
            </button>
          </form>
        )}
      </div>
    </section>
  );
}
