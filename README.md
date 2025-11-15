# LeetCode → Notion Importer

**Automatically import top company-specific LeetCode questions into Notion databases.**

This tool pulls the most frequently asked interview questions from LeetCode (by company and time window) and syncs them to organized Notion databases, making interview prep efficient and trackable.

![Status](https://img.shields.io/badge/status-production%20ready-brightgreen)
![Performance](https://img.shields.io/badge/upload-2.07x%20faster-blue)
![Data Accuracy](https://img.shields.io/badge/accuracy-100%25-green)

---

## ✨ Features

- 🎯 **Smart Data Extraction** - Fetches top N questions by frequency (default: 100)
- 📊 **Multi-Window Support** - Tracks 30d, 90d, and 180d frequency trends
- ⚡ **Fast Delta Sync** - Only updates changed questions (247× faster re-uploads)
- 🔄 **Batch Processing** - Concurrent uploads with automatic retry
- 🏢 **Multi-Company** - Parallel processing for multiple companies
- 🧪 **Dry-Run Mode** - Test without affecting your Notion database
- 📈 **Performance Tracking** - Built-in analytics and verification

---

## 📋 Requirements

### LeetCode
- **LeetCode Premium** subscription (required for company-specific data)

### Notion
- Notion account (free or paid)
- Notion integration token (see Setup below)
- Database(s) shared with your integration

### System
- Python 3.9 or higher
- 200MB disk space (for browser automation)

---

## 🚀 Quick Start

### 1. Clone and Install

```bash
# Clone repository
git clone <your-repo-url>
cd LeetCodeCompanyNotionBoard

# Create virtual environment
python3 -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install core dependencies
pip install python-dotenv playwright notion-client
playwright install chromium

# Install topic analysis dependencies (optional)
pip install plotly scipy matplotlib pandas
```

### 2. Configure Notion Integration

**A. Create Notion Integration**
1. Go to https://www.notion.so/my-integrations
2. Click **"+ New integration"**
3. Name it (e.g., "LeetCode Importer")
4. Select your workspace
5. Copy the **Internal Integration Token** (starts with `secret_`)

**B. Create Notion Database(s)**

You have two options:

**Option 1: Use Template (Recommended)**
1. Duplicate this template: [LeetCode Tracker Template](https://trapezoidal-dash-803.notion.site/280d7fb3bcac806d9bf3fb0fbee58281?v=280d7fb3bcac81deb102000c7bf08238)
2. Share it with your integration (••• menu → Add connections)
3. Copy the database ID from URL

**Option 2: Create from Scratch**

Create a database with these properties:
- **Name** (Title) - Question title with number
- **Difficulty** (Select) - Easy, Medium, Hard
- **Topic Tags** (Multi-select) - Dynamic tags
- **Freq 30d** (Number) - 30-day frequency
- **Freq 90d** (Number) - 90-day frequency
- **Freq 180d** (Number) - 180-day frequency
- **Acceptance Rate** (Number) - Acceptance percentage
- **Company** (Select) - Company name
- **Last Attempted** (Date) - Track your progress

### Combined Database Schema

If you plan to use `combine_companies.py` to aggregate questions across multiple companies, create a **separate database** with these additional/modified properties:

**Required Properties:**
- **Name** (Title) - Question title with number (hyperlinked)
- **Difficulty** (Select) - Mode across contributing companies
- **Topic Tags** (Multi-select) - Union of all tags
- **Freq 30d Avg** (Number) - Average 30-day frequency
- **Freq 90d Avg** (Number) - Average 90-day frequency
- **Freq 180d Avg** (Number) - Average 180-day frequency
- **Acceptance Rate** (Number) - Mean acceptance percentage
- **Companies** (Multi-select) - Which companies contributed this question
- **Relevance Score** (Number) - Weighted score (0.5×30d + 0.3×90d + 0.2×180d)
- **Last Computed** (Date) - When aggregation last ran

**Optional User Columns (preserved during updates):**
- **Last Attempted** (Date)
- **Notes** (Text/Rich Text)
- Any other custom properties you add

**Note on Question Retention:**
When a question falls off the top N list, its frequencies and relevance score are zeroed (not deleted). This preserves your notes and custom data while pushing it down the list, and the question will automatically reappear if it returns to the top N.

**C. Get Database ID**

From your Notion database URL:
```
https://notion.so/280d7fb3bcac806d9bf3fb0fbee58281?v=...
                    ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
                    This is your database ID
```

### 3. Configure Environment

**A. Create `.env` file**

```bash
cp .env.example .env  # Or create manually
```

Add to `.env`:
```bash
# Notion Integration Token (required)
NOTION_TOKEN=secret_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx

# Optional: Default database (fallback)
NOTION_DATABASE_ID=your_default_database_id

# Path to company mapping file
NOTION_DB_MAP_FILE=./dbmap.json

# Data storage location
COMPANIES_ROOT=companies
```

**B. Create `dbmap.json`**

Map company display names to LeetCode slugs and Notion databases:

```json
{
  "Meta": {
    "db": "your_meta_database_id",
    "slug": "facebook"
  },
  "Amazon": {
    "db": "your_amazon_database_id",
    "slug": "amazon"
  },
  "Google": {
    "db": "your_google_database_id",
    "slug": "google"
  }
}
```

**Key Points:**
- **Display Name** (e.g., "Meta") - Used in commands
- **`db`** - Notion database ID (32 chars, no hyphens)
- **`slug`** - LeetCode company identifier (usually lowercase)

Common LeetCode slugs:
- Meta → `facebook`
- Amazon → `amazon`
- Google → `google`
- Microsoft → `microsoft`
- Apple → `apple`

### 4. Run Your First Import

```bash
# Test with dry-run (no changes to Notion)
python pull_and_import.py --companies Meta --dry-run

# If successful, run for real
python pull_and_import.py --companies Meta
```

**What happens:**
1. Browser opens (Chromium) → **Login to LeetCode manually**
2. Script pulls top 100 questions for each time window (30d, 90d, 180d)
3. Data is consolidated and uploaded to Notion
4. Complete! Check your Notion database

---

## 📖 Usage Guide

### Basic Commands

**Import single company:**
```bash
python pull_and_import.py --companies Meta
```

**Import multiple companies:**
```bash
python pull_and_import.py --companies "Meta,Amazon,Google"
```

**Custom number of questions:**
```bash
python pull_and_import.py --companies Meta --top-n 200
```

**Specific date:**
```bash
python pull_and_import.py --companies Meta --date 2025-10-03
```

**Dry-run (test mode):**
```bash
python pull_and_import.py --companies Meta --dry-run
```

### Advanced Usage

**Pull only (no upload):**
```bash
python leetcode_pull.py --companies Meta --top-n 100
```

**Generate master file:**
```bash
python generate_master.py --company Meta --date 2025-10-03
```

**Upload from existing data:**
```bash
python upload.py --companies Meta --date 2025-10-03
```

**Combine multiple companies into a unified board from locally generated Master files:**
```bash
# Aggregates top questions across multiple companies (requires NOTION_COMBINED_DATABASE_ID)
python combine_companies.py --companies "Meta,Google,Amazon"

# Optional arguments:
# --score weighted    # Weighted scoring: 0.5*30d + 0.3*90d + 0.2*180d (default)
# --score simple      # Simple averaging across all windows
# --top 100           # Limit to top N questions (default: 150)
```
*Note: The combined database requires [additional properties](#combined-database-schema) beyond the per-company database schema.*

**Analyze topic distributions and generate visualizations:**
```bash
# Analyze single company
python topic_analysis.py --company Meta --window 180d

# Analyze multiple companies with comparisons
python topic_analysis.py --companies "Meta,Google,Amazon" --window 180d --compare

# Auto-open visualizations in browser
python topic_analysis.py --company Meta --window 180d --auto-open

# Custom output directory
python topic_analysis.py --companies "Meta,Amazon" --output custom_output/ --compare

# Available options:
# --window {30d,90d,180d}  # Time window to analyze (default: 180d)
# --compare                # Include statistical comparisons between companies
# --auto-open              # Automatically open HTML visualizations in browser
# --output <dir>           # Custom output directory (default: topic_analysis_output/)
```
*Note: Requires additional dependencies: `pip install plotly scipy matplotlib pandas`*

---

## 📂 Project Structure

```
LeetCodeCompanyNotionBoard/
├── companies/                     # Data storage (gitignored)
│   └── Meta/
│       └── 2025-10-03/
│           ├── 30d.json          # 30-day window questions
│           ├── 90d.json          # 90-day window questions
│           ├── 180d.json         # 180-day window questions
│           ├── master.json       # Consolidated data
│           └── .upload_state.json # Delta sync state
│
├── topic_analysis_output/         # Topic analysis visualizations (gitignored)
│   ├── Meta_180d_topic_analysis.html
│   ├── Combined_180d_topic_analysis.html
│   └── statistical_comparison.txt
│
├── leetcode_pull.py              # Step 1: Pull from LeetCode
├── generate_master.py            # Step 2: Generate master file
├── upload.py                     # Step 3: Upload to Notion
├── pull_and_import.py            # Complete workflow (1+2+3)
├── notion_company_snapshot_import.py # Incremental per-company imports
├── combine_companies.py          # Aggregate multiple companies
├── topic_analysis.py             # Analyze topic distributions
│
├── upload_adapter.py             # Upload abstraction layer
│
├── .env                          # Credentials (gitignored)
├── dbmap.json                    # Company mappings (gitignored)
├── README.md                     # This file
└── CLAUDE.md                     # Development guide
```

---

## 🔧 Configuration Options

### Environment Variables (.env)

```bash
# Required
NOTION_TOKEN=secret_xxx              # Notion integration token

# Optional
NOTION_DATABASE_ID=xxx               # Fallback database ID for per-company imports (Used in lieu of dbmap.json)
NOTION_COMBINED_DATABASE_ID=xxx      # Database ID for combined multi-company board
NOTION_DB_MAP_FILE=./dbmap.json     # Company mapping file
COMPANIES_ROOT=companies             # Data storage directory
PULL_TOP_N=100                       # Default top N questions
PULL_THROTTLE_MS=400                 # Delay between requests
```

### Company Mapping (dbmap.json)

```json
{
  "DisplayName": {
    "db": "notion_database_id_32chars",
    "slug": "leetcode_company_slug"
  }
}
```

---

## 🎯 How It Works

### 3-Step Workflow

```
┌─────────────────────────────────────────────────────────────┐
│  Step 1: Pull from LeetCode                                 │
│  • Opens browser (manual login required)                    │
│  • Fetches ALL questions from GraphQL API                   │
│  • Sorts by frequency descending (client-side)              │
│  • Keeps top N questions (default: 100)                     │
│  • Saves to: companies/{Company}/{Date}/{30d,90d,180d}.json │
└─────────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────┐
│  Step 2: Generate Master File                               │
│  • Consolidates 3 windows into single file                  │
│  • Computes checksum for delta detection                    │
│  • Calculates overall scores                                │
│  • Saves to: companies/{Company}/{Date}/master.json         │
└─────────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────┐
│  Step 3: Upload to Notion                                   │
│  • Loads previous upload state                              │
│  • Compares checksums (delta detection)                     │
│  • If changed: computes per-question operations             │
│  • Batch upload (5 workers, 10 ops/batch)                   │
│  • Saves new upload state                                   │
│  • Preserves user columns (Last Attempted, Notes, etc.)     │
└─────────────────────────────────────────────────────────────┘
```

**🔒 User Data Preservation:**
Updates only modify script-managed properties (frequencies, difficulty, tags, etc.). Your personal data like **Last Attempted**, **Notes**, and any custom fields remain untouched.

### Data Flow

```
LeetCode API (all questions)
    ↓ [client-side sort by frequency]
30d.json, 90d.json, 180d.json (top N)
    ↓ [consolidate + checksum]
master.json (single source of truth)
    ↓ [delta detection]
Upload operations (only changes)
    ↓ [batch + concurrent]
Notion Database

    ↓ [topic analysis - read only]
Interactive HTML visualizations + statistical reports
```

### Delta Sync Logic

**First Upload:**
- Compares master.json with existing Notion records
- Uploads all new/changed questions

**Subsequent Uploads (Same Data):**
- Compares checksum: `sha256:abc123...`
- If match → Skip (0.99s, 247× faster!)
- If changed → Upload only differences

---

## 📊 Performance

### Benchmarks (Meta - 132 questions)

| Operation | Old System | New System | Improvement |
|-----------|------------|------------|-------------|
| **Full Upload** | 245s | 118s | **2.07× faster** |
| **Delta Sync (no changes)** | 245s | 1s | **247× faster** |
| **File Size** | 396KB | 64KB | **84% smaller** |
| **Multi-Company (3)** | 735s | 118s | **6.2× faster** |

### Typical Runtime

**Single Company (Meta):**
```
Pull from LeetCode:  ~60s (includes manual login)
Generate master:     <1s
Upload to Notion:    ~118s (first time) or ~1s (delta sync)
─────────────────────────────────────────────────────
Total:               ~3 minutes (first time)
                     ~1 minute (subsequent runs)
```

**Three Companies (Parallel):**
```
Pull (sequential):   ~180s (manual login × 1)
Generate masters:    <1s
Upload (parallel):   ~118s
─────────────────────────────────────────────────────
Total:               ~5 minutes (first time)
                     ~3 minutes (subsequent runs)
```

---

## ❓ Troubleshooting

### Login Issues

**Problem:** "Login not detected (timeout)"
- **Solution:** Make sure you're logged into LeetCode **Premium**
- The browser waits 120 seconds for login
- Look for `LEETCODE_SESSION` and `csrftoken` cookies

### Database ID Not Found

**Problem:** "No database ID found for company 'Meta'"
- **Solution:** Check `dbmap.json` has the company configured:
  ```json
  {
    "Meta": {
      "db": "your_database_id",
      "slug": "facebook"
    }
  }
  ```

### Missing Questions

**Problem:** High-frequency questions missing from results
- **Cause:** Fixed in latest version (client-side sorting)
- **Solution:** Re-pull data with updated `leetcode_pull.py`
- Questions are now guaranteed to be top N by frequency

### Slow Upload

**Problem:** Upload takes longer than expected
- **Normal:** ~0.6s per question (Notion API limitation)
- **Optimization:** Use delta sync for re-uploads (247× faster)
- **Check:** Network connection and Notion API status

### Rate Limiting

**Problem:** "Too many requests" errors
- **Solution:** Increase `--throttle-ms` (default: 400)
  ```bash
  python leetcode_pull.py --companies Meta --throttle-ms 1000
  ```

### Topic Analysis Missing Dependencies

**Problem:** "ModuleNotFoundError: No module named 'plotly'"
- **Solution:** Install topic analysis dependencies
  ```bash
  pip install plotly scipy matplotlib pandas
  ```

### Topic Analysis No Data Found

**Problem:** "No snapshot data found for company 'Meta'"
- **Cause:** No snapshot JSONs exist in `companies/Meta/` directory
- **Solution:** Run data pull first:
  ```bash
  python pull_and_import.py --companies Meta
  python topic_analysis.py --company Meta --window 180d
  ```

---

## 🔐 Security & Privacy

### Credentials
- **Never commit** `.env` or `dbmap.json` (gitignored by default)
- Store Notion tokens securely
- Rotate integration tokens periodically

### Data Storage
- All data stored locally in `companies/` folder
- No cloud storage or external services
- Safe to delete snapshots (re-pullable)

### LeetCode Login
- Browser automation requires manual login each time
- No credentials stored (cookies expire after session)
- Uses incognito mode (no persistent state)

---

## 🤝 Contributing

Found a bug or want to add a feature?

1. Check existing issues
2. Create a new issue describing the problem/feature
3. Fork and create a pull request

---

## 📚 Additional Resources

### Documentation
- [CLAUDE.md](CLAUDE.md) - Developer guide and architecture
- [IMPLEMENTATION_SUMMARY.md](IMPLEMENTATION_SUMMARY.md) - Optimization details
- [performance_evaluation.md](performance_evaluation.md) - Performance analysis

### Notion Resources
- [Notion API Documentation](https://developers.notion.com/)
- [Create Integration](https://www.notion.so/my-integrations)
- [Database Template](https://trapezoidal-dash-803.notion.site/280d7fb3bcac806d9bf3fb0fbee58281)

### LeetCode Resources
- [LeetCode Premium](https://leetcode.com/subscribe/)
- [Company Tag Lists](https://leetcode.com/company/)

---

## 📝 License

This project is for personal use. Please review LeetCode's Terms of Service regarding data scraping and automation.

---

## 🙏 Acknowledgments

Built with:
- [Playwright](https://playwright.dev/) - Browser automation
- [Notion SDK](https://github.com/ramnes/notion-sdk-py) - Notion API client
- [Python-dotenv](https://github.com/theskumar/python-dotenv) - Environment management

---

## 📞 Support

**Questions or Issues?**
1. Check [Troubleshooting](#-troubleshooting) section
2. Review [CLAUDE.md](CLAUDE.md) for architecture details
3. Open an issue with:
   - Error messages
   - Steps to reproduce
   - Python version
   - Operating system

**Quick Test:**
```bash
# Verify setup
python pull_and_import.py --companies Meta --dry-run

# Check versions
python --version
pip list | grep -E "(notion|playwright|dotenv)"
```

---

**Happy Interview Prep! 🚀**
