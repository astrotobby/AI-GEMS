import { site } from "../site.config";

export function Hero() {
  const { hero, company, trust } = site;
  return (
    <section className="hero" id="top">
      <div className="container hero-grid">
        <div>
          <p className="eyebrow">{hero.eyebrow}</p>
          <h1>{hero.headline}</h1>
          <p className="lead">{hero.sub}</p>
          <div className="hero-actions">
            <a className="btn btn-primary" href="#estimate">Request an estimate</a>
            <a className="btn btn-ghost" href="#pricing">See pricing</a>
          </div>
          <ul className="trust-row">
            {trust.map((t) => (
              <li key={t}>✓ {t}</li>
            ))}
          </ul>
        </div>
        <aside className="hero-card" aria-label="At a glance">
          <p className="hero-card-title">{company.tagline}</p>
          <div className="stats">
            {hero.stats.map((s) => (
              <div key={s.label}>
                <strong>{s.value}</strong>
                <span>{s.label}</span>
              </div>
            ))}
          </div>
          <p className="muted small">{company.license} · Serving {company.city} since {company.founded}</p>
        </aside>
      </div>
    </section>
  );
}
