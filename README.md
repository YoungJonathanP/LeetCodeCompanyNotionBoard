# LeetCode → Notion Importer

This project pulls company-specific question frequency data from **LeetCode** and imports it into **Notion databases**.  

It supports:
- Per-company Notion DB mapping via `dbmap.json`
- Aliasing (`Meta` → `facebook`, etc.)
- Automatic snapshot organization into dated folders
- Dry-run modes for safe testing

Requirements:
- Leetcode premium subscription
- Notion account
---

## 📂 Project Structure

```
.
├── companies/
│   ├── Meta/
│   │   └── 2025-09-29/
│   │       ├── 30d.json
│   │       ├── 90d.json
│   │       └── 180d.json
│   ├── Amazon/
│   └── Microsoft/
├── dbmap.json
├── notion_company_snapshot_import.py
├── leetcode_pull.py
├── pull_and_import.py
├── .env
└── .gitignore
```

---

## ⚙️ Configuration

### `.env`
Copy `.env.example` into `.env` and fill in your values:

```bash
# https://developers.notion.com/docs/authorization#set-up-the-auth-flow-for-a-public-integration
NOTION_TOKEN=secret_xxxxx
# Default DB (fallback if no mapping found)
NOTION_DATABASE_ID=
# Path to the company → Notion DB map
NOTION_DB_MAP=./dbmap.json
```

### `dbmap.json`
Defines **aliases** (local folder names) → LeetCode slugs + Notion DB IDs.

```json
{
  "Meta":        { "db": "00000000000000000000000000000000", "slug": "facebook" },
  "Amazon":      { "db": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "slug": "amazon" },
  "Microsoft":   { "db": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb", "slug": "microsoft" },
  "Google":      { "db": "cccccccccccccccccccccccccccccccc", "slug": "google" },
  "Apple":       { "db": "dddddddddddddddddddddddddddddddd", "slug": "apple" },
  "Netflix":     { "db": "eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee", "slug": "netflix" },
  "Airbnb":      { "db": "ffffffffffffffffffffffffffffffff", "slug": "airbnb" },
  "Uber":        { "db": "11111111111111111111111111111111", "slug": "uber" },
  "Lyft":        { "db": "22222222222222222222222222222222", "slug": "lyft" },
  "Stripe":      { "db": "33333333333333333333333333333333", "slug": "stripe" }
}
```

### Notion properties to create
- **Name** (Title)
- **Difficulty** (Select)
- **Topic Tags** (Multi-select)
- **Freq 30d** (Number)
- **Freq 90d** (Number)
- **Freq 180d** (Number)
- **Acceptance Rate** (Number)
- **Last Attempted** (Date)

Or duplicate my Template
https://trapezoidal-dash-803.notion.site/280d7fb3bcac806d9bf3fb0fbee58281?v=280d7fb3bcac81deb102000c7bf08238

Each db board will only hold questions from that company
TODO: Unified board with questions common to sub-boards

Share the database with your integration
- Open the database → ••• menu (top right) → Add connections.
- Pick your integration (e.g., “LeetCode Importer”).
- If the DB lives inside a page that’s restricted, also make sure the parent page is shared with the integration or that the DB is directly shared (Notion respects the chain of permissions).

Add the db to the `dbmap.json` for that company

The id is the first value after the default url
https://www.notion.so/280d7fb3bcac806d9bf3fb0fbee58281?v=280d7fb3bcac81deb102000c7bf08238

So the id in this instance would be `280d7fb3bcac806d9bf3fb0fbee58281`

---

## ▶️ Usage
```
pip install python-dotenv playwright notion-client
playwright install chromium
```

### 1. Pull + Import (combined)
Testing via Dry Run:
```bash
python pull_and_import.py --companies "Meta,Amazon" --dry-run-import
```

Run It
```bash
python pull_and_import.py --companies "Meta,Amazon"
```

Runs both:
- Pull from LeetCode
- Import into Notion

`--dry-run-import` previews changes without updating Notion.

---

### 2. Pull questions from LeetCode (does not import)
```bash
python leetcode_pull.py --companies "Meta,Amazon,Microsoft"
```

- Opens an **incognito Chromium** browser
- Prompts you to login to **LeetCode**
- Pulls company-specific JSONs
- Saves them under:

```
companies/{Alias}/{YYYY-MM-DD}/{30d.json,90d.json,180d.json}
```

If today’s folder already contains all 3 JSONs, the pull is skipped.

---

### 3. Import into Notion (Only if questions have been pulled)
```bash
python notion_company_snapshot_import.py ./companies/Meta --company Meta
```

- Updates the mapped Notion DB for that company
- Sets frequencies per window
- Updates acceptance rate, tags, difficulty
- Respects aliases (Meta → facebook)

---


## 🧹 .gitignore

We ignore:
- Company snapshot data (`companies/`)
- Local configs
- Python caches

```gitignore
# Python
__pycache__/
*.pyc

# Env files
.env
.env.local

# Company snapshot data
companies/
dbmap.json
```

---

## 🚀 Notes

- The `--companies` argument should match **keys in dbmap.json** (aliases).
- The script automatically handles LeetCode slug differences (e.g. `Meta` → `facebook`).
- Always login when prompted (cookies expire quickly).
- Dry-run modes are available for both pull + import.
