import { site } from "../site.config";

export function Reviews() {
  return (
    <section className="section alt" id="reviews">
      <div className="container">
        <h2>Homeowners trust us</h2>
        <div className="grid-3">
          {site.reviews.map((r) => (
            <figure key={r.name} className="card review">
              <div className="stars" aria-label={`${r.rating} out of 5 stars`}>{"★".repeat(r.rating)}</div>
              <blockquote>“{r.text}”</blockquote>
              <figcaption>
                <strong>{r.name}</strong> · {r.area}
              </figcaption>
            </figure>
          ))}
        </div>
        <ul className="trust-badges">
          {site.trust.map((t) => (
            <li key={t}>{t}</li>
          ))}
        </ul>
      </div>
    </section>
  );
}
