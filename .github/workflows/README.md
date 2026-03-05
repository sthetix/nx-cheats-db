# GitHub Actions for nx-cheats-db

This directory contains automated workflows for maintaining the cheat database.

## Workflows

### 📊 Cheats Database Summary (`cheats-db-summary.yml`)

**Triggers:**
- Push to `cheats/`, `versions/`, or `versions.json`
- Pull requests affecting these files
- Weekly schedule (Sundays at midnight UTC)
- Manual trigger via workflow_dispatch

**What it does:**
1. Analyzes the entire cheat database
2. Checks for missing version mappings
3. Validates formatting
4. Fetches latest cheats from GBAtemp
5. Compares local DB with GBAtemp
6. Generates summary report (`cheats_db_summary.md`)
7. Posts summary as comment on PRs

**Outputs:**
- `cheats_db_summary.md` - Human-readable report
- `cheats_db_report.json` - Machine-readable data

## Scripts

### 🐍 Python Scripts (`.github/scripts/`)

#### `analyze_db.py`
Main analysis script that:
- Loads and parses all cheat and version files
- Finds missing version→build_id mappings
- Detects formatting issues
- Fetches data from GBAtemp API
- Generates markdown and JSON reports

#### `fix_missing_mappings.py`
Automated fix script that:
- Scans cheats for unmapped build IDs
- Generates proper version numbers
- Updates versions.json and version files
- Supports dry-run mode
- Can auto-commit changes

## Usage

### Local Development

```bash
# Analyze current database
python .github/scripts/analyze_db.py

# Fix missing mappings (dry run first)
python .github/scripts/fix_missing_mappings.py --dry-run

# Apply fixes
python .github/scripts/fix_missing_mappings.py
```

### GitHub Action

To manually trigger the analysis workflow:
1. Go to Actions tab in GitHub
2. Select "Cheats Database Summary"
3. Click "Run workflow" button

## Report Sections

### Statistics
- Total titles
- Total build IDs
- Mapped vs unmapped count
- Coverage percentage
- Titles with issues

### Action Items (Priority Order)

**Priority 1:** Missing Version Mappings
- Lists all build IDs in cheats but not in versions.json
- Quick fix command provided
- Shows top 20 titles, expandable for full list

**Priority 2:** Formatting Issues
- Empty cheat content
- Malformed cheat codes
- Invalid JSON structure

**Priority 3:** GBAtemp Not in DB
- Cheats available on GBAtemp but missing locally
- Lists first 20 missing entries

## Contributing

When you see action items in the report:

1. **Missing mappings:** Run `python .github/scripts/fix_missing_mappings.py`
2. **Formatting issues:** Manually review and fix the specific files
3. **GBAtemp additions:** Manually add the missing cheats with proper attribution

## Data Flow

```
┌─────────────┐
│  GBAtemp    │
│  API        │
└──────┬──────┘
       │
       ▼
┌─────────────────────────────────────┐
│  analyze_db.py                     │
│  - Fetches from GBAtemp            │
│  - Compares with local DB          │
│  - Generates report                │
└──────┬──────────────────────────────┘
       │
       ▼
┌─────────────────────────────────────┐
│  fix_missing_mappings.py           │
│  - Finds unmapped build IDs        │
│  - Generates version numbers       │
│  - Updates version files           │
└─────────────────────────────────────┘
```
