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

# Install dependencies
pip install python-dotenv playwright notion-client
playwright install chromium
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

**Performance testing:**
```bash
python baseline_test.py companies/Meta/2025-10-03
```

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
├── leetcode_pull.py              # Step 1: Pull from LeetCode
├── generate_master.py            # Step 2: Generate master file
├── upload.py                     # Step 3: Upload to Notion
├── pull_and_import.py            # Complete workflow (1+2+3)
│
├── upload_adapter.py             # Upload abstraction layer
├── baseline_test.py              # Performance testing
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
NOTION_DATABASE_ID=xxx               # Fallback database ID
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
└─────────────────────────────────────────────────────────────┘
```

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
