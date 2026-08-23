#!/usr/bin/env python3
"""Fetch only new CheatSlips submissions discovered through its sitemap."""

import argparse
import json
import os
import random
import re
import shutil
import time
import xml.etree.ElementTree as ET
import zipfile
from collections import OrderedDict
from pathlib import Path
from urllib.parse import urlparse


STATE_PATH = Path(".cache/cheatslips_incremental_state.json")
ERROR_LOG_PATH = Path("logs/cheatslips_incremental_errors.log")
DOWNLOAD_DIR = Path("downloads/cheatslips")


def save_json(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(data, indent=2), encoding="utf-8")
    temporary.replace(path)


def log_error(url: str, message: str):
    ERROR_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(ERROR_LOG_PATH, "a", encoding="utf-8") as error_log:
        error_log.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')}\t{url}\t{message}\n")


def sitemap_submission_urls(crawler) -> set[str]:
    response, error = crawler.fetch_with_retry("https://www.cheatslips.com/sitemap.xml", crawler.SCRAPER)
    if not response:
        raise RuntimeError(error or "failed to fetch sitemap index")

    sitemap_urls = [node.text for node in ET.fromstring(response.content).iter() if node.tag.endswith("loc")]
    submissions = set()
    for sitemap_url in sitemap_urls:
        if "/sitemap/games/" not in sitemap_url:
            continue
        page, error = crawler.fetch_with_retry(sitemap_url, crawler.SCRAPER)
        if not page:
            raise RuntimeError(error or f"failed to fetch {sitemap_url}")
        for node in ET.fromstring(page.content).iter():
            if not node.tag.endswith("loc") or not node.text:
                continue
            parts = urlparse(node.text).path.strip("/").split("/")
            if len(parts) == 3 and parts[0] == "game" and parts[2].isdigit():
                submissions.add(node.text.rstrip("/"))
        time.sleep(random.uniform(0.2, 0.4))
    return submissions


def download_submission(crawler, detail_url: str) -> tuple[Path, dict[str, OrderedDict]]:
    response, error = crawler.fetch_with_retry(detail_url, crawler.SCRAPER)
    if not response:
        raise RuntimeError(error or "failed to fetch submission page")

    soup = crawler.BeautifulSoup(response.text, "html.parser")
    csrf = soup.select_one("form[method='post'] input[name='csrf_token']")
    if not csrf or not csrf.get("value"):
        raise RuntimeError("download form unavailable; cookie may be expired")

    download = crawler.SCRAPER.post(
        detail_url,
        data={"csrf_token": csrf["value"], "action": "download"},
        timeout=30,
        stream=True,
    )
    download.raise_for_status()

    submission_id = detail_url.rsplit("/", 1)[-1]
    incoming_dir = DOWNLOAD_DIR / "incoming"
    incoming_dir.mkdir(parents=True, exist_ok=True)
    temporary = incoming_dir / f"{submission_id}.zip.tmp"
    chunks = download.iter_content(chunk_size=64 * 1024)
    first_chunk = next(chunks, b"")
    if not first_chunk.startswith(b"PK"):
        raise RuntimeError("download did not return a ZIP; cookie may be expired")
    with open(temporary, "wb") as archive_file:
        archive_file.write(first_chunk)
        for chunk in chunks:
            archive_file.write(chunk)

    extracted = {}
    destinations = []
    with zipfile.ZipFile(temporary) as archive:
        for name in archive.namelist():
            match = re.search(
                r"(?:^|/)contents/([0-9A-F]{16})/cheats/([0-9A-F]{16})\.txt$",
                name,
                re.IGNORECASE,
            )
            if not match:
                continue
            title_id, build_id = (value.upper() for value in match.groups())
            cheats = crawler.parse_cheat_txt(
                archive.read(name).decode("utf-8-sig", errors="replace")
            )
            cheats = OrderedDict(
                (key, value)
                for key, value in cheats.items()
                if not crawler.is_cheat_metadata_key(key)
            )
            if not cheats:
                continue
            extracted.setdefault(title_id, OrderedDict()).setdefault(build_id, OrderedDict()).update(cheats)
            destinations.append(DOWNLOAD_DIR / title_id / build_id / f"{submission_id}.zip")

    if not extracted:
        unmatched = DOWNLOAD_DIR / "unmatched" / f"{submission_id}.zip"
        unmatched.parent.mkdir(parents=True, exist_ok=True)
        temporary.replace(unmatched)
        raise RuntimeError(f"ZIP did not contain a recognized cheat TXT; saved as {unmatched}")

    first_destination = destinations[0]
    first_destination.parent.mkdir(parents=True, exist_ok=True)
    temporary.replace(first_destination)
    for destination in destinations[1:]:
        if destination == first_destination:
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(first_destination, destination)
    return first_destination, extracted


