import { useEffect, useState } from "react";
import { Checkout, CheckoutElement, ExpressCheckoutElement, type CheckoutPaymentCompletion, type CheckoutWaitlistCompletion, type CheckoutSetupCompletion } from "@whop/elements-react";
import { planByKey, plansConfigured, type PlanKey } from "../site.config";
import { track } from "../lib/track";

/**
 * Hosted Whop checkout for one-time plans (inspection fee, flat-rate repair).
 *
 * ExpressCheckoutElement and CheckoutElement share one slot per Checkout
 * handle, so each gets its own <Checkout> for the same plan: the wallet
 * buttons on top, the full checkout (cards, BNPL/financing, promo codes and
 * Whop's merchant-of-record branding) below.
 */
export function CheckoutModal({ planKey, onClose }: { planKey: Exclude<PlanKey, "deposit">; onClose: () => void }) {
  const plan = planByKey(planKey);
  const [paid, setPaid] = useState<string | null>(null);
  const returnUrl = `${location.origin}/?checkout=return&plan=${plan.key}`;

  useEffect(() => {
    track("initiate_checkout", { variant_id: plan.planId, product_id: plan.productId, value: plan.price, currency: "USD" });
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    document.body.style.overflow = "hidden";
    return () => {
      window.removeEventListener("keydown", onKey);
      document.body.style.overflow = "";
    };
  }, [plan, onClose]);

  const onComplete = (payload: CheckoutPaymentCompletion | CheckoutWaitlistCompletion | CheckoutSetupCompletion) => {
    setPaid("paymentId" in payload ? payload.paymentId : payload.sessionId);
  };

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div
        className="modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="checkout-title"
        onClick={(e) => e.stopPropagation()}
        data-whop-product-id={plan.productId}
        data-whop-variant-id={plan.planId}
        data-whop-price={plan.price}
        data-whop-currency="USD"
      >
        <button className="modal-close" onClick={onClose} aria-label="Close checkout">
          ×
        </button>
        <h2 id="checkout-title">
          {plan.name} · ${plan.price}
        </h2>
        <p className="muted">{plan.description}</p>

        {!plansConfigured ? (
          <p className="error">Plans are not configured yet. Run <code>npm run setup:plans</code> and redeploy.</p>
        ) : paid ? (
          <div className="success" role="status">
            <h3>You're booked!</h3>
            <p>Payment received. We'll call within one business day to confirm your appointment.</p>
            <p className="muted small">Reference: {paid}</p>
          </div>
        ) : (
          <>
            <div className="express-slot">
              <Checkout plan={plan.planId} returnUrl={returnUrl} onComplete={onComplete}>
                <ExpressCheckoutElement />
              </Checkout>
            </div>
            <Checkout plan={plan.planId} returnUrl={returnUrl} onComplete={onComplete} metadata={{ source: "roofing_site", plan: plan.key }}>
              <CheckoutElement />
            </Checkout>
          </>
        )}
      </div>
    </div>
  );
}
