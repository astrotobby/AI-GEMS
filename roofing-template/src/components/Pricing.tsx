import { site, type PlanKey } from "../site.config";

export function Pricing({ onBook }: { onBook: (key: PlanKey) => void }) {
  return (
    <section className="section alt" id="pricing">
      <div className="container">
        <h2>Simple, upfront pricing</h2>
        <p className="section-sub">Pay securely online. Apple Pay, Google Pay and eligible pay-over-time options appear at checkout.</p>
        <div className="grid-3">
          {site.plans.map((p) => (
            <article
              key={p.key}
              className={`card price-card${p.key === "deposit" ? " featured" : ""}`}
              data-whop-product-id={p.productId}
              data-whop-variant-id={p.planId}
              data-whop-price={p.price}
              data-whop-currency="USD"
            >
              {p.key === "deposit" && <span className="badge">Replacement projects</span>}
              <h3>{p.name}</h3>
              <p className="price">
                ${p.price.toLocaleString()} <span>{p.priceNote}</span>
              </p>
              <p>{p.description}</p>
              <ul className="checks">
                {p.features.map((f) => (
                  <li key={f}>{f}</li>
                ))}
              </ul>
              <button className="btn btn-primary" onClick={() => onBook(p.key)}>
                {p.cta}
              </button>
            </article>
          ))}
        </div>
      </div>
    </section>
  );
}
