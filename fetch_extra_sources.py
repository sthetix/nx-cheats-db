#!/usr/bin/env python3
"""
Fetches cheats from the extra sources we keep in this repo that are NOT
already covered by database_builder.py (GbaTemp + Chansey), and merges them
into the existing cheats/*.json files.

Sources:
  Hamlet     — per-title JSON from HamletDuFromage/switch-cheats-db
  CheatSlips — scraped per-title (needs title name from versions.json)

Title IDs  → cheats/*.json filenames
Title names → versions.json["title"]
"""

import json
import os
import re
import time
from collections import OrderedDict
from pathlib import Path
from string import hexdigits
from urllib.parse import urljoin
import unicodedata

import cloudscraper
import requests
from bs4 import BeautifulSoup

CHEATS_DIR = Path("cheats")
CHEATS_DIR.mkdir(exist_ok=True)
CACHE_DIR = Path(".cache")
CACHE_DIR.mkdir(exist_ok=True)
CHEATSLIPS_CACHE_PATH = CACHE_DIR / "cheatslips_game_urls.json"

GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")

# Regular session for GitHub / raw URLs
SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "ns-emu-cheats-downloader"})
if GITHUB_TOKEN:
    SESSION.headers["Authorization"] = f"token {GITHUB_TOKEN}"

# Cloudscraper session for sites with Cloudflare (CheatSlips)
SCRAPER = cloudscraper.create_scraper()
SCRAPER.headers.update({"User-Agent": "ns-emu-cheats-downloader"})


# ── helpers ──────────────────────────────────────────────────────────────────

def is_valid_title_id(s: str) -> bool:
    return len(s) == 16 and all(c in hexdigits for c in s)


def load_existing(title_id: str) -> OrderedDict:
    path = CHEATS_DIR / f"{title_id.upper()}.json"
    if path.exists():
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f, object_pairs_hook=OrderedDict)
        except Exception:
            pass
    return OrderedDict()


def save(title_id: str, data: OrderedDict):
    if not data:
        return
    path = CHEATS_DIR / f"{title_id.upper()}.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4)


def merge_into(existing: OrderedDict, new: OrderedDict) -> OrderedDict:
    """Merge new into existing; existing values win on key collision."""
    for build_id, cheats in new.items():
        if build_id == "attribution":
            continue
        if build_id in existing:
            for cheat_key, cheat_val in cheats.items():
                existing[build_id].setdefault(cheat_key, cheat_val)
        else:
            existing[build_id] = cheats
    return existing


def parse_cheat_txt(content: str) -> OrderedDict:
    """Parse a standard .txt cheat file into {[Name]: [Name]\nCODE\n\n}."""
    out = OrderedDict()
    current_key = None
    current_lines = []

    def flush():
        if current_key and len(current_lines) > 1:
            out[current_key] = "\n".join(current_lines).strip() + "\n\n"

    for line in content.splitlines():
        s = line.strip()
        if not s:
            continue
        is_header = (s.startswith("[") and s.endswith("]")) or \
                    (s.startswith("{") and s.endswith("}"))
        if is_header:
            flush()
            current_key = s
            current_lines = [s]
        elif current_key:
            current_lines.append(s)

    flush()
    return out


def normalize_title_name(title: str) -> str:
    """Matches utils.rs normalize_title_name exactly."""
    result = []
    prev_dash = False
    for ch in title:
        if ch == " ":
            if result and not prev_dash:
                result.append("-")
                prev_dash = True
        elif ch in ("®", "™"):
            prev_dash = False
        elif ch in ("é", "É"):
            result.append("e")
            prev_dash = False
        elif ch.isascii() and (ch.isalnum() or ch == "-"):
            lower = ch.lower()
            if lower == "-" and (not result or prev_dash):
                continue
            result.append(lower)
            prev_dash = lower == "-"
    name = "".join(result)
    return name.rstrip("-")


def simplify_title_name(title: str) -> str:
    """Looser normalization for matching titles from HTML listings/search results."""
    normalized = unicodedata.normalize("NFKD", title)
    ascii_text = normalized.encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", "", ascii_text.lower())


def load_cheatslips_cache() -> dict[str, str]:
    try:
        with open(CHEATSLIPS_CACHE_PATH, "r", encoding="utf-8") as f:
            raw = json.load(f)
        return {k.upper(): v for k, v in raw.items() if isinstance(v, str) and v}
    except Exception:
        return {}


