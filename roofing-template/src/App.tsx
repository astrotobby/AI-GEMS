import { useCallback, useEffect, useMemo, useState } from "react";
import { WhopElements } from "@whop/elements-react";
import { loadWhop } from "@whop/elements";
import { site, type PlanKey } from "./site.config";
import { Header } from "./components/Header";
import { Hero } from "./components/Hero";
import { Services } from "./components/Services";
import { Pricing } from "./components/Pricing";
import { Financing } from "./components/Financing";
import { ServiceArea } from "./components/ServiceArea";
import { Reviews } from "./components/Reviews";
import { EstimateForm } from "./components/EstimateForm";
import { DepositFlow } from "./components/DepositFlow";
import { CheckoutModal } from "./components/CheckoutModal";
import { AdminInvoice } from "./components/AdminInvoice";
import { Footer } from "./components/Footer";

export function App() {
  // One shared SDK load for every Whop element on the page.
  const elements = useMemo(() => loadWhop(), []);
  const [checkoutPlan, setCheckoutPlan] = useState<Exclude<PlanKey, "deposit"> | null>(null);
  const [isAdmin, setIsAdmin] = useState(() => location.hash === "#admin");
  // Shown whenever a demo plan is configured; set site.demoPlan to null for a real client.
  const showDemo = Boolean(site.demoPlan);

  useEffect(() => {
    const onHash = () => setIsAdmin(location.hash === "#admin");
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  const closeCheckout = useCallback(() => setCheckoutPlan(null), []);

  const book = (key: PlanKey) => {
    if (key === "deposit") {
      document.getElementById("deposit")?.scrollIntoView({ behavior: "smooth" });
      return;
    }
    setCheckoutPlan(key);
  };

  return (
    <WhopElements
      elements={elements}
      appearance={{ theme: { appearance: "light", accentColor: site.theme.elementsAccent } }}
      onLoadError={(err) => console.error("Whop Elements failed to load", err)}
    >
      <div
        style={
          {
            "--brand": site.theme.primary,
            "--brand-dark": site.theme.primaryDark,
            "--ink": site.theme.ink,
          } as React.CSSProperties
        }
      >
        <Header />
        {isAdmin ? (
          <main>
            <AdminInvoice />
          </main>
        ) : (
          <main>
            {showDemo && site.demoPlan && (
              <div className="demo-bar">
                <span>Demo mode: run a real ${site.demoPlan.price} checkout through the same Whop Payment Elements.</span>
                <button className="btn btn-primary btn-sm" onClick={() => setCheckoutPlan("demo")}>
                  {site.demoPlan.cta}
                </button>
              </div>
            )}
            <Hero />
            <Services onBook={book} />
            <Pricing onBook={book} />
            <DepositFlow />
            <Financing />
            <Reviews />
            <ServiceArea />
            <EstimateForm />
          </main>
        )}
        <Footer />
        {checkoutPlan && <CheckoutModal planKey={checkoutPlan} onClose={closeCheckout} />}
      </div>
    </WhopElements>
  );
}
