#!/usr/bin/env python3
"""
Fetches cheats from all sources used by ns-emu-cheats-downloader that are NOT
already covered by database_builder.py (GbaTemp + Chansey), and merges them
into the existing cheats/*.json files.

Sources:
  Blawar     — bulk JSON from blawar/titledb
  Ibnux      — GitHub repo: ibnux/switch-cheat
  CheatSlips — scraped per-title (needs title name from versions.json)
  Tinfoil    — scraped per-title (needs title ID)

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

import cloudscraper
import requests
from bs4 import BeautifulSoup

CHEATS_DIR = Path("cheats")
CHEATS_DIR.mkdir(exist_ok=True)

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


# ── Blawar ───────────────────────────────────────────────────────────────────

def process_blawar():
    print("Fetching Blawar bulk JSON ...")
    url = "https://raw.githubusercontent.com/blawar/titledb/master/cheats.json"
    r = SESSION.get(url, timeout=120)
    r.raise_for_status()
    raw = r.json()

    updated = 0
    for title_id, builds in raw.items():
        if not is_valid_title_id(title_id) or not isinstance(builds, dict):
            continue

        new_data = OrderedDict()
        for build_id, build_data in builds.items():
            if not isinstance(build_data, dict):
                continue
            cheats = OrderedDict()
            for entry in build_data.values():
                if not isinstance(entry, dict):
                    continue
                title_field = entry.get("title", "").strip()
                source = entry.get("source", "").strip()
                if not title_field or not source:
                    continue
                # title_field is "[Name]" format; source is code-only (no header line)
                key = title_field if title_field.startswith("[") else f"[{title_field}]"
                value = f"{key}\n{source.strip()}\n\n"
                cheats.setdefault(key, value)
            if cheats:
                new_data[build_id.upper()] = cheats

        if new_data:
            existing = load_existing(title_id)
            save(title_id, merge_into(existing, new_data))
            updated += 1

    print(f"  Blawar: updated {updated} title files")


# ── Ibnux ────────────────────────────────────────────────────────────────────

IBNUX_LIST_URL = "https://api.github.com/repos/ibnux/switch-cheat/contents/atmosphere/titles"
IBNUX_CHEATS_URL = "https://api.github.com/repos/ibnux/switch-cheat/contents/atmosphere/titles/{}/cheats"


def process_ibnux():
    print("Fetching Ibnux title list ...")
    r = SESSION.get(IBNUX_LIST_URL, timeout=30)
    if r.status_code != 200:
        print(f"  Ibnux: could not list titles ({r.status_code})")
        return

    title_ids = [item["name"] for item in r.json()
                 if item.get("type") == "dir" and is_valid_title_id(item["name"])]
    print(f"  Ibnux: {len(title_ids)} titles found")

    updated = 0
    for title_id in title_ids:
        url = IBNUX_CHEATS_URL.format(title_id)
        r = SESSION.get(url, timeout=30)
        if r.status_code == 404:
            time.sleep(0.1)
            continue
        if r.status_code == 403:
            print("  Ibnux: rate limited, sleeping 60s ...")
            time.sleep(60)
            r = SESSION.get(url, timeout=30)
        if r.status_code != 200:
            time.sleep(0.1)
            continue

        new_data = OrderedDict()
        for item in r.json():
            stem = item["name"].replace(".txt", "")
            if not is_valid_title_id(stem):
                continue
            dl_url = item.get("download_url")
            if not dl_url:
                continue
            txt_r = SESSION.get(dl_url, timeout=30)
            if txt_r.status_code != 200:
                continue
            cheats = parse_cheat_txt(txt_r.text)
            if cheats:
                new_data[stem.upper()] = cheats

        if new_data:
            existing = load_existing(title_id)
            save(title_id, merge_into(existing, new_data))
            updated += 1

        time.sleep(0.2)

    print(f"  Ibnux: updated {updated} title files")


# ── CheatSlips ────────────────────────────────────────────────────────────────

def fetch_cheatslips_title(title_id: str, title_name: str) -> OrderedDict:
    slug = normalize_title_name(title_name)
    if not slug:
        return OrderedDict()

    base_url = f"https://www.cheatslips.com/game/{slug}"
    r = SCRAPER.get(base_url, timeout=30)
    if r.status_code != 200:
        return OrderedDict()

    soup = BeautifulSoup(r.text, "html.parser")
    build_links = soup.select("tr > td > a")
    if not build_links:
        return OrderedDict()

    new_data = OrderedDict()

    for a_tag in build_links:
        build_id = a_tag.get_text(strip=True).upper()
        if not is_valid_title_id(build_id):
            continue

        build_url = f"{base_url}/{build_id}"
        r2 = SCRAPER.get(build_url, timeout=30)
        if r2.status_code != 200:
            time.sleep(0.5)
            continue

        soup2 = BeautifulSoup(r2.text, "html.parser")
        cheats = OrderedDict()

        for a2 in soup2.select(".text-secondary"):
            href = a2.get("href", "")
            segment = href.rstrip("/").rsplit("/", 1)[-1]
            if not segment:
                continue

            sources_url = f"{base_url}/{segment}/sources"
            r3 = SCRAPER.get(sources_url, timeout=30)
            if r3.status_code != 200:
                time.sleep(0.5)
                continue

            soup3 = BeautifulSoup(r3.text, "html.parser")
            for tbody in soup3.select("tbody"):
                rows = list(tbody.children)
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

            time.sleep(0.3)

        if cheats:
            new_data[build_id] = cheats

        time.sleep(0.5)

    return new_data


def process_cheatslips(title_names: dict[str, str], title_ids: list[str]):
    print(f"Fetching CheatSlips for {len(title_ids)} titles ...")
    updated = 0
    for title_id in title_ids:
        name = title_names.get(title_id)
        if not name:
            continue
        new_data = fetch_cheatslips_title(title_id, name)
        if new_data:
            existing = load_existing(title_id)
            save(title_id, merge_into(existing, new_data))
            updated += 1
        time.sleep(0.5)
    print(f"  CheatSlips: updated {updated} title files")


# ── Tinfoil ───────────────────────────────────────────────────────────────────

def fetch_tinfoil_title(title_id: str) -> OrderedDict:
    url = f"https://tinfoil.io/Title/{title_id}"
    for attempt in range(3):
        try:
            r = SESSION.get(url, timeout=30)
            break
        except requests.exceptions.Timeout:
            if attempt == 2:
                return OrderedDict()
            time.sleep(5)
    if r.status_code != 200:
        return OrderedDict()

    soup = BeautifulSoup(r.text, "html.parser")

    # Build patch → build_id map from the fixed table
    patch_to_build = {}
    for tr in soup.select("table.fixed > tbody > tr"):
        cols = tr.find_all("td")
        if len(cols) >= 2:
            build_id = cols[0].get_text(strip=True)
            patch    = cols[1].get_text(strip=True)
            if build_id and patch:
                patch_to_build[patch] = build_id

    # Find the "Cheats" section h4
    cheats_h4 = None
    for h4 in soup.select("div > h4:nth-child(1)"):
        if "Cheats" in h4.get_text():
            cheats_h4 = h4
    if not cheats_h4:
        return OrderedDict()

    # The cheats table is in the next sibling div
    cheats_div = None
    for sib in cheats_h4.next_siblings:
        if sib.name == "div":
            cheats_div = sib
            break
    if not cheats_div:
        return OrderedDict()

    new_data = OrderedDict()

    for tr in cheats_div.select("table > tbody > tr"):
        cols = tr.find_all("td")
        if len(cols) < 4:
            continue
        name  = cols[0].get_text(strip=True).strip("[]{}").strip()
        patch = cols[1].get_text(strip=True)
        code_items = [
            li.get_text(strip=True)
            for li in cols[3].select("ul > li")
            if li.get_text(strip=True)
        ]
        # skip header-style lines
        code_lines = [l for l in code_items if not l.startswith(("[", "{"))]
        if not name or not code_lines:
            continue
        build_id = patch_to_build.get(patch)
        if not build_id:
            continue
        key   = f"[{name}]"
        value = f"{key}\n" + "\n".join(code_lines) + "\n\n"
        new_data.setdefault(build_id, OrderedDict())
        new_data[build_id].setdefault(key, value)

    return new_data


def process_tinfoil(title_ids: list[str]):
    print(f"Fetching Tinfoil for {len(title_ids)} titles ...")
    updated = 0
    for title_id in title_ids:
        try:
            new_data = fetch_tinfoil_title(title_id)
        except Exception as e:
            print(f"  Tinfoil: skipping {title_id} ({e})")
            continue
        if new_data:
            existing = load_existing(title_id)
            save(title_id, merge_into(existing, new_data))
            updated += 1
        time.sleep(0.4)
    print(f"  Tinfoil: updated {updated} title files")


# ── entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    # Sources that don't need title metadata
    process_blawar()
    process_ibnux()

    # Sources that scrape per-title
    ids   = known_title_ids()
    names = load_title_names()

    process_cheatslips(names, ids)
    process_tinfoil(ids)

    print("Done.")