def save_cheatslips_cache(cache: dict[str, str]):
    with open(CHEATSLIPS_CACHE_PATH, "w", encoding="utf-8") as f:
        json.dump(dict(sorted(cache.items())), f, indent=2, ensure_ascii=False)


def extract_cheatslips_game_links(html: str) -> list[tuple[str, str]]:
    soup = BeautifulSoup(html, "html.parser")
    seen = set()
    results = []
    for a in soup.select("a[href]"):
        href = (a.get("href") or "").strip()
        if not href.startswith("/game/"):
            continue
        title = " ".join(a.get_text(" ", strip=True).split())
        abs_url = urljoin("https://www.cheatslips.com", href)
        if abs_url in seen:
            continue
        seen.add(abs_url)
        results.append((title, abs_url))
    return results


def score_cheatslips_candidate(title_name: str, target_slug: str, candidate_title: str, candidate_url: str) -> int:
    score = 0
    candidate_slug = candidate_url.rstrip("/").split("/game/", 1)[-1].split("/", 1)[0].lower()
    target_simple = simplify_title_name(title_name)
    candidate_simple = simplify_title_name(candidate_title)

    if candidate_slug == target_slug:
        score += 100
    elif candidate_slug.startswith(target_slug + "/"):
        score += 90
    elif candidate_slug.startswith(target_slug + "-"):
        score += 85
    elif target_slug in candidate_slug:
        score += 40

    if candidate_simple == target_simple:
        score += 100
    elif candidate_simple.startswith(target_simple):
        score += 75
    elif target_simple.startswith(candidate_simple):
        score += 60
    elif target_simple and target_simple in candidate_simple:
        score += 35

    return score


def resolve_cheatslips_game_url(title_id: str, title_name: str, cache: dict[str, str], verbose: bool = False) -> str | None:
    cached = cache.get(title_id.upper())
    if cached:
        return cached

    slug = normalize_title_name(title_name)
    if not slug:
        return None

    direct_url = f"https://www.cheatslips.com/game/{slug}"
    direct_response, _ = fetch_with_retry(direct_url, SCRAPER, max_retries=2, base_delay=0.5)
    if direct_response and direct_response.status_code == 200:
        cache[title_id.upper()] = direct_url
        return direct_url

    candidate_urls = [
        f"https://www.cheatslips.com/games/{slug[:1].upper()}",
        f"https://www.cheatslips.com/games?terms={requests.utils.quote(title_name)}",
        f"https://www.cheatslips.com/games?terms={requests.utils.quote(slug.replace('-', ' '))}",
    ]

    candidates: list[tuple[int, str, str]] = []
    seen_urls = set()

    for url in candidate_urls:
        response, err = fetch_with_retry(url, SCRAPER, max_retries=2, base_delay=0.5)
        if not response or response.status_code != 200:
            if verbose and err:
                print(f"    CheatSlips resolver: failed listing {url} ({err})")
            continue

        for candidate_title, candidate_url in extract_cheatslips_game_links(response.text):
            if candidate_url in seen_urls:
                continue
            seen_urls.add(candidate_url)
            score = score_cheatslips_candidate(title_name, slug, candidate_title, candidate_url)
            if score > 0:
                candidates.append((score, candidate_title, candidate_url))

        time.sleep(0.2)

    if not candidates:
        return None

    candidates.sort(key=lambda item: (-item[0], item[1].lower(), item[2]))
    best_score, best_title, best_url = candidates[0]
    if verbose:
        print(f"    CheatSlips resolver matched '{title_name}' -> '{best_title}' ({best_url}, score={best_score})")

    if best_score < 75:
        if verbose:
            print(f"    CheatSlips resolver: best match too weak for '{title_name}'")
        return None

    cache[title_id.upper()] = best_url
    return best_url


def load_title_names() -> dict[str, str]:
    """Returns {title_id_upper: title_name} from versions.json."""
    try:
        with open("versions.json", "r", encoding="utf-8") as f:
            raw = json.load(f)
        return {tid.upper(): v["title"] for tid, v in raw.items() if "title" in v}
    except Exception as e:
        print(f"  Warning: could not load versions.json: {e}")
        return {}


def known_title_ids() -> list[str]:
    """All title IDs we already have JSON files for."""
    return [p.stem.upper() for p in CHEATS_DIR.glob("*.json") if is_valid_title_id(p.stem)]


def candidate_title_ids(title_names: dict[str, str]) -> list[str]:
    """Union of existing cheat files and all title IDs we can discover by name."""
    return sorted(set(known_title_ids()) | {tid.upper() for tid in title_names.keys() if is_valid_title_id(tid)})


