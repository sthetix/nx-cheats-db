#!/usr/bin/env python3
"""Run the incremental crawler and publish only append-only cheat changes."""

import argparse
import subprocess
import sys
from pathlib import Path


def run(*args: str, capture: bool = False):
    return subprocess.run(
        args,
        check=True,
        text=True,
        stdout=subprocess.PIPE if capture else None,
    )


def output(*args: str) -> str:
    return run(*args, capture=True).stdout.strip()


def main():
    parser = argparse.ArgumentParser(description="Safely publish incremental CheatSlips updates")
    parser.add_argument("--cookies", type=Path, required=True)
    parser.add_argument("--max-new", type=int, default=50)
    parser.add_argument("--delay", type=float, default=20.0)
    parser.add_argument("--jitter", type=float, default=0.25)
    args = parser.parse_args()

    if output("git", "branch", "--show-current") != "main":
        raise SystemExit("publisher must run on the main branch")
    if output("git", "status", "--porcelain", "--untracked-files=no"):
        raise SystemExit("publisher worktree has tracked changes; refusing to continue")

    run("git", "fetch", "origin", "main")
    if output("git", "rev-parse", "HEAD") != output("git", "rev-parse", "origin/main"):
        run("git", "merge", "--ff-only", "origin/main")

    run(
        sys.executable,
        "fetch_cheatslips_incremental.py",
        "--cookies", str(args.cookies),
        "--max-new", str(args.max_new),
        "--delay", str(args.delay),
        "--jitter", str(args.jitter),
    )
    run(sys.executable, "validate_cheats_append_only.py", "--base", "origin/main")

    changed = output("git", "status", "--porcelain", "--", "cheats")
    if not changed:
        print("No safe CheatSlips changes to publish")
        return

    run("git", "add", "--", "cheats")
    run("git", "diff", "--cached", "--check")
    run("git", "commit", "-m", "automatic: added validated CheatSlips cheats")
    run("git", "push", "origin", "main")


if __name__ == "__main__":
    main()
