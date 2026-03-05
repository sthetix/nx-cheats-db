#!/usr/bin/env python3
"""
nx-cheats-db Analysis Script

Analyzes the cheat database and generates a summary of:
- Missing version mappings
- Formatting issues
- Cheats available from GBAtemp but not in local DB
- Action items for maintainers
"""

import json
import os
import re
from pathlib import Path
from collections import defaultdict
from datetime import datetime
import requests

# Configuration
GBATEMP_API_URL = "https://gbatemp.net/attachments/cheat-updates-json.411363/"
REPO_URL = "https://github.com/sthetix/nx-cheats-db"

class Colors:
    """ANSI color codes for terminal output"""
    HEADER = '\033[95m'
    OKBLUE = '\033[94m'
    OKCYAN = '\033[96m'
    OKGREEN = '\033[92m'
    WARNING = '\033[93m'
    FAIL = '\033[91m'
    ENDC = '\033[0m'
    BOLD = '\033[1m'
    UNDERLINE = '\033[4m'

def print_header(text):
    print(f"\n{Colors.HEADER}{Colors.BOLD}{'=' * 60}{Colors.ENDC}")
    print(f"{Colors.HEADER}{Colors.BOLD}{text.center(60)}{Colors.ENDC}")
    print(f"{Colors.HEADER}{Colors.BOLD}{'=' * 60}{Colors.ENDC}\n")

def print_success(text):
    print(f"{Colors.OKGREEN}✓{Colors.ENDC} {text}")

def print_warning(text):
    print(f"{Colors.WARNING}⚠{Colors.ENDC} {text}")

def print_error(text):
    print(f"{Colors.FAIL}✗{Colors.ENDC} {text}")

def print_info(text):
    print(f"{Colors.OKCYAN}ℹ{Colors.ENDC} {text}")

