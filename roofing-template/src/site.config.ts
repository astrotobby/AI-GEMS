/**
 * Everything company-specific lives in this one file.
 * To re-skin the template for a new roofer: edit this file (name, phone,
 * services, prices, plan IDs, colors). No component needs to change.
 */

export type PlanKey = "inspection" | "deposit" | "flatRate";

export interface Service {
  id: string;
  title: string;
  blurb: string;
  bullets: string[];
  /** Optional plan this service can be booked with directly. */
  plan?: PlanKey;
}

export interface PricedPlan {
  key: PlanKey;
  /** Whop variant ID (prefixed `plan_`). Run `npm run setup:plans` to create them. */
  planId: string;
  /** Whop product ID (prefixed `prod_`), used for pixel attribution. */
  productId: string;
  name: string;
  price: number;
  priceNote: string;
  description: string;
  features: string[];
  cta: string;
}

export const site = {
  /** Your Whop business ID (prefixed `biz_`). Filled in by `npm run setup:plans`. */
  whopAccountId: "biz_REPLACE",

  company: {
    name: "Summit Ridge Roofing",
    tagline: "Roofs built for the next 30 years.",
    phone: "(555) 010-4477",
    phoneHref: "tel:+15550104477",
    email: "office@example.com",
    license: "Licensed & insured · Lic. #RC-000000",
    founded: 2009,
    city: "Austin, TX",
  },

  theme: {
    /** Primary brand color — also passed to Whop Elements as the accent. */
    primary: "#c2410c",
    primaryDark: "#9a3412",
    ink: "#0f172a",
    elementsAccent: "orange" as const,
  },

  hero: {
    eyebrow: "Free estimates · Same-week inspections",
    headline: "Storm damage? Leaks? Aging shingles? We fix roofs right.",
    sub: "Repairs, full replacements and certified inspections across the metro. Book online, pay a deposit to lock your crew date, and get one clear final invoice.",
    stats: [
      { value: "2,400+", label: "roofs completed" },
      { value: "4.9★", label: "average rating" },
      { value: "25-yr", label: "workmanship warranty" },
    ],
  },

  services: [
    {
      id: "repair",
      title: "Roof Repair",
      blurb: "Leaks, missing shingles, flashing, storm and hail damage — diagnosed and fixed fast.",
      bullets: ["Emergency tarping available", "Photo report of every fix", "Insurance claim documentation"],
      plan: "flatRate",
    },
    {
      id: "replacement",
      title: "Roof Replacement",
      blurb: "Full tear-off and re-roof in asphalt, metal or tile, usually completed in 1–2 days.",
      bullets: ["Manufacturer-certified installers", "Full deck inspection", "Magnetic nail sweep & cleanup"],
      plan: "deposit",
    },
    {
      id: "inspection",
      title: "Roof Inspection",
      blurb: "A 21-point inspection with drone photos and a written condition report.",
      bullets: ["Real-estate & insurance ready", "Drone + attic inspection", "Fee credited toward any job"],
      plan: "inspection",
    },
  ] satisfies Service[],

  plans: [
    {
      key: "inspection",
      planId: "plan_REPLACE_INSPECTION",
      productId: "prod_REPLACE",
      name: "Certified Inspection",
      price: 149,
      priceNote: "one-time",
      description: "21-point inspection with drone photos and a written report within 48 hours.",
      features: ["Drone & attic inspection", "Written condition report", "Credited toward any repair or replacement"],
      cta: "Book inspection",
    },
    {
      key: "deposit",
      planId: "plan_REPLACE_DEPOSIT",
      productId: "prod_REPLACE",
      name: "Project Deposit",
      price: 500,
      priceNote: "applied to your project",
      description: "Locks your crew date and materials order. Your card is saved securely so the final balance can be billed on completion.",
      features: ["Reserves your install date", "Materials ordered same week", "Final balance invoiced after the job"],
      cta: "Pay deposit",
    },
    {
      key: "flatRate",
      planId: "plan_REPLACE_FLATRATE",
      productId: "prod_REPLACE",
      name: "Leak Repair (Flat Rate)",
      price: 395,
      priceNote: "flat rate",
      description: "Fix one leak source — up to 10 shingles, flashing or a pipe boot — with a 2-year warranty.",
      features: ["No surprise pricing", "Most repairs done in one visit", "2-year leak-free warranty"],
      cta: "Book repair",
    },
  ] satisfies PricedPlan[],

  serviceArea: {
    summary: "Serving the greater metro and surrounding counties within 50 miles.",
    areas: ["Austin", "Round Rock", "Cedar Park", "Georgetown", "Pflugerville", "Leander", "Buda", "Kyle", "San Marcos", "Lakeway"],
  },

  financing: {
    headline: "Pay over time",
    body: "Approved homeowners can split a project into monthly payments at checkout. Eligible buy-now-pay-later options (such as Klarna, Afterpay or Splitit) appear automatically when available — no separate application.",
    note: "Financing options shown at checkout depend on eligibility and the merchant's approved payment methods.",
  },

  reviews: [
    { name: "Dana R.", area: "Round Rock", rating: 5, text: "Hail took out half our shingles. They handled the inspection, the insurance photos and the replacement in under two weeks." },
    { name: "Marcus T.", area: "Cedar Park", rating: 5, text: "Clear flat-rate price for the leak, fixed in one visit, and the crew cleaned up better than they found it." },
    { name: "Priya S.", area: "Austin", rating: 5, text: "Paid the deposit online, got a crew date the same day, and the final invoice matched the quote to the dollar." },
  ],

  trust: ["GAF-style certified installers", "BBB A+ rated", "25-year workmanship warranty", "Fully licensed & insured"],

  estimateServices: ["Roof repair", "Full replacement", "Inspection", "Storm / insurance claim", "Gutters", "Other"],
};

export const planByKey = (key: PlanKey): PricedPlan => {
  const plan = site.plans.find((p) => p.key === key);
  if (!plan) throw new Error(`Unknown plan ${key}`);
  return plan;
};

export const plansConfigured =
  !site.whopAccountId.includes("REPLACE") && site.plans.every((p) => !p.planId.includes("REPLACE"));
