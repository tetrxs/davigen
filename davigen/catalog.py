"""Camera catalog with photos: Wikidata (camera models, drones) + Wikimedia Commons thumbnails.

Fetched once and stored in data/catalog/ (portable with the davigen folder), refreshed about once
a year. Wikidata knows models and manufacturers, not log profiles – those come from the brand
(profiles.toml) or, for cameras davigen knows in detail, from cameras.toml.
"""

from __future__ import annotations

import json
import re
import time
import urllib.parse
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path

from .config import DATA_DIR, Config
from .providers import tokens

CATALOG_DIR = DATA_DIR / "catalog"
CATALOG_FILE = CATALOG_DIR / "cameras.json"
THUMBS = CATALOG_DIR / "thumbs"
SPARQL = "https://query.wikidata.org/sparql"
COMMONS_API = "https://commons.wikimedia.org/w/api.php"
USER_AGENT = "davigen-travel-creator/1.0 (personal non-commercial camera catalog)"
THUMB_WIDTH = 250          # a standard Wikimedia thumbnail step (cached, not rendered on demand)
REFRESH_DAYS = 365
MIN_YEAR = 2014
DRONE_BRANDS = ("dji", "autel", "parrot", "skydio", "insta360", "potensic", "hubsan", "holy stone", "xiaomi",
                "zero zero", "hover", "yuneec", "powervision", "snaptain")

# one small query per class – the public endpoint is rate-limited and times out on big UNIONs
QUERY = """
SELECT ?item ?label ?maker ?img ?date (GROUP_CONCAT(DISTINCT ?alt; separator="|") AS ?alts) WHERE {
  ?item wdt:P31 %(cls)s ; wdt:P176 ?m .
  %(date_clause)s
  ?item rdfs:label ?label . FILTER(LANG(?label) = "en")
  ?m rdfs:label ?maker . FILTER(LANG(?maker) = "en")
  OPTIONAL { ?item wdt:P18 ?img . }
  OPTIONAL { ?item skos:altLabel ?alt . FILTER(LANG(?alt) = "en" || LANG(?alt) = "de") }
}
GROUP BY ?item ?label ?maker ?img ?date
"""
OPTIONAL_DATE = "OPTIONAL { ?item wdt:P571 ?date . } FILTER(!BOUND(?date) || YEAR(?date) >= %d)"
REQUIRED_DATE = "?item wdt:P571 ?date . FILTER(YEAR(?date) >= %d)"
PARTS = [  # (class, kind, date clause)
    ("wd:Q20741022", "camera", OPTIONAL_DATE),     # digital camera model
    ("wd:Q20888659", "camera", REQUIRED_DATE),     # camera model (incl. action/360 cams; film cams have old dates)
    ("wd:Q484000", "drone", OPTIONAL_DATE),        # unmanned aerial vehicle
    ("wd:Q43965", "drone", OPTIONAL_DATE),         # quadcopter
]
PAUSE = 61                                         # WDQS currently allows ~1 query/minute


