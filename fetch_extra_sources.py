import json
import logging
import os
import re
import time
from collections import OrderedDict
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

import cloudscraper
import requests
from bs4 import BeautifulSoup

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
logger = logging.getLogger(__name__)

CHEATS_DIR = Path("cheats")
CHEATS_DIR.mkdir(exist_ok=True)

GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
SESSION = requests.Session()
if GITHUB_TOKEN:
    SESSION.headers["Authorization"] = f"token {GITHUB_TOKEN}"

SCRAPER = cloudscraper.create_scraper()

def is_valid_title_id(s):
    return len(s) == 16 and all(c in "0123456789ABCDEFabcdef" for c in s)

def load_existing(title_id):
    path = CHEATS_DIR / f"{title_id.upper()}.json"
    if path.exists():
        try:
            with open(path, "r") as f:
                return json.load(f, object_pairs_hook=OrderedDict)
        except Exception as e:
            logger.error(f"Error loading {path}: {e}")
    return OrderedDict()

def save(title_id, data):
    if not data: return
    path = CHEATS_DIR / f"{title_id.upper()}.json"
    try:
        with open(path, "w") as f:
            json.dump(data, f, indent=4)
    except Exception as e:
        logger.error(f"Error saving {path}: {e}")

def merge_into(existing, new):
    for build_id, cheats in new.items():
        if build_id not in existing:
            existing[build_id] = cheats
        else:
            existing[build_id].update(cheats)
    return existing

HAMLET_URL = "https://raw.githubusercontent.com/HamletDuFromage/switch-cheats-db/master/cheats/{}.json"

def fetch_hamlet_title(title_id):
    try:
        r = SESSION.get(HAMLET_URL.format(title_id.upper()), timeout=20)
        if r.status_code == 404: return None
        r.raise_for_status()
        raw = r.json(object_pairs_hook=OrderedDict)

        new_data = OrderedDict()
        for bid, cheats in raw.items():
            if bid == "attribution" or not isinstance(cheats, dict): continue
            clean_cheats = OrderedDict()
            for name, source in cheats.items():
                code = "\n".join(l.strip() for l in source.splitlines() if l.strip() and not l.strip().startswith(("[", "{")))
                if code:
                    clean_name = f"[{name.strip('[]{} ')}]"
                    clean_cheats[clean_name] = f"{clean_name}\n{code}\n\n"
            if clean_cheats:
                new_data[bid.upper()] = clean_cheats
        return new_data
    except Exception as e:
        logger.debug(f"Hamlet fetch failed for {title_id}: {e}")
        return None

def process_hamlet_parallel(title_ids):
    logger.info(f"Fetching Hamlet for {len(title_ids)} titles...")
    updated = 0
    with ThreadPoolExecutor(max_workers=10) as executor:
        future_to_tid = {executor.submit(fetch_hamlet_title, tid): tid for tid in title_ids}
        for future in as_completed(future_to_tid):
            tid = future_to_tid[future]
            try:
                data = future.result()
                if data:
                    existing = load_existing(tid)
                    save(tid, merge_into(existing, data))
                    updated += 1
            except Exception as e:
                logger.error(f"Error processing {tid}: {e}")
    logger.info(f"Hamlet: updated {updated} titles")

if __name__ == "__main__":
    # Simplified main for now
    title_ids = [f.stem for f in CHEATS_DIR.glob("*.json")]
    process_hamlet_parallel(title_ids)
