import { site } from "../site.config";

export function Header() {
  const { company } = site;
  return (
    <header className="header">
      <div className="container header-inner">
        <a href="#top" className="brand" aria-label={`${company.name} home`}>
          <img src="/favicon.svg" alt="" width={32} height={32} />
          <span>{company.name}</span>
        </a>
        <nav className="nav" aria-label="Main">
          <a href="#services">Services</a>
          <a href="#pricing">Pricing</a>
          <a href="#financing">Financing</a>
          <a href="#area">Service area</a>
        </nav>
        <div className="header-cta">
          <a className="phone" href={company.phoneHref}>{company.phone}</a>
          <a className="btn btn-primary btn-sm" href="#estimate">Free estimate</a>
        </div>
      </div>
    </header>
  );
}