def _get(url: str, timeout: int = 60, accept: str = "*/*", retries: int = 4) -> bytes:
    """GET with Wikimedia etiquette: descriptive User-Agent, honour 429/503 Retry-After."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": accept})
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 - fixed Wikimedia URLs
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code not in (429, 502, 503, 504) or attempt == retries:
                raise
            wait = e.headers.get("Retry-After", "")
            time.sleep(min(int(wait) if wait.isdigit() else 30 * (attempt + 1), 120))
    raise RuntimeError("unreachable")


def fetch_entries(progress=None) -> list[dict]:
    entries: dict[str, dict] = {}
    for i, (cls, kind, date_clause) in enumerate(PARTS):
        if i:
            time.sleep(PAUSE)
        if progress:
            progress(i, len(PARTS), "Wikidata-Abfrage")
        query = QUERY % {"cls": cls, "date_clause": date_clause % MIN_YEAR}
        url = SPARQL + "?" + urllib.parse.urlencode({"query": query, "format": "json"})
        data = json.loads(_get(url, timeout=120, accept="application/sparql-results+json"))
        for b in data["results"]["bindings"]:
            qid = b["item"]["value"].rsplit("/", 1)[-1]
            maker = b["maker"]["value"]
            if kind == "drone" and not any(w in maker.lower() for w in DRONE_BRANDS):
                continue
            e = entries.setdefault(qid, {"qid": qid, "name": b["label"]["value"], "maker": maker, "kind": kind,
                                         "image": "", "year": "", "aliases": []})
            if "img" in b and not e["image"]:
                e["image"] = urllib.parse.unquote(b["img"]["value"].rsplit("/", 1)[-1])
            if "date" in b and not e["year"]:
                e["year"] = b["date"]["value"][:4]
            e["aliases"] = sorted(set(e["aliases"]) | {a for a in b.get("alts", {}).get("value", "").split("|") if a})
    return sorted(entries.values(), key=lambda e: (e["maker"].lower(), e["name"].lower()))


def _thumb_name(entry: dict) -> str:
    return f"{entry['qid']}.jpg"


def _thumb_urls(entries: list[dict]) -> dict[str, str]:
    """{file name: thumbnail URL} via the Commons API, 50 files per request (Wikimedia's recommended way)."""
    urls: dict[str, str] = {}
    names = sorted({e["image"] for e in entries if e["image"]})
    for i in range(0, len(names), 50):
        chunk = names[i:i + 50]
        params = {"action": "query", "format": "json", "prop": "imageinfo", "iiprop": "url",
                  "iiurlwidth": THUMB_WIDTH, "titles": "|".join("File:" + n for n in chunk)}
        data = json.loads(_get(COMMONS_API + "?" + urllib.parse.urlencode(params), timeout=60))
        norm = {n["to"]: n["from"] for n in data.get("query", {}).get("normalized", [])}
        for page in data.get("query", {}).get("pages", {}).values():
            info = (page.get("imageinfo") or [{}])[0]
            title = norm.get(page.get("title", ""), page.get("title", ""))
            if info.get("thumburl"):
                urls[title.removeprefix("File:")] = info["thumburl"]
        time.sleep(0.5)
    return urls


def _download_thumb(entry: dict, url: str) -> bool:
    target = THUMBS / _thumb_name(entry)
    if target.exists():
        return True
    try:
        target.write_bytes(_get(url, timeout=30, retries=6))
        time.sleep(0.3)
        return True
    except Exception:  # noqa: BLE001 - a missing photo is fine, the card shows initials
        return False


def refresh(progress=None) -> dict:
    """Fetch the catalog from Wikidata, then all missing thumbnails. Returns the stored catalog."""
    entries = fetch_entries(progress)
    return _save(entries, progress)


def fill_photos(progress=None) -> dict:
    """Download photos that failed earlier (Wikimedia rate limits) without re-querying Wikidata."""
    return _save(load()["cameras"], progress, updated=load().get("updated"))


def _save(entries: list[dict], progress=None, updated: str | None = None) -> dict:
    THUMBS.mkdir(parents=True, exist_ok=True)
    todo = [e for e in entries if e["image"] and not (THUMBS / _thumb_name(e)).exists()]
    if progress:
        progress(0, len(todo), "Foto-Adressen")
    urls = _thumb_urls(todo)
    todo = [(e, urls[e["image"]]) for e in todo if e["image"] in urls]
    done = 0
    with ThreadPoolExecutor(max_workers=2) as pool:   # polite to Wikimedia
        for _ in pool.map(lambda t: _download_thumb(*t), todo):
            done += 1
            if progress:
                progress(done, len(todo), "Fotos")
    for e in entries:
        e["thumb"] = _thumb_name(e) if (THUMBS / _thumb_name(e)).exists() else ""
    catalog = {"updated": updated or date.today().isoformat(), "source": "Wikidata / Wikimedia Commons",
               "cameras": entries}
    CATALOG_FILE.write_text(json.dumps(catalog, ensure_ascii=False, indent=0), encoding="utf-8")
    return catalog


def load() -> dict:
    if CATALOG_FILE.exists():
        return json.loads(CATALOG_FILE.read_text(encoding="utf-8"))
    return {"updated": "", "cameras": []}


def age_days(catalog: dict | None = None) -> int | None:
    updated = (catalog or load()).get("updated")
    return (date.today() - date.fromisoformat(updated)).days if updated else None


def needs_refresh() -> bool:
    age = age_days()
    return age is None or age >= REFRESH_DAYS


# ------------------------------------------------------------------ matching / search

def _norm_tokens(text: str, brand_words: set[str]) -> list[str]:
    return [t for t in tokens(text) if t not in brand_words]


def brand_words(cfg: Config) -> set[str]:
    words = {"lumix", "alpha", "eos", "osmo", "black", "camera", "digital", "sony", "group", "corporation"}
    for needles in cfg.brands.values():
        for n in needles:
            words.update(tokens(n))
    return words


def match(cfg: Config, camera_name: str, model: str = "", catalog: dict | None = None) -> dict | None:
    """Catalog entry for a detected/known camera (by name, model code or Wikidata alias)."""
    catalog = catalog or load()
    words = brand_words(cfg)
    wanted = [t for t in (_norm_tokens(camera_name, words), _norm_tokens(model, words)) if t]
    cam = next((c for c in cfg.cameras if c.name == camera_name), None)
    for e in catalog["cameras"]:
        names = [e["name"], *e.get("aliases", [])]
        if cam and any(cam.matches(n) for n in names):
            return e
        if any(_norm_tokens(n, words) in wanted for n in names):
            return e
    return None


def search(cfg: Config, query: str, limit: int = 24, catalog: dict | None = None) -> list[dict]:
    """Known cameras first (they have exact profiles), then catalog models – all with photo if available."""
    catalog = catalog or load()
    q = tokens(query)
    if not q:
        return []

    def hit(text: str) -> bool:
        # every query token must start a word of the name ("x4" finds "X4", not "SX400")
        words = tokens(text)
        return all(any(w.startswith(part) for w in words) for part in q)

    results, seen_qids = [], set()
    for cam in cfg.cameras:
        if hit(f"{cam.name} {cam.brand} {cam.key}"):
            e = match(cfg, cam.name, catalog=catalog)
            if e:
                seen_qids.add(e["qid"])
            results.append({"key": cam.key, "name": cam.name, "brand": cam.brand, "profiles": cam.profiles,
                            "thumb": e.get("thumb", "") if e else "", "known": True})
    for e in catalog["cameras"]:
        if e["qid"] in seen_qids or not hit(" ".join([e["name"], e["maker"], *e.get("aliases", [])])):
            continue
        brand = cfg.brand_of(e["maker"]) or cfg.brand_of(e["name"])
        profiles = cfg.profiles_for_brand(brand) if brand else list(cfg.profiles)
        known = next((c for c in cfg.cameras if any(c.matches(n) for n in [e["name"], *e.get("aliases", [])])), None)
        results.append({"key": known.key if known else _key(e), "name": e["name"], "brand": brand or e["maker"],
                        "profiles": known.profiles if known else sort_profiles(profiles),
                        "thumb": e.get("thumb", ""), "year": e.get("year", ""), "known": bool(known)})
        if len(results) >= limit:
            break
    return results[:limit]


def _key(entry: dict) -> str:
    from .naming import normalize  # noqa: PLC0415
    return normalize(entry["name"])


def sort_profiles(ids: list[str]) -> list[str]:
    """Log profiles first (in profiles.toml order, newest first), display-referred (HLG/Rec.709) last."""
    return sorted(ids, key=lambda p: p in ("HLG", "REC709"))


def thumb_path(name: str) -> Path | None:
    if not re.fullmatch(r"Q\d+\.jpg", name):
        return None
    p = THUMBS / name
    return p if p.exists() else None
