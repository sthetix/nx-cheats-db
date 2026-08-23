#!/usr/bin/env python3
"""Rebuild a reviewable CheatSlips staging database from archived ZIPs."""

import argparse
import json
import re
import zipfile
from collections import OrderedDict, defaultdict
from pathlib import Path

import fetch_extra_sources as crawler


CHEAT_PATH_PATTERN = re.compile(
    r"(?:^|/)contents/([0-9A-F]{16})/cheats/([0-9A-F]{16})\.txt$",
    re.IGNORECASE,
)


def write_json(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as output:
        json.dump(data, output, indent=2, ensure_ascii=False)


def main():
    parser = argparse.ArgumentParser(description="Rebuild clean CheatSlips JSON staging from ZIP archives")
    parser.add_argument("snapshot", type=Path, help="Extracted VPS snapshot directory")
    parser.add_argument("output", type=Path, help="New staging output directory")
    parser.add_argument(
        "--accept-validated-internal-paths",
        action="store_true",
        help="Accept mismatched ZIP paths only when the internal Title ID and Build ID exist in versions.json",
    )
    args = parser.parse_args()

    archive_root = args.snapshot / "downloads" / "cheatslips"
    if not archive_root.is_dir():
        parser.error(f"archive directory not found: {archive_root}")
    if args.output.exists():
        parser.error(f"output already exists: {args.output}")

    with open("versions.json", "r", encoding="utf-8") as versions_file:
        versions = json.load(versions_file)

    values = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))
    rejected = {
        "corrupt_archives": [],
        "path_mismatches": [],
        "archives_without_cheat_txt": [],
        "archives_without_valid_cheats": [],
        "metadata_entries": [],
    }
    archive_count = 0
    accepted_archives = 0
    accepted_path_mismatches = 0

    for archive_path in sorted(archive_root.rglob("*.zip")):
        archive_count += 1
        expected_title = archive_path.parent.parent.name.upper()
        expected_build = archive_path.parent.name.upper()
        try:
            with zipfile.ZipFile(archive_path) as archive:
                corrupt_member = archive.testzip()
                if corrupt_member:
                    rejected["corrupt_archives"].append({
                        "archive": str(archive_path),
                        "member": corrupt_member,
                    })
                    continue

                matching_members = []
                recognized_members = []
                for member in archive.namelist():
                    match = CHEAT_PATH_PATTERN.search(member)
                    if not match:
                        continue
                    title_id, build_id = (value.upper() for value in match.groups())
                    recognized_members.append(member)
                    if title_id != expected_title or build_id != expected_build:
                        version_data = versions.get(title_id, {})
                        internal_path_verified = build_id in {
                            str(value).upper()
                            for key, value in version_data.items()
                            if key not in {"latest", "title"}
                        }
                        rejected["path_mismatches"].append({
                            "archive": str(archive_path),
                            "expected_title": expected_title,
                            "expected_build": expected_build,
                            "actual_title": title_id,
                            "actual_build": build_id,
                            "member": member,
                            "internal_path_verified": internal_path_verified,
                        })
                        if args.accept_validated_internal_paths and internal_path_verified:
                            matching_members.append((member, title_id, build_id))
                            accepted_path_mismatches += 1
                        continue
                    matching_members.append((member, title_id, build_id))

                if not recognized_members:
                    rejected["archives_without_cheat_txt"].append(str(archive_path))
                    continue
                if not matching_members:
                    continue

                archive_had_cheats = False
                for member, title_id, build_id in matching_members:
                    content = archive.read(member).decode("utf-8-sig", errors="replace")
                    parsed = crawler.parse_cheat_txt(content)
                    if not parsed:
                        continue
                    archive_had_cheats = True
                    for key, value in parsed.items():
                        source = {
                            "submission": archive_path.stem,
                            "archive": str(archive_path),
                            "value": value,
                        }
                        if crawler.is_cheat_metadata_key(key):
                            rejected["metadata_entries"].append({
                                "title_id": title_id,
                                "build_id": build_id,
                                "key": key,
                                **source,
                            })
                            continue
                        values[title_id][build_id][key].append(source)
                if archive_had_cheats:
                    accepted_archives += 1
                else:
                    rejected["archives_without_valid_cheats"].append(str(archive_path))
        except (OSError, zipfile.BadZipFile) as exc:
            rejected["corrupt_archives"].append({
                "archive": str(archive_path),
                "error": str(exc),
            })

    conflicts = []
    safe_values = defaultdict(lambda: defaultdict(OrderedDict))
    for title_id, builds in values.items():
        for build_id, cheats in builds.items():
            for key, sources in cheats.items():
                distinct = OrderedDict()
                for source in sources:
                    distinct.setdefault(source["value"], []).append(source["submission"])
                if len(distinct) > 1:
                    conflicts.append({
                        "title_id": title_id,
                        "build_id": build_id,
                        "key": key,
                        "variants": [
                            {"value": value, "submissions": submission_ids}
                            for value, submission_ids in distinct.items()
                        ],
                    })
                    continue
                safe_values[title_id][build_id][key] = next(iter(distinct))

    changed_files = []
    added_builds = 0
    added_cheats = 0
    for title_id, builds in safe_values.items():
        existing = crawler.load_existing(title_id)
        before = json.dumps(existing, sort_keys=True)
        for build_id, cheats in builds.items():
            if build_id not in existing:
                existing[build_id] = OrderedDict()
                added_builds += 1
            for key, value in cheats.items():
                if key not in existing[build_id]:
                    existing[build_id][key] = value
                    added_cheats += 1
        if json.dumps(existing, sort_keys=True) == before:
            continue
        output_path = args.output / "cheats" / f"{title_id}.json"
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as output:
            json.dump(existing, output, indent=4)
        changed_files.append(str(output_path))

    summary = {
        "archives_scanned": archive_count,
        "archives_with_valid_cheats": accepted_archives,
        "candidate_files": len(changed_files),
        "added_builds": added_builds,
        "added_cheats": added_cheats,
        "conflicting_keys": len(conflicts),
        "metadata_entries_quarantined": len(rejected["metadata_entries"]),
        "path_mismatches": len(rejected["path_mismatches"]),
        "validated_path_mismatches_accepted": accepted_path_mismatches,
        "corrupt_archives": len(rejected["corrupt_archives"]),
        "archives_without_cheat_txt": len(rejected["archives_without_cheat_txt"]),
        "archives_without_valid_cheats": len(rejected["archives_without_valid_cheats"]),
    }
    write_json(args.output / "reports" / "summary.json", summary)
    write_json(args.output / "reports" / "conflicts.json", conflicts)
    write_json(args.output / "reports" / "rejected.json", rejected)
    write_json(args.output / "reports" / "changed_files.json", changed_files)
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