# ── Hamlet ───────────────────────────────────────────────────────────────────

HAMLET_URL = (
    "https://raw.githubusercontent.com/HamletDuFromage/"
    "switch-cheats-db/master/cheats/{}.json"
)


def fetch_hamlet_title(title_id: str) -> OrderedDict:
    r = SESSION.get(HAMLET_URL.format(title_id.upper()), timeout=30)
    if r.status_code == 404:
        return OrderedDict()
    r.raise_for_status()

    raw = r.json(object_pairs_hook=OrderedDict)
    new_data = OrderedDict()

    for build_id, cheats in raw.items():
        if build_id == "attribution":
            continue
        if not isinstance(cheats, dict):
            continue

        build_cheats = OrderedDict()
        for name, source in cheats.items():
            if not isinstance(name, str) or not isinstance(source, str):
                continue
            code_lines = [
                line.strip()
                for line in source.splitlines()
                if line.strip() and not line.strip().startswith(("[", "{"))
            ]
            clean_name = name.strip().strip("[]{}").strip()
            if not clean_name or not code_lines:
                continue

            key = f"[{clean_name}]"
            value = f"{key}\n" + "\n".join(code_lines) + "\n\n"
            build_cheats.setdefault(key, value)

        if build_cheats:
            new_data[build_id.upper()] = build_cheats

    return new_data


def process_hamlet(title_ids: list[str]):
    print(f"Fetching Hamlet for {len(title_ids)} titles ...")
    updated = 0
    for title_id in title_ids:
        try:
            new_data = fetch_hamlet_title(title_id)
        except Exception as e:
            print(f"  Hamlet: skipping {title_id} ({e})")
            continue

        if new_data:
            existing = load_existing(title_id)
            save(title_id, merge_into(existing, new_data))
            updated += 1

        time.sleep(0.15)

    print(f"  Hamlet: updated {updated} title files")


# ── CheatSlips ────────────────────────────────────────────────────────────────

def fetch_with_retry(url: str, scraper, max_retries: int = 3, base_delay: float = 1.0) -> tuple:
    """Fetch a URL with retry logic for rate limiting and transient errors.
    Returns (response, error_msg) tuple."""
    for attempt in range(max_retries):
        try:
            response = scraper.get(url, timeout=30)
            
            if response.status_code == 200:
                return response, None
            
            if response.status_code in (403, 429):
                # Rate limited - wait longer before retry
                delay = base_delay * (2 ** attempt) + 5  # Exponential backoff + extra wait
                print(f"    Rate limited (HTTP {response.status_code}), waiting {delay:.1f}s...")
                time.sleep(delay)
                continue
            
            if response.status_code == 404:
                return None, "Not found (404)"
            
            # Other errors - retry with backoff
            if attempt < max_retries - 1:
                delay = base_delay * (2 ** attempt)
                print(f"    HTTP {response.status_code}, retrying in {delay:.1f}s...")
                time.sleep(delay)
                continue
            
            return None, f"HTTP {response.status_code}"
            
        except requests.exceptions.Timeout:
            if attempt < max_retries - 1:
                delay = base_delay * (2 ** attempt)
                print(f"    Timeout, retrying in {delay:.1f}s...")
                time.sleep(delay)
                continue
            return None, "Timeout after retries"
        except Exception as e:
            return None, str(e)
    
    return None, "Max retries exceeded"


