#!/usr/bin/env python3
"""Download and merge CheatSlips cheats using a local browser cookie export."""

import argparse
import json
import os
import random
import re
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from string import hexdigits
from urllib.parse import urlparse


STATE_PATH = Path(".cache/cheatslips_local_state.json")
SITEMAP_CACHE_PATH = Path(".cache/cheatslips_sitemap_game_urls.json")
ERROR_LOG_PATH = Path("logs/cheatslips_errors.log")


def valid_title_id(value: str) -> str:
    title_id = value.strip().upper()
    if len(title_id) != 16 or any(char not in hexdigits for char in title_id):
        raise argparse.ArgumentTypeError(f"invalid Title ID: {value}")
    return title_id


def load_title_id_file(path: Path) -> list[str]:
    title_ids = []
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        value = line.partition("#")[0].strip()
        if value:
            title_ids.append(valid_title_id(value))
    return title_ids


def save_json(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(data, indent=2), encoding="utf-8")
    temporary.replace(path)


def log_error(game_url: str, message: str):
    ERROR_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    with open(ERROR_LOG_PATH, "a", encoding="utf-8") as error_log:
        error_log.write(f"{timestamp}\t{game_url}\t{message}\n")


def sleep_with_jitter(delay: float, jitter: float) -> float:
    actual_delay = delay * random.uniform(1.0 - jitter, 1.0 + jitter)
    time.sleep(actual_delay)
    return actual_delay


def sitemap_game_urls(crawler, refresh: bool = False) -> list[str]:
    if SITEMAP_CACHE_PATH.is_file() and not refresh:
        return json.loads(SITEMAP_CACHE_PATH.read_text(encoding="utf-8"))

    response = crawler.SCRAPER.get("https://www.cheatslips.com/sitemap.xml", timeout=30)
    response.raise_for_status()
    sitemap_urls = [node.text for node in ET.fromstring(response.content).iter() if node.tag.endswith("loc")]

    game_urls = set()
    for sitemap_url in sitemap_urls:
        if "/sitemap/games/" not in sitemap_url:
            continue
        page = crawler.SCRAPER.get(sitemap_url, timeout=30)
        page.raise_for_status()
        for node in ET.fromstring(page.content).iter():
            if not node.tag.endswith("loc") or not node.text:
                continue
            parts = urlparse(node.text).path.strip("/").split("/")
            if len(parts) == 2 and parts[0] == "game":
                game_urls.add(node.text.rstrip("/"))
        time.sleep(0.25)

    result = sorted(game_urls)
    save_json(SITEMAP_CACHE_PATH, result)
    return result


def extract_title_id(html: str) -> str | None:
    text = re.sub(r"<[^>]+>", " ", html)
    match = re.search(r"\b(?:Game|Title)\s+Id\s*:\s*([0-9A-F]{16})\b", text, re.IGNORECASE)
    return match.group(1).upper() if match else None


