import { site } from "../site.config";

export function Financing() {
  const f = site.financing;
  return (
    <section className="section" id="financing">
      <div className="container financing">
        <div>
          <p className="eyebrow">Financing</p>
          <h2>{f.headline}</h2>
          <p className="lead">{f.body}</p>
          <p className="muted small">{f.note}</p>
        </div>
        <div className="finance-card" aria-hidden="true">
          <p className="muted small">Example: $9,600 replacement</p>
          <p className="price">$400<span>/mo</span></p>
          <p className="muted small">24 monthly payments, subject to approval at checkout</p>
        </div>
      </div>
    </section>
  );
}