def fetch_cheatslips_title(title_id: str, title_name: str, cache: dict[str, str], verbose: bool = False) -> OrderedDict:
    game_url = resolve_cheatslips_game_url(title_id, title_name, cache, verbose=verbose)
    if not game_url:
        if verbose:
            print(f"    Skipping {title_id}: could not resolve CheatSlips URL for '{title_name}'")
        return OrderedDict()
    
    if verbose:
        print(f"  Fetching CheatSlips: {title_name} ({title_id}) -> {game_url}")
    
    response, err = fetch_with_retry(game_url, SCRAPER)
    if not response or response.status_code != 200:
        if verbose and err:
            print(f"    Failed to fetch game page: {err}")
        return OrderedDict()

    soup = BeautifulSoup(response.text, "html.parser")
    
    # Try multiple selector patterns for build links (site structure may vary)
    build_links = (
        soup.select("tr > td > a") or
        soup.select("table a") or
        soup.select("a[href*='/game/']") or
        soup.select(".build-link, a.build, a[data-buildid]")
    )
    
    if not build_links:
        if verbose:
            print(f"    No build links found on page")
        return OrderedDict()

    new_data = OrderedDict()
    cheats_found = 0

    for a_tag in build_links:
        build_id = a_tag.get_text(strip=True).upper()
        if not is_valid_title_id(build_id):
            continue

        if verbose:
            print(f"    Processing build: {build_id}")

        build_url = f"{game_url.rstrip('/')}/{build_id}"
        response2, err2 = fetch_with_retry(build_url, SCRAPER)
        if not response2 or response2.status_code != 200:
            if verbose and err2:
                print(f"      Failed to fetch build page: {err2}")
            time.sleep(0.3)
            continue

        soup2 = BeautifulSoup(response2.text, "html.parser")
        cheats = OrderedDict()

        # Try multiple patterns for cheat links (more robust selectors)
        cheat_links = (
            soup2.select(".text-secondary") or
            soup2.select("a.text-secondary") or
            soup2.select(".cheat-link, a.cheat") or
            soup2.select("table a[href*='/cheat/']") or
            soup2.select("a[href*='/cheats/']")
        )

        for a2 in cheat_links:
            href = a2.get("href", "")
            if not href:
                continue
            segment = href.rstrip("/").rsplit("/", 1)[-1]
            if not segment or segment == build_id:
                continue

            sources_url = f"{game_url.rstrip('/')}/{segment}/sources"
            response3, err3 = fetch_with_retry(sources_url, SCRAPER)
            if not response3 or response3.status_code != 200:
                if verbose and err3:
                    print(f"      Failed to fetch sources: {err3}")
                time.sleep(0.2)
                continue

            soup3 = BeautifulSoup(response3.text, "html.parser")
            for tbody in soup3.select("tbody"):
                strong = tbody.select_one("strong")
                if not strong:
                    continue
                name = strong.get_text(strip=True).strip("[]{}").strip()
                pre = tbody.select_one("pre")
                if not pre:
                    continue
                source = pre.get_text(strip=True)
                code_lines = [
                    l.strip() for l in source.splitlines()
                    if l.strip() and not l.strip().startswith(("[", "{"))
                ]
                if not name or not code_lines:
                    continue
                key = f"[{name}]"
                value = f"{key}\n" + "\n".join(code_lines) + "\n\n"
                cheats.setdefault(key, value)
                cheats_found += 1

            time.sleep(0.2)

        if cheats:
            new_data[build_id] = cheats

        time.sleep(0.3)

    if verbose:
        print(f"    Found {cheats_found} cheats across {len(new_data)} builds")

    return new_data


def process_cheatslips(title_names: dict[str, str], title_ids: list[str], verbose: bool = False):
    print(f"Fetching CheatSlips for {len(title_ids)} titles ...")
    updated = 0
    failed = []
    cache = load_cheatslips_cache()
    
    for title_id in title_ids:
        name = title_names.get(title_id)
        if not name:
            if verbose:
                print(f"  Skipping {title_id}: no title name found")
            continue
        
        try:
            new_data = fetch_cheatslips_title(title_id, name, cache, verbose=verbose)
            if new_data:
                existing = load_existing(title_id)
                save(title_id, merge_into(existing, new_data))
                updated += 1
        except Exception as e:
            failed.append((title_id, name, str(e)))
            if verbose:
                print(f"  Error processing {title_id}: {e}")
        
        time.sleep(0.5)

    save_cheatslips_cache(cache)
    
    print(f"  CheatSlips: updated {updated} title files")
    if failed:
        print(f"  Failed titles ({len(failed)}):")
        for tid, name, err in failed[:10]:  # Show first 10 failures
            print(f"    - {tid} ({name}): {err}")
        if len(failed) > 10:
            print(f"    ... and {len(failed) - 10} more")


# ── entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="Fetch cheats from various sources")
    parser.add_argument("--cheatslips-only", action="store_true", 
                        help="Only fetch from CheatSlips (for testing)")
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="Enable verbose logging for debugging")
    args = parser.parse_args()
    
    if args.cheatslips_only:
        # Quick test mode - just CheatSlips
        names = load_title_names()
        ids   = candidate_title_ids(names)
        process_cheatslips(names, ids, verbose=args.verbose)
    else:
        # Full run - extra sources we keep enabled
        names = load_title_names()
        ids   = candidate_title_ids(names)

        process_hamlet(ids)
        process_cheatslips(names, ids, verbose=args.verbose)

    print("Done.")
