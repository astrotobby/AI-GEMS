import { useEffect, useRef } from "react";
import { site, planByKey, type PlanKey } from "../site.config";
import { observeServiceViews } from "../lib/track";

export function Services({ onBook }: { onBook: (key: PlanKey) => void }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => observeServiceViews(ref.current), []);

  return (
    <section className="section" id="services">
      <div className="container">
        <h2>Roofing services</h2>
        <p className="section-sub">Every job starts with an honest inspection and a written, line-item quote.</p>
        <div className="grid-3" ref={ref}>
          {site.services.map((s) => {
            const plan = s.plan ? planByKey(s.plan) : null;
            return (
              <article key={s.id} className="card service" data-service-id={s.id}>
                <h3>{s.title}</h3>
                <p>{s.blurb}</p>
                <ul className="checks">
                  {s.bullets.map((b) => (
                    <li key={b}>{b}</li>
                  ))}
                </ul>
                {plan && (
                  <button className="btn btn-outline" onClick={() => onBook(plan.key)}>
                    {plan.cta} · ${plan.price}
                  </button>
                )}
              </article>
            );
          })}
        </div>
      </div>
    </section>
  );
}
