// TASK 4 — Cross-network ad rotation (geo-aware, A/B rotating, with fallback).
import { CONFIG, AD_NETWORKS, AD_REGION_MAP } from "../../config.js";

// Choose a network for this visitor. Rotates every CONFIG.AD_ROTATE_EVERY views.
// viewCount comes from a per-visitor counter (see index.js).
export function pickNetwork(region, viewCount) {
  const eligible = AD_REGION_MAP[region] || AD_REGION_MAP.OTHER;
  if (eligible.length === 1) return eligible[0];
  // rotate deterministically by view bucket so each visitor cycles networks
  const bucket = Math.floor(viewCount / CONFIG.AD_ROTATE_EVERY);
  return eligible[bucket % eligible.length];
}

// Render the ad slot. Includes a client-side fallback to the region's primary
// network if the chosen network's container stays empty (failed to fill).
export function renderAdSlot(region, network) {
  const eligible = AD_REGION_MAP[region] || AD_REGION_MAP.OTHER;
  const fallback = eligible[0];
  const net = AD_NETWORKS[network] || AD_NETWORKS[fallback];
  const fb = AD_NETWORKS[fallback];

  return `
<div class="mz-ad-slot" data-network="${network}">
  <div class="mz-ad-inner" id="mz-ad-inner">${net.html}</div>
</div>
<script>
(function(){
  // Beacon which network was served (impression) for A/B tracking.
  try{ navigator.sendBeacon('/mz/track', JSON.stringify({type:'ad_impression', network:${JSON.stringify(network)}})); }catch(e){}
  // Fallback: if the slot rendered nothing after 2.5s, load the region primary.
  setTimeout(function(){
    var inner=document.getElementById('mz-ad-inner');
    if(inner && inner.offsetHeight < 10 && ${JSON.stringify(network)} !== ${JSON.stringify(fallback)}){
      inner.innerHTML=${JSON.stringify(fb.html)};
      try{ navigator.sendBeacon('/mz/track', JSON.stringify({type:'ad_fallback', network:${JSON.stringify(fallback)}})); }catch(e){}
    }
  }, 2500);
})();
</script>
<style>.mz-ad-slot{margin:20px auto;text-align:center;min-height:90px}</style>`;
}