class CheatsDatabaseAnalyzer:
    def __init__(self):
        self.repo_path = Path(".")
        self.cheats_dir = self.repo_path / "cheats"
        self.versions_dir = self.repo_path / "versions"
        self.versions_file = self.repo_path / "versions.json"

        self.cheats_file = {}
        self.version_mappings = {}
        self.titles = {}
        self.missing_mappings = defaultdict(list)
        self.formatting_issues = defaultdict(list)
        self.gbatemp_cheats = {}

        self.stats = {
            "total_titles": 0,
            "total_cheat_files": 0,
            "total_build_ids": 0,
            "mapped_build_ids": 0,
            "unmapped_build_ids": 0,
            "titles_with_issues": 0,
            "gbatemp_not_in_db": 0
        }

    def load_data(self):
        """Load all data from the database"""
        print_header("Loading Database")

        # Load versions.json
        if self.versions_file.exists():
            with open(self.versions_file, 'r') as f:
                main_versions = json.load(f)
                self._parse_versions_data(main_versions)
            print_success(f"Loaded {self.versions_file}")

        # Load individual version files
        if self.versions_dir.exists():
            for version_file in self.versions_dir.glob("*.json"):
                with open(version_file, 'r') as f:
                    data = json.load(f)
                    self._parse_versions_data(data, version_file.stem)
            print_success(f"Loaded {len(list(self.versions_dir.glob('*.json')))} version files")

        # Load cheats files
        if self.cheats_dir.exists():
            for cheat_file in self.cheats_dir.glob("*.json"):
                with open(cheat_file, 'r') as f:
                    self.cheats_file[cheat_file.stem] = json.load(f)
            print_success(f"Loaded {len(self.cheats_file)} cheat files")

        self.stats["total_titles"] = len(self.cheats_file)
        self.stats["total_cheat_files"] = len(self.cheats_file)

    def _parse_versions_data(self, data, title_id=None):
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

            # Initialize dict for this title ID if not exists
            if tid not in self.version_mappings:
                self.version_mappings[tid] = {}

            for key, value in content.items():
                if key in ["title", "latest"]:
                    continue
                if key.isdigit() and len(value) == 16:
                    self.version_mappings[tid][int(key)] = value

    def analyze_missing_mappings(self):
        """Find build IDs in cheats that aren't mapped in versions"""
        print_header("Analyzing Missing Mappings")

        for title_id, cheats_data in self.cheats_file.items():
            build_ids_in_cheats = set()

            # Extract build IDs from cheats file
            for key in cheats_data.keys():
                if key != "attribution" and len(key) == 16:
                    try:
                        int(key, 16)
                        build_ids_in_cheats.add(key)
                    except ValueError:
                        pass

            self.stats["total_build_ids"] += len(build_ids_in_cheats)

            # Check which are mapped
            mapped = set()
            if title_id in self.version_mappings:
                for build_id in self.version_mappings[title_id].values():
                    mapped.add(build_id)

            self.stats["mapped_build_ids"] += len(mapped)
            unmapped = build_ids_in_cheats - mapped
            self.stats["unmapped_build_ids"] += len(unmapped)

            if unmapped:
                self.missing_mappings[title_id] = list(unmapped)
                self.stats["titles_with_issues"] += 1

        print_success(f"Found {self.stats['unmapped_build_ids']} unmapped build IDs")
        print_info(f"Across {self.stats['titles_with_issues']} titles")

    def analyze_formatting_issues(self):
        """Check for common formatting issues"""
        print_header("Checking Formatting Issues")

        issues_found = 0

        for title_id, cheats_data in self.cheats_file.items():
            title_issues = []

            # Check 1: Empty build ID objects
            for build_id, content in cheats_data.items():
                if build_id == "attribution":
                    continue

                if not isinstance(content, dict):
                    title_issues.append(f"Build ID {build_id}: content is not a dict")
                    issues_found += 1
                    continue

                # Check 2: Empty cheat content
                for cheat_name, cheat_content in content.items():
                    if not cheat_content or cheat_content.strip() == "":
                        title_issues.append(f"Build ID {build_id}: Empty cheat '{cheat_name}'")
                        issues_found += 1

                    # Check 3: Malformed cheat codes
                    if cheat_content:
                        lines = cheat_content.split('\n')
                        for i, line in enumerate(lines, 1):
                            line = line.strip()
                            if not line:
                                continue
                            # Check if it looks like a cheat code (should start with hex digits)
                            if not any(c.isdigit() for c in line[:8]):
                                title_issues.append(f"Build ID {build_id}: Line {i} doesn't look like cheat code")
                                issues_found += 1
                                break

            if title_issues:
                self.formatting_issues[title_id] = title_issues

        print_success(f"Found {issues_found} formatting issues")

    def fetch_gbatemp_cheats(self):
        """Fetch cheat updates from GBAtemp"""
        print_header("Fetching GBAtemp Cheats")

        try:
            response = requests.get(GBATEMP_API_URL, timeout=30)
            response.raise_for_status()
            data = response.json()

            if data and "cheats" in data:
                for cheat in data["cheats"]:
                    title_id = cheat.get("titleId", "").upper()
                    build_id = cheat.get("buildId", "").upper()
                    cheat_name = cheat.get("name", "")

                    if title_id and build_id:
                        if title_id not in self.gbatemp_cheats:
                            self.gbatemp_cheats[title_id] = {}
                        if build_id not in self.gbatemp_cheats[title_id]:
                            self.gbatemp_cheats[title_id][build_id] = []

                        self.gbatemp_cheats[title_id][build_id].append({
                            "name": cheat_name,
                            "credits": cheat.get("credits", "")
                        })

                print_success(f"Fetched {len(self.gbatemp_cheats)} titles from GBAtemp")
            else:
                print_warning("GBAtemp API returned unexpected format")

        except Exception as e:
            print_warning(f"Could not fetch GBAtemp cheats: {e}")

    def compare_with_gbatemp(self):
        """Compare local database with GBAtemp"""
        print_header("Comparing with GBAtemp")

        if not self.gbatemp_cheats:
            print_warning("No GBAtemp data available")
            return

        not_in_local = []

        for title_id in self.gbatemp_cheats:
            if title_id not in self.cheats_file:
                not_in_local.append(title_id)
                continue

            for build_id in self.gbatemp_cheats[title_id]:
                if build_id not in self.cheats_file[title_id]:
                    not_in_local.append(f"{title_id}/{build_id}")

        self.stats["gbatemp_not_in_db"] = len(not_in_local)

        if not_in_local:
            print_warning(f"Found {len(not_in_local)} cheats in GBAtemp but not in local DB")
            self.stats["titles_with_issues"] += len(set(x.split('/')[0] for x in not_in_local))
        else:
            print_success("All GBAtemp cheats are in local DB")

        return not_in_local[:20]  # Return first 20 for summary

    def generate_markdown_report(self, gbatemp_missing=None):
        """Generate a markdown summary report"""
        report = []
        report.append(f"# 📊 nx-cheats-db Analysis Report\n")
        report.append(f"**Generated:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S UTC')}\n")
        report.append(f"**Repository:** [{REPO_URL}]({REPO_URL})\n")

        # Statistics Section
        report.append("## 📈 Statistics\n")
        report.append("| Metric | Count |")
        report.append("|--------|-------|")
        report.append(f"| Total Titles | {self.stats['total_titles']} |")
        report.append(f"| Total Build IDs | {self.stats['total_build_ids']} |")
        report.append(f"| ✓ Mapped | {self.stats['mapped_build_ids']} |")
        report.append(f"| ✗ Unmapped | {self.stats['unmapped_build_ids']} |")
        report.append(f"| Coverage | {self.stats['mapped_build_ids']/max(self.stats['total_build_ids'],1)*100:.1f}% |")
        report.append(f"| Titles with Issues | {self.stats['titles_with_issues']} |")
        if self.stats['gbatemp_not_in_db'] > 0:
            report.append(f"| GBAtemp Not in DB | {self.stats['gbatemp_not_in_db']} |")
        report.append("")

        # Action Items Section
        report.append("## 🔧 Action Items\n")

        # Priority 1: Missing Mappings
        if self.missing_mappings:
            report.append("### Priority 1: Add Missing Version Mappings\n")
            report.append(f"**{len(self.missing_mappings)} titles** have build IDs in cheats but not in versions.json\n")
            report.append("\n**Top 20 titles needing attention:**\n")
            report.append("| Title ID | Title | Missing Build IDs |")
            report.append("|----------|-------|-------------------|")

            count = 0
            for title_id, build_ids in sorted(self.missing_mappings.items()):
                if count >= 20:
                    break
                title = self.titles.get(title_id, f"Title {title_id}")
                # Truncate build IDs list if too long
                bids_str = ", ".join(build_ids[:3])
                if len(build_ids) > 3:
                    bids_str += f" (+{len(build_ids)-3} more)"
                report.append(f"| `{title_id}` | {title[:40]} | {bids_str} |")
                count += 1

            report.append(f"\n<details>\n<summary>View all {len(self.missing_mappings)} titles</summary>\n\n")
            for title_id, build_ids in sorted(self.missing_mappings.items()):
                title = self.titles.get(title_id, f"Title {title_id}")
                bids_str = ", ".join(f"`{bid}`" for bid in build_ids)
                report.append(f"- **{title}** (`{title_id}`): {bids_str}")
            report.append("\n</details>\n")
            report.append("**Fix:** Run `python fix_missing_mappings.py` to auto-generate these mappings\n")
            report.append("")

        # Priority 2: Formatting Issues
        if self.formatting_issues:
            report.append("### Priority 2: Fix Formatting Issues\n")
            report.append(f"**{len(self.formatting_issues)} titles** have formatting problems\n")
            report.append("\n")
            for title_id, issues in list(self.formatting_issues.items())[:10]:
                title = self.titles.get(title_id, title_id)
                report.append(f"#### `{title_id}` - {title}\n")
                for issue in issues[:5]:
                    report.append(f"- {issue}")
                if len(issues) > 5:
                    report.append(f"- ... and {len(issues)-5} more issues")
                report.append("")
            report.append("")

        # Priority 3: GBAtemp Missing
        if gbatemp_missing:
            report.append("### Priority 3: Add Missing GBAtemp Cheats\n")
            report.append(f"**{len(gbatemp_missing)} cheat versions** from GBAtemp are not in this database\n")
            report.append("\n**First 20 missing:**\n")
            for item in gbatemp_missing[:20]:
                report.append(f"- {item}")
            report.append("\n")

        # Commands Section
        report.append("## 🛠️ Quick Fix Commands\n")
        report.append("```bash\n")
        report.append("# Fix missing mappings\n")
        report.append("python fix_missing_mappings.py\n")
        report.append("\n# Commit changes\n")
        report.append("git add versions.json versions/\n")
        report.append("git commit -m 'fix: add missing version mappings'\n")
        report.append("git push\n")
        report.append("```\n")

        # Detailed Statistics
        report.append("## 📋 Detailed Statistics by Title\n")
        report.append("| Title ID | Title | Cheats Files | Mapped | Unmapped |")
        report.append("|----------|-------|--------------|--------|----------|")

        for title_id in sorted(self.cheats_file.keys())[:50]:
            title = self.titles.get(title_id, "Unknown")[:30]
            total_bids = len([k for k in self.cheats_file[title_id].keys() if k != "attribution"])
            mapped = len(self.version_mappings.get(title_id, {}))
            unmapped = total_bids - mapped
            report.append(f"| `{title_id}` | {title} | {total_bids} | {mapped} | {unmapped} |")

        if len(self.cheats_file) > 50:
            report.append(f"\n*... and {len(self.cheats_file)-50} more titles*\n")

        return "\n".join(report)

    def generate_json_report(self):
        """Generate a JSON report for machine processing"""
        return {
            "generated_at": datetime.now().isoformat() + "Z",
            "statistics": self.stats,
            "missing_mappings": {
                k: v for k, v in sorted(
                    self.missing_mappings.items(),
                    key=lambda x: len(x[1]),
                    reverse=True
                )
            },
            "formatting_issues": dict(self.formatting_issues),
            "gbatemp_not_in_db": self.stats["gbatemp_not_in_db"]
        }

    def run(self):
        """Run all analysis"""
        print(f"""
╔═══════════════════════════════════════════════════════════╗
║   nx-cheats-db Analysis Tool                              ║
║   Analyzing cheat database for issues and improvements    ║
╚═══════════════════════════════════════════════════════════╝
""")

        self.load_data()
        self.analyze_missing_mappings()
        self.analyze_formatting_issues()
        self.fetch_gbatemp_cheats()
        gbatemp_missing = self.compare_with_gbatemp()

        # Generate reports
        print_header("Generating Reports")

        markdown_report = self.generate_markdown_report(gbatemp_missing)
        json_report = self.generate_json_report()

        # Save reports
        with open("cheats_db_summary.md", "w", encoding="utf-8") as f:
            f.write(markdown_report)
        print_success("Saved cheats_db_summary.md")

        with open("cheats_db_report.json", "w", encoding="utf-8") as f:
            json.dump(json_report, f, indent=2)
        print_success("Saved cheats_db_report.json")

        # Print summary to console
        print_header("Summary")
        print(f"  Total Titles:     {self.stats['total_titles']}")
        print(f"  Total Build IDs:  {self.stats['total_build_ids']}")
        print(f"  ✓ Mapped:         {self.stats['mapped_build_ids']}")
        print(f"  ✗ Unmapped:       {self.stats['unmapped_build_ids']}")
        print(f"  Coverage:         {self.stats['mapped_build_ids']/max(self.stats['total_build_ids'],1)*100:.1f}%")
        print(f"  Titles w/ Issues:  {self.stats['titles_with_issues']}")
        print()
        print_success("Analysis complete!")

        # Print top action items
        if self.missing_mappings:
            print()
            print_warning(f"TOP ACTION ITEM: Add {len(self.missing_mappings)} missing version mappings")
            print_info("Run: python fix_missing_mappings.py")

if __name__ == "__main__":
    analyzer = CheatsDatabaseAnalyzer()
    analyzer.run()
