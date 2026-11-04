import { site } from "../site.config";

export function Footer() {
  const c = site.company;
  return (
    <footer className="footer">
      <div className="container footer-inner">
        <div>
          <strong>{c.name}</strong>
          <p className="small">{c.license}</p>
        </div>
        <div className="small">
          <a href={c.phoneHref}>{c.phone}</a> · <a href={`mailto:${c.email}`}>{c.email}</a>
        </div>
        <p className="small muted">Payments processed securely by Whop. © {new Date().getFullYear()} {c.name}</p>
      </div>
    </footer>
  );
}