def run_gradual_dump(
    crawler,
    names: dict[str, str],
    batch_size: int,
    delay: float,
    download_delay: float,
    jitter: float,
    refresh: bool,
    verbose: bool,
) -> bool:
    game_urls = sitemap_game_urls(crawler, refresh=refresh)
    if not game_urls:
        raise RuntimeError("CheatSlips sitemap did not contain any game URLs")

    state = {"next_index": 0}
    if STATE_PATH.is_file():
        try:
            state.update(json.loads(STATE_PATH.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, TypeError):
            pass

    start = int(state.get("next_index", 0)) % len(game_urls)
    count = min(batch_size, len(game_urls))
    selected = [game_urls[(start + offset) % len(game_urls)] for offset in range(count)]
    changed = set()
    complete_games = 0
    placeholder_games = 0
    failed_games = 0
    no_download_games = 0
    downloaded_builds = 0
    stopped_on_error = False
    cache = crawler.load_cheatslips_cache()

    print(f"Gradual CheatSlips dump: games {start + 1}-{start + count} of {len(game_urls)}")
    for offset, game_url in enumerate(selected, start=1):
        response, error = crawler.fetch_with_retry(game_url, crawler.SCRAPER)
        if not response:
            print(f"  [{offset}/{count}] Failed {game_url}: {error}")
            failed_games += 1
            log_error(game_url, error or "failed to fetch game page")
            state["next_index"] = (start + offset - 1) % len(game_urls)
            save_json(STATE_PATH, state)
            stopped_on_error = True
            break

        title_id = extract_title_id(response.text)
        if not title_id:
            print(f"  [{offset}/{count}] Skipping page without Title ID: {game_url}")
            placeholder_games += 1
            state["next_index"] = (start + offset) % len(game_urls)
            save_json(STATE_PATH, state)
            continue

        path = crawler.CHEATS_DIR / f"{title_id}.json"
        before = path.read_bytes() if path.exists() else None
        soup = crawler.BeautifulSoup(response.text, "html.parser")
        build_links = (
            soup.select("tr > td > a") or
            soup.select("table a") or
            soup.select("a[href*='/game/']") or
            soup.select(".build-link, a.build, a[data-buildid]")
        )
        page_builds = {
            link.get_text(strip=True).upper()
            for link in build_links
            if crawler.is_valid_title_id(link.get_text(strip=True).upper())
        }
        page_builds.discard(title_id)
        existing_builds = set(crawler.load_existing(title_id))
        missing_builds = page_builds - existing_builds
        if not missing_builds:
            complete_games += 1
            state["next_index"] = (start + offset) % len(game_urls)
            save_json(STATE_PATH, state)
            sleep_with_jitter(delay, jitter)
            continue

        title_name = names.get(title_id, game_url.rsplit("/", 1)[-1].replace("-", " "))
        cache[title_id] = game_url
        try:
            new_data = crawler.fetch_cheatslips_title(
                title_id,
                title_name,
                cache,
                verbose=verbose,
                game_url=game_url,
                game_response=response,
                missing_builds_only=True,
            )
        except Exception as exc:
            failed_games += 1
            log_error(game_url, str(exc))
            print(f"  [{offset}/{count}] Error {game_url}: {exc}")
            state["next_index"] = (start + offset - 1) % len(game_urls)
            save_json(STATE_PATH, state)
            stopped_on_error = True
            break
        if new_data:
            crawler.save(title_id, crawler.merge_into(crawler.load_existing(title_id), new_data))
            downloaded_builds += len(new_data)
            sleep_with_jitter(download_delay, jitter)
        else:
            no_download_games += 1
        if path.exists() and path.read_bytes() != before:
            changed.add(path)

        state["next_index"] = (start + offset) % len(game_urls)
        save_json(STATE_PATH, state)
        sleep_with_jitter(delay, jitter)

    crawler.save_cheatslips_cache(cache)
    print(
        "Gradual batch complete: "
        f"{complete_games} already complete, "
        f"{placeholder_games} without Title ID, "
        f"{failed_games} failed, "
        f"{no_download_games} with no downloadable new build, "
        f"{downloaded_builds} build(s) downloaded, "
        f"{len(changed)} file(s) changed"
    )
    for path in sorted(changed):
        print(f"  {path}")
    if stopped_on_error:
        print(f"Stopped on error; the same game will be retried. See {ERROR_LOG_PATH}")
    return not stopped_on_error


def main():
    parser = argparse.ArgumentParser(
        description="Download CheatSlips ZIPs locally and merge them into cheats/*.json"
    )
    parser.add_argument(
        "--cookies",
        type=Path,
        default=Path("cookies"),
        help="Browser cookie export in JSON or Netscape format (default: cookies)",
    )
    parser.add_argument(
        "--title-id",
        type=valid_title_id,
        action="append",
        default=[],
        help="Title ID to process; may be supplied more than once",
    )
    parser.add_argument(
        "--title-id-file",
        type=Path,
        help="Text file containing one Title ID per line",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Process the next gradual batch from the CheatSlips sitemap",
    )
    parser.add_argument("--batch-size", type=int, default=50,
                        help="Number of sitemap games per gradual run (default: 50)")
    parser.add_argument("--delay", type=float, default=1.0,
                        help="Delay between games in seconds (default: 1.0)")
    parser.add_argument("--download-delay", type=float, default=20.0,
                        help="Extra delay after a game downloads new builds (default: 20)")
    parser.add_argument("--jitter", type=float, default=0.25,
                        help="Random delay variation from 0.0 to 1.0 (default: 0.25)")
    parser.add_argument("--continuous", action="store_true",
                        help="Keep processing gradual batches until stopped")
    parser.add_argument("--batch-pause", type=float, default=60.0,
                        help="Pause between continuous batches in seconds (default: 60)")
    parser.add_argument("--refresh-sitemap", action="store_true")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    if not args.cookies.is_file():
        parser.error(f"cookie file not found: {args.cookies}")
    if args.batch_size < 1:
        parser.error("--batch-size must be at least 1")
    if args.delay < 0:
        parser.error("--delay cannot be negative")
    if args.download_delay < 0:
        parser.error("--download-delay cannot be negative")
    if not 0 <= args.jitter <= 1:
        parser.error("--jitter must be between 0.0 and 1.0")
    if args.batch_pause < 0:
        parser.error("--batch-pause cannot be negative")

    requested_ids = list(args.title_id)
    if args.title_id_file:
        if not args.title_id_file.is_file():
            parser.error(f"Title ID file not found: {args.title_id_file}")
        requested_ids.extend(load_title_id_file(args.title_id_file))
    if not requested_ids and not args.all:
        parser.error("provide --title-id, --title-id-file, or --all")

    os.environ.pop("CHEATSLIPS_COOKIE", None)
    os.environ["CHEATSLIPS_COOKIE_FILE"] = str(args.cookies.resolve())
    os.environ["CHEATSLIPS_DOWNLOAD_DIR"] = str(Path("downloads/cheatslips").resolve())

    import fetch_extra_sources as crawler

    names = crawler.load_title_names()
    if args.all:
        title_ids = []
    else:
        title_ids = list(dict.fromkeys(requested_ids))
        missing = [title_id for title_id in title_ids if title_id not in names]
        if missing:
            parser.error(f"Title ID not found in versions.json: {', '.join(missing)}")

    profile = crawler.SCRAPER.get("https://www.cheatslips.com/profile/", timeout=30)
    if "/login" in profile.url or "/logout" not in profile.text:
        parser.error("CheatSlips cookie is expired or not authenticated")

    if args.all:
        refresh_sitemap = args.refresh_sitemap
        while True:
            run_gradual_dump(
                crawler,
                names,
                batch_size=args.batch_size,
                delay=args.delay,
                download_delay=args.download_delay,
                jitter=args.jitter,
                refresh=refresh_sitemap,
                verbose=args.verbose,
            )
            refresh_sitemap = False
            if not args.continuous:
                break

            next_batch_delay = args.batch_pause * random.uniform(1.0 - args.jitter, 1.0 + args.jitter)
            print(f"Waiting {next_batch_delay:.1f} seconds before the next batch (Ctrl+C to stop) ...")
            try:
                time.sleep(next_batch_delay)
            except KeyboardInterrupt:
                print("Stopped. Progress has been saved.")
                break

            while True:
                try:
                    profile = crawler.SCRAPER.get("https://www.cheatslips.com/profile/", timeout=30)
                    break
                except Exception as exc:
                    log_error("https://www.cheatslips.com/profile/", str(exc))
                    retry_delay = args.batch_pause * random.uniform(1.0 - args.jitter, 1.0 + args.jitter)
                    print(f"Session check failed; retrying in {retry_delay:.1f} seconds: {exc}")
                    time.sleep(retry_delay)
            if "/login" in profile.url or "/logout" not in profile.text:
                print("Stopped: CheatSlips cookie is expired or no longer authenticated")
                break
        return

    before = {
        title_id: (crawler.CHEATS_DIR / f"{title_id}.json").read_bytes()
        if (crawler.CHEATS_DIR / f"{title_id}.json").exists()
        else None
        for title_id in title_ids
    }

    crawler.process_cheatslips(names, title_ids, verbose=args.verbose)

    changed = []
    for title_id in title_ids:
        path = crawler.CHEATS_DIR / f"{title_id}.json"
        if path.exists() and path.read_bytes() != before[title_id]:
            changed.append(path)

    print(f"Local CheatSlips crawl complete: {len(changed)} file(s) changed")
    for path in changed:
        print(f"  {path}")


if __name__ == "__main__":
    main()
