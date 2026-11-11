/**
 * Thin wrapper around the Whop pixel. On whop.site the pixel is injected
 * automatically (window.whop exists before app code runs). Elsewhere —
 * local dev, previews — calls are queued to the console so nothing breaks.
 */
type TrackProps = Record<string, string | number | undefined>;

declare global {
  interface Window {
    whop?: { track: (event: string, props?: TrackProps) => void };
  }
}

export type CustomEvent = "estimate_requested" | "service_viewed" | "deposit_started";

export function track(event: CustomEvent | string, props?: TrackProps) {
  const clean = props
    ? Object.fromEntries(Object.entries(props).filter(([, v]) => v !== undefined))
    : undefined;
  try {
    if (typeof window !== "undefined" && window.whop?.track) {
      window.whop.track(event, clean as TrackProps);
    }
  } catch (err) {
    console.warn("whop.track failed", err);
  }
  // Visible in the browser console for the demo recording.
  console.info("[whop.track]", event, clean ?? {});
}

/** Fire `service_viewed` once per service when its card scrolls into view. */
export function observeServiceViews(root: HTMLElement | null) {
  if (!root || typeof IntersectionObserver === "undefined") return () => {};
  const seen = new Set<string>();
  const io = new IntersectionObserver(
    (entries) => {
      for (const e of entries) {
        const id = (e.target as HTMLElement).dataset.serviceId;
        if (e.isIntersecting && id && !seen.has(id)) {
          seen.add(id);
          track("service_viewed", { service: id, event_id: `service_viewed_${id}_${sessionId()}` });
        }
      }
    },
    { threshold: 0.6 },
  );
  root.querySelectorAll<HTMLElement>("[data-service-id]").forEach((el) => io.observe(el));
  return () => io.disconnect();
}

let sid: string | null = null;
export function sessionId() {
  if (!sid) sid = Math.random().toString(36).slice(2, 10);
  return sid;
}
