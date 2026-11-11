# Roofing Website Template — built on Whop Websites

**Live demo:** https://chainztobby.whop.site (app `app_eHEE702xOFUnjC`) · office invoicing page: https://chainztobby.whop.site/#admin

A production-ready, reusable website template for a US roofing company, hosted on **Whop Websites** (`*.whop.site`). It demos Whop end-to-end for a high-ticket service business: **deposit → job → final invoice**.

- **Sections:** services (repair, replacement, inspection), pricing, deposit booking, financing callout, reviews & trust badges, service area, estimate/contact form.
- **Payments:** Whop Payment Elements only. No Stripe, PayPal SDK or custom card forms.
- **Re-skinnable:** every company-specific value (name, phone, services, prices, plan IDs, colors) lives in [`src/site.config.ts`](src/site.config.ts). Swapping in a new company needs no structural change.

## Whop features implemented

| Requirement | Where |
|---|---|
| Hosted on Whop Websites, deployed with the Whop CLI | `vite.config.ts`, `vite.server.config.ts` (`whop()` plugin packs `dist/client` + `dist/server/index.js`) |
| 3 real plans: $149 inspection, $500 project deposit, $395 flat-rate repair | `scripts/create-plans.mjs` → Whop Products + Variants API |
| `CheckoutElement` (cards, eligible BNPL/financing, promo codes) | `src/components/CheckoutModal.tsx` |
| `ExpressCheckoutElement` (Apple Pay / Google Pay) | `src/components/CheckoutModal.tsx` (its own `<Checkout>` handle, as the docs require) |
| `PaymentElement` + `EmailElement` + required `BrandingElement` | `src/components/DepositFlow.tsx` |
| Card on file via a **setup intent** at deposit checkout | Payment Elements in `mode: "setup"` → `POST /setup_intents` → `handleNextAction` (3DS) → poll until `succeeded` |
| Deposit charged to the saved card | `POST /payments` with `member_id` + `payment_method_id` |
| Final balance billed with the **Invoices API** | `POST /invoices` → `/issue` (auto-charge the stored card) or `/send` (email a pay link). UI at `/#admin` |
| "Request an Estimate" form → **Whop Leads API** | The form fires a pixel `lead` event with name, email and phone (customer matching). Then `POST /api/leads` resolves the email to a Whop user (`GET /people/{email}`, then `GET /members?query=`) and calls `POST /leads` with `user_id` and the form fields as metadata. The Leads API requires a Whop user, so an email with no Whop account is kept as the pixel lead event instead. |
| Custom pixel events with `whop.track` | `estimate_requested`, `service_viewed`, `deposit_started` (plus standard `lead`, `initiate_checkout`). Page views and checkout events are automatic on whop.site |
| Product attribution attributes | `data-whop-product-id` / `variant-id` / `price` / `currency` on pricing cards and the checkout modal |
| Responsive | Mobile-first CSS, tested at 390px and 1280px, no horizontal scroll |

The server code never holds an API key: Whop hosting attaches the app's key to server-side calls to the Whop API.

## Deploy (about 10 minutes)

Requirements: Node 22+, a Whop business with payments enabled, and the Whop CLI (`npm i -g @whop/cli`, then run `whop` once to sign in and pick the business).

```bash
cd roofing-template
npm install

# 1. Register a website app with a whop.site domain (first time only).
#    The code is already here, so create the app instead of scaffolding.
#    See `whop apps create --help` for the exact flags; it prints an app_xxx id.
whop apps create --help

# 2. Create the 3 plans and write their IDs into src/site.config.ts
#    (WHOP_API_KEY: a business API key from Dashboard → Developer)
WHOP_API_KEY=... WHOP_ACCOUNT_ID=biz_... npm run setup:plans

# 3. Secret for the office invoicing page (/#admin)
whop apps secrets set --secret ADMIN_TOKEN=pick-a-long-random-string

# 4. Ship it
whop apps deploy --app app_xxx
```

`GET /api/health` on the live site reports whether the account, plans and admin token are configured.

**Apple Pay / Google Pay:** the express buttons appear only on a domain registered as a payment method domain (`POST /payment_method_domains` with your `*.whop.site` host), and only on a device that has a wallet set up.

## Test checklist (for the 3–5 minute demo recording)

Open the browser DevTools console. Every `whop.track` call is also logged as `[whop.track] …`.

1. **Live site.** Scroll the whole page on desktop, then in phone view. The `service_viewed` events fire as the service cards scroll into view.
2. **Estimate → lead.** Submit the form. You should see a success message with a `lead_…` reference and the `estimate_requested` event. Then show the lead in **Dashboard → Leads**.
3. **Checkout.** Click **Book inspection**. The modal shows the Express buttons and the full CheckoutElement with Whop branding. Complete a test payment and show the success message and the payment in the dashboard.
4. **Deposit + card on file.** In the deposit section, enter a job address, an email and a card. The `deposit_started` event fires. Click **Save card & pay deposit**. You should see the setup intent (`sint_…`) and the deposit payment reference.
5. **Final invoice.** Open `/#admin`, paste the admin token (the `sint_…` reference is prefilled), enter a balance, and choose **Charge card on file** or **Email invoice**. Show the JSON result and the invoice in the dashboard.
6. **Pixel events.** Show the events in the Whop pixel dashboard.

## Re-skinning for another roofer

1. Edit `src/site.config.ts`: company, theme colors, hero, services, plans (names and prices), service area, reviews.
2. Reset the three `planId`s to `plan_REPLACE_*`, then re-run `npm run setup:plans` against the new business.
3. Run `whop apps deploy`.

## Project layout

```
src/
  site.config.ts        all company content, prices and plan IDs
  App.tsx               page composition + <WhopElements> provider
  components/           Header, Hero, Services, Pricing, DepositFlow, CheckoutModal,
                        Financing, Reviews, ServiceArea, EstimateForm, AdminInvoice, Footer
  lib/track.ts          whop.track wrapper + service_viewed observer
  server/index.ts       API routes (leads, setup intents, deposit charge, final invoice)
scripts/create-plans.mjs  one-time product + plan setup
```