def main():
    parser = argparse.ArgumentParser(description="Download new CheatSlips submissions only")
    parser.add_argument("--cookies", type=Path, default=Path("cookies"))
    parser.add_argument("--initialize", action="store_true",
                        help="Record the current sitemap as the baseline without downloading")
    parser.add_argument("--max-new", type=int, default=50,
                        help="Maximum new submissions to process per run (default: 50)")
    parser.add_argument("--delay", type=float, default=20.0,
                        help="Base delay after each download in seconds (default: 20)")
    parser.add_argument("--jitter", type=float, default=0.25)
    args = parser.parse_args()

    if not args.cookies.is_file():
        parser.error(f"cookie file not found: {args.cookies}")
    if args.max_new < 1:
        parser.error("--max-new must be at least 1")
    if args.delay < 0 or not 0 <= args.jitter <= 1:
        parser.error("invalid delay or jitter")

    os.environ.pop("CHEATSLIPS_COOKIE", None)
    os.environ["CHEATSLIPS_COOKIE_FILE"] = str(args.cookies.resolve())
    os.environ["CHEATSLIPS_DOWNLOAD_DIR"] = str(DOWNLOAD_DIR.resolve())
    import fetch_extra_sources as crawler

    current = sitemap_submission_urls(crawler)
    if args.initialize or not STATE_PATH.is_file():
        save_json(STATE_PATH, {"seen_urls": sorted(current), "updated_at": int(time.time())})
        print(f"Incremental baseline initialized with {len(current)} submissions")
        return

    state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    seen = set(state.get("seen_urls", []))
    new_urls = sorted(current - seen)[:args.max_new]
    print(f"Incremental check: {len(current)} current, {len(current - seen)} new, processing {len(new_urls)}")

    changed = set()
    completed = 0
    failed = 0
    for index, detail_url in enumerate(new_urls, start=1):
        try:
            _, extracted = download_submission(crawler, detail_url)
            for title_id, new_data in extracted.items():
                path = crawler.CHEATS_DIR / f"{title_id}.json"
                before = path.read_bytes() if path.exists() else None
                existing = crawler.load_existing(title_id)
                for build_id, cheats in new_data.items():
                    existing_cheats = existing.get(build_id, {})
                    for key in list(cheats):
                        if key in existing_cheats and cheats[key] != existing_cheats[key]:
                            log_error(
                                detail_url,
                                f"conflicting cheat quarantined: {title_id}/{build_id}/{key}",
                            )
                            del cheats[key]
                crawler.save(title_id, crawler.merge_into(existing, new_data))
                if path.exists() and path.read_bytes() != before:
                    changed.add(path)
            seen.add(detail_url)
            state.update({"seen_urls": sorted(seen), "updated_at": int(time.time())})
            save_json(STATE_PATH, state)
            completed += 1
            print(f"  [{index}/{len(new_urls)}] Downloaded {detail_url}")
            time.sleep(args.delay * random.uniform(1.0 - args.jitter, 1.0 + args.jitter))
        except Exception as exc:
            failed += 1
            log_error(detail_url, str(exc))
            print(f"  [{index}/{len(new_urls)}] Failed {detail_url}: {exc}")

    print(f"Incremental complete: {completed} downloaded, {failed} failed, {len(changed)} file(s) changed")
    for path in sorted(changed):
        print(f"  {path}")


if __name__ == "__main__":
    main()
