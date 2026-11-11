import { site } from "../site.config";

export function ServiceArea() {
  const a = site.serviceArea;
  return (
    <section className="section" id="area">
      <div className="container">
        <h2>Service area</h2>
        <p className="section-sub">{a.summary}</p>
        <ul className="chips">
          {a.areas.map((x) => (
            <li key={x}>{x}</li>
          ))}
        </ul>
      </div>
    </section>
  );
}
