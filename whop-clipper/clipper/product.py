"""Pull real product facts and visuals from the link a UGC campaign promotes.

App Store / Play Store / ordinary product pages expose a name, a description and images
(screenshots, icon, og:image). Using the product's own copy and imagery keeps UGC videos
accurate - nothing is invented - and gives them real visuals instead of a blank screen.
"""
from __future__ import annotations

import html
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

import requests

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/605.1.15 "
      "(KHTML, like Gecko) Version/17.0 Safari/605.1.15")


@dataclass
class Product:
    name: str = ""
    description: str = ""
    image_urls: list[str] = field(default_factory=list)
    images: list[Path] = field(default_factory=list)

    def facts(self, limit: int = 6) -> list[str]:
        """Short factual lines from the product's own description (bullets first, then sentences)."""
        text = self.description
        lines = [ln for ln in text.splitlines() if not re.fullmatch(r"[A-Z0-9 '&!?:-]{4,}", ln.strip())]
        bullets = [re.sub(r"^[\s•\-*–]+", "", ln).strip() for ln in lines if re.match(r"^\s*[•\-*–]", ln)]
        text = "\n".join(ln for ln in lines if not re.match(r"^\s*[•\-*–]", ln))
        sentences = re.split(r"(?<=[.!?])\s+", re.sub(r"\s+", " ", text))
        out = []
        for s in bullets + sentences:
            s = re.sub(r"\s+", " ", s).strip(" .")
            if 4 <= len(s.split()) <= 18 and s not in out and not re.search(r"https?://|©|terms|privacy", s, re.I):
                out.append(s)
        return out[:limit]


def _meta(h: str, prop: str) -> str:
    m = re.search(rf'<meta[^>]+(?:property|name)="{re.escape(prop)}"[^>]+content="([^"]*)"', h)
    return html.unescape(m.group(1)) if m else ""


def fetch(url: str, dest: Path | None = None, max_images: int = 6) -> Product:
    p = Product()
    try:
        r = requests.get(url, headers={"User-Agent": UA, "Accept-Language": "en"}, timeout=30)
        r.raise_for_status()
        h = r.content.decode(r.encoding if r.encoding and "charset" in r.headers.get("content-type", "") else "utf-8",
                             errors="replace")
    except requests.RequestException:
        return p
    for m in re.finditer(r'<script[^>]+application/ld\+json[^>]*>(.*?)</script>', h, re.S):
        try:
            d = json.loads(m.group(1))
        except ValueError:
            continue
        for item in d if isinstance(d, list) else [d]:
            if isinstance(item, dict) and item.get("description") and item.get("@type") not in ("Organization",):
                p.name = p.name or item.get("name", "")
                p.description = p.description or item["description"]
    p.name = p.name or _meta(h, "og:title")
    p.description = p.description or _meta(h, "og:description") or _meta(h, "description")
    p.name = re.sub(r"\s*[-|]\s*(App Store|Apps on Google Play).*$", "", p.name).strip()

    # App Store screenshots: ".../1.png" style bases that serve any size as <base>/<w>x<h>bb.jpg
    shots = list(dict.fromkeys(re.findall(
        r'(https://is\d-ssl\.mzstatic\.com/image/thumb/[^"\s,]+?/\d+\.(?:png|jpg|jpeg))', h)))
    p.image_urls += [f"{u}/886x1920bb.jpg" for u in shots]
    # Google Play screenshots
    p.image_urls += [u + "=w720-h1600" for u in dict.fromkeys(
        re.findall(r'(https://play-lh\.googleusercontent\.com/[A-Za-z0-9_\-]{40,})', h))][:max_images]
    og = _meta(h, "og:image")
    if og:
        p.image_urls.append(og)
    p.image_urls = list(dict.fromkeys(p.image_urls))[:max_images]

    if dest is not None:
        dest.mkdir(parents=True, exist_ok=True)
        for i, u in enumerate(p.image_urls):
            try:
                img = requests.get(u, headers={"User-Agent": UA}, timeout=30)
                if img.ok and img.headers.get("content-type", "").startswith("image/") and len(img.content) > 5000:
                    f = dest / f"img{i:02d}.jpg"
                    f.write_bytes(img.content)
                    p.images.append(f)
            except requests.RequestException:
                continue
    return p
