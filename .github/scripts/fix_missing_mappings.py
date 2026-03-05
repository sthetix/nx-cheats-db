#!/usr/bin/env python3
"""
Auto-generate missing version→build_id mappings for nx-cheats-db

This script:
1. Scans all cheats/*.json files for build IDs
2. Checks which are missing from versions.json
3. Generates proper version mappings
4. Updates both versions.json and versions/<title_id>.json

Usage:
    python fix_missing_mappings.py [--dry-run] [--auto-commit]
"""

import json
import os
import sys
from pathlib import Path
from collections import defaultdict
import subprocess

# Colors for terminal output
class Colors:
    HEADER = '\033[95m'
    OKBLUE = '\033[94m'
    OKCYAN = '\033[96m'
    OKGREEN = '\033[92m'
    WARNING = '\033[93m'
    FAIL = '\033[91m'
    ENDC = '\033[0m'
    BOLD = '\033[1m'

class VersionMappingFixer:
    def __init__(self, dry_run=False, auto_commit=False):
        self.repo_path = Path(".")
        self.cheats_dir = self.repo_path / "cheats"
        self.versions_dir = self.repo_path / "versions"
        self.versions_file = self.repo_path / "versions.json"

        self.dry_run = dry_run
        self.auto_commit = auto_commit

        self.existing_mappings = defaultdict(dict)
        self.titles = {}
        self.missing_mappings = defaultdict(list)

    def print_header(self, text):
        print(f"\n{Colors.HEADER}{Colors.BOLD}{'=' * 60}{Colors.ENDC}")
        print(f"{Colors.HEADER}{Colors.BOLD}{text.center(60)}{Colors.ENDC}")
        print(f"{Colors.HEADER}{Colors.BOLD}{'=' * 60}{Colors.ENDC}\n")

    def load_data(self):
        """Load existing version mappings"""
        self.print_header("Loading Database")

        # Load main versions.json
        if self.versions_file.exists():
            with open(self.versions_file, 'r') as f:
                data = json.load(f)
                self._parse_versions(data)
            print(f"{Colors.OKGREEN}✓{Colors.ENDC} Loaded versions.json")

        # Load individual version files
        if self.versions_dir.exists():
            count = 0
            for version_file in self.versions_dir.glob("*.json"):
                with open(version_file, 'r') as f:
                    data = json.load(f)
                    self._parse_versions(data, version_file.stem)
                    count += 1
            print(f"{Colors.OKGREEN}✓{Colors.ENDC} Loaded {count} version files from versions/")

    def _parse_versions(self, data, title_id=None):
        """Parse version data from JSON"""
        for tid, content in data.items():
            if tid == "attribution":
                continue
            if not isinstance(content, dict):
                continue

            if title_id and title_id != tid:
                continue

            if "title" in content:
                self.titles[tid] = content["title"]

            for key, value in content.items():
                if key not in ["title", "latest"] and key.isdigit():
                    self.existing_mappings[tid][int(key)] = value

    def find_missing_mappings(self):
        """Find build IDs in cheats that aren't mapped in versions"""
        self.print_header("Finding Missing Mappings")

        if not self.cheats_dir.exists():
            print(f"{Colors.FAIL}✗{Colors.ENDC} cheats/ directory not found!")
            return

        for cheats_file in self.cheats_dir.glob("*.json"):
            title_id = cheats_file.stem

            with open(cheats_file, 'r') as f:
                cheats_data = json.load(f)

            # Extract all build IDs from cheats file
            build_ids_in_cheats = set()
            for key in cheats_data.keys():
                if key != "attribution" and len(key) == 16:
                    try:
                        int(key, 16)
                        build_ids_in_cheats.add(key)
                    except ValueError:
                        pass

            # Find unmapped build IDs
            for build_id in build_ids_in_cheats:
                already_mapped = False
                if title_id in self.existing_mappings:
                    for mapped_bid in self.existing_mappings[title_id].values():
                        if mapped_bid == build_id:
                            already_mapped = True
                            break

                if not already_mapped:
                    self.missing_mappings[title_id].append(build_id)

        total_missing = sum(len(v) for v in self.missing_mappings.values())
        print(f"{Colors.OKGREEN}✓{Colors.ENDC} Found {total_missing} missing mappings")
        print(f"{Colors.OKCYAN}ℹ{Colors.ENDC} Across {len(self.missing_mappings)} titles")

    def generate_mappings(self):
        """Generate proper version numbers for missing mappings"""
        self.print_header("Generating Version Mappings")

        self.updates = {}

        for title_id, build_ids in sorted(self.missing_mappings.items()):
            if not build_ids:
                continue

            # Get existing versions for this title
            existing_versions = set()
            if title_id in self.existing_mappings:
                existing_versions = set(self.existing_mappings[title_id].keys())

            # Find next available version number
            # Nintendo uses: version * 65536
            # So: v1.0 = 65536, v2.0 = 131072, v3.0 = 196608, etc.
            used_versions = set()
            if title_id in self.existing_mappings:
                used_versions = set(self.existing_mappings[title_id].keys())

            # Assign sequential version numbers
            new_mappings = {}
            next_version = 65536
            for build_id in sorted(build_ids):
                while next_version in used_versions:
                    next_version += 65536
                new_mappings[str(next_version)] = build_id
                used_versions.add(next_version)
                next_version += 65536

            self.updates[title_id] = {
                "title": self.titles.get(title_id, f"Title {title_id}"),
                "existing_count": len(existing_versions),
                "new_count": len(new_mappings),
                "new_mappings": new_mappings
            }

        print(f"{Colors.OKGREEN}✓{Colors.ENDC} Generated {sum(len(u['new_mappings']) for u in self.updates.values())} new mappings")

    def show_preview(self):
        """Show preview of changes"""
        self.print_header("Preview of Changes")

        for title_id, info in sorted(self.updates.items()):
            print(f"\n{Colors.BOLD}Title:{Colors.ENDC} {title_id}")
            print(f"  {Colors.OKCYAN}Name:{Colors.ENDC} {info['title']}")
            print(f"  {Colors.OKCYAN}Existing:{Colors.ENDC} {info['existing_count']} mappings")
            print(f"  {Colors.WARNING}Adding:{Colors.ENDC} {info['new_count']} new mappings")
            print(f"\n  New version → build_id mappings:")
            for version, build_id in sorted(info["new_mappings"].items(), key=lambda x: int(x[0])):
                print(f"    {version} → {build_id}")

        total_new = sum(len(u['new_mappings']) for u in self.updates.values())
        total_titles = len(self.updates)
        print(f"\n{Colors.BOLD}Summary:{Colors.ENDC}")
        print(f"  {total_new} new mappings across {total_titles} titles")

    def apply_changes(self):
        """Apply the generated mappings to version files"""
        if self.dry_run:
            self.print_header("DRY RUN - No files will be modified")
            return False

        self.print_header("Applying Changes")

        # First, update main versions.json
        if self.versions_file.exists():
            with open(self.versions_file, 'r') as f:
                versions_data = json.load(f)

            for title_id, info in self.updates.items():
                if title_id in versions_data:
                    for version, build_id in info["new_mappings"].items():
                        versions_data[title_id][version] = build_id

                    # Update latest version
                    all_versions = [int(k) for k in versions_data[title_id].keys() if k.isdigit()]
                    if all_versions:
                        versions_data[title_id]["latest"] = max(all_versions)

            with open(self.versions_file, 'w') as f:
                json.dump(versions_data, f, indent=4, ensure_ascii=False)
            print(f"{Colors.OKGREEN}✓{Colors.ENDC} Updated versions.json")

        # Then, update individual version files
        updated_count = 0
        for title_id, info in self.updates.items():
            version_file = self.versions_dir / f"{title_id}.json"
            if version_file.exists():
                with open(version_file, 'r') as f:
                    version_data = json.load(f)

                for version, build_id in info["new_mappings"].items():
                    version_data[version] = build_id

                # Update latest version
                all_versions = [int(k) for k in version_data.keys() if k.isdigit()]
                if all_versions:
                    version_data["latest"] = max(all_versions)

                with open(version_file, 'w') as f:
                    json.dump(version_data, f, indent=4, ensure_ascii=False)
                updated_count += 1

        print(f"{Colors.OKGREEN}✓{Colors.ENDC} Updated {updated_count} version files")
        return True

    def commit_changes(self):
        """Git commit and push the changes"""
        if not self.auto_commit:
            return

        self.print_header("Committing Changes")

        try:
            # Check if there are changes
            result = subprocess.run(
                ["git", "status", "--porcelain"],
                capture_output=True,
                text=True
            )

            if not result.stdout.strip():
                print(f"{Colors.WARNING}No changes to commit{Colors.ENDC}")
                return

            # Add files
            subprocess.run(["git", "add", "versions.json", "versions/"], check=True)

            # Commit
            total_new = sum(len(u['new_mappings']) for u in self.updates.values())
            commit_msg = f"fix: add {total_new} missing version→build_id mappings"
            subprocess.run(["git", "commit", "-m", commit_msg], check=True)

            print(f"{Colors.OKGREEN}✓{Colors.ENDC} Changes committed")

            # Push
            print(f"{Colors.OKCYAN}ℹ{Colors.ENDC} To push, run: git push")

        except subprocess.CalledProcessError as e:
            print(f"{Colors.FAIL}✗{Colors.ENDC} Error during git operations: {e}")

    def run(self):
        """Main execution flow"""
        print(f"""
╔═══════════════════════════════════════════════════════════╗
║   nx-cheats-db Missing Mapping Fixer                       ║
║   Auto-generates version→build_id mappings                 ║
╚═══════════════════════════════════════════════════════════╝
""")

        if self.dry_run:
            print(f"{Colors.WARNING}DRY RUN MODE - No files will be modified{Colors.ENDC}\n")

        self.load_data()
        self.find_missing_mappings()

        if not self.missing_mappings:
            print(f"\n{Colors.OKGREEN}✓{Colors.ENDC} All build IDs are already mapped!")
            return

        self.generate_mappings()
        self.show_preview()

        if self.dry_run:
            print(f"\n{Colors.WARNING}Dry run complete. Run without --dry-run to apply changes.{Colors.ENDC}")
            return

        response = input(f"\nApply these changes? (y/n): ").strip().lower()
        if response != 'y':
            print("Aborted.")
            return

        self.apply_changes()
        self.commit_changes()

        print(f"\n{Colors.OKGREEN}✓{Colors.ENDC} Done!")

if __name__ == "__main__":
    dry_run = "--dry-run" in sys.argv
    auto_commit = "--auto-commit" in sys.argv

    fixer = VersionMappingFixer(dry_run=dry_run, auto_commit=auto_commit)
    fixer.run()
