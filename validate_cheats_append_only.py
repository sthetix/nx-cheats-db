#!/usr/bin/env python3
"""Reject cheat database changes that modify or remove existing entries."""

import argparse
import json
import re
import subprocess
from pathlib import Path

import fetch_extra_sources as crawler


TITLE_ID_PATTERN = re.compile(r"^[0-9A-F]{16}$")
BUILD_ID_PATTERN = re.compile(r"^[0-9A-F]{16}$")
CODE_LINE_PATTERN = re.compile(r"^(?:[0-9A-F]{8})(?:\s+[0-9A-F]{8}){1,4}$")


def git_output(*args: str) -> str:
    return subprocess.run(
        ["git", *args],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    ).stdout


def load_json(path: Path):
    with open(path, "r", encoding="utf-8") as source:
        return json.load(source)


def load_base(ref: str, relative_path: str):
    result = subprocess.run(
        ["git", "show", f"{ref}:{relative_path}"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    )
    if result.returncode != 0:
        return None
    return json.loads(result.stdout)


def validate_file(ref: str, path: Path) -> list[str]:
    errors = []
    title_id = path.stem.upper()
    if not TITLE_ID_PATTERN.fullmatch(title_id):
        return [f"invalid Title ID filename: {path}"]

    current = load_json(path)
    if not isinstance(current, dict):
        return [f"top-level JSON value is not an object: {path}"]

    relative_path = path.as_posix()
    base = load_base(ref, relative_path)
    if base is not None:
        for build_id, old_cheats in base.items():
            if build_id not in current:
                errors.append(f"removed build: {title_id}/{build_id}")
                continue
            for key, old_value in old_cheats.items():
                if key not in current[build_id]:
                    errors.append(f"removed cheat: {title_id}/{build_id}/{key}")
                elif current[build_id][key] != old_value:
                    errors.append(f"modified cheat: {title_id}/{build_id}/{key}")

    for build_id, cheats in current.items():
        if not BUILD_ID_PATTERN.fullmatch(build_id.upper()):
            errors.append(f"invalid Build ID: {title_id}/{build_id}")
            continue
        if not isinstance(cheats, dict):
            errors.append(f"cheat collection is not an object: {title_id}/{build_id}")
            continue
        old_cheats = (base or {}).get(build_id, {})
        for key, value in cheats.items():
            if key in old_cheats:
                continue
            if crawler.is_cheat_metadata_key(key):
                errors.append(f"metadata key added as cheat: {title_id}/{build_id}/{key}")
            if not isinstance(value, str):
                errors.append(f"non-string cheat value: {title_id}/{build_id}/{key}")
                continue
            code_lines = [line.strip().upper() for line in value.splitlines()]
            if not any(CODE_LINE_PATTERN.fullmatch(line) for line in code_lines):
                errors.append(f"new cheat has no valid code line: {title_id}/{build_id}/{key}")
    return errors


def main():
    parser = argparse.ArgumentParser(description="Validate append-only cheat database changes")
    parser.add_argument("--base", default="origin/main")
    args = parser.parse_args()

    changed = set(git_output("diff", "--name-only", args.base, "--", "cheats").splitlines())
    changed.update(git_output("ls-files", "--others", "--exclude-standard", "cheats").splitlines())
    paths = [Path(path) for path in sorted(changed) if path.endswith(".json")]

    errors = []
    for path in paths:
        if not path.is_file():
            errors.append(f"deleted cheat file: {path}")
            continue
        try:
            errors.extend(validate_file(args.base, path))
        except (OSError, json.JSONDecodeError) as exc:
            errors.append(f"invalid JSON: {path}: {exc}")

    if errors:
        print("Append-only validation failed:")
        for error in errors:
            print(f"  {error}")
        raise SystemExit(1)

    print(f"Append-only validation passed for {len(paths)} cheat file(s)")


if __name__ == "__main__":
    main()
