# Import LeetCode JSON → Notion (enhanced)

Adds:
- **Linked Title**: `Name` shows "questionFrontendId. Title" and hyperlinks to problem URL
- **Frequency** column (numeric score from JSON)
- **Acceptance Rate** column (percent number)
- **Topic Tags** column (multi-select)
- Sorting and limiting

## Notion properties to create
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

## .env
```
NOTION_TOKEN=secret_xxx
NOTION_DATABASE_ID=xxxxxxxxxxxxxxxxxxxxxxxxxxxx
```

## Run
```
pip install notion-client python-dotenv

# Dry run: Use a company folder (auto-picks latest date subfolder):
# python notion_company_snapshot_import.py ./{folder for company} --{company to tag in notion} --dry-run
python notion_company_snapshot_import.py ./Meta --company Meta --dry-run

# Real run: Will store data in notion
python notion_company_snapshot_import.py ./Meta --company Meta

# To run by a specific folder
python notion_company_snapshot_import.py ./meta/2025-09-29 --company Meta --dry-run
python notion_company_snapshot_import.py ./meta/2025-09-29 --company Meta

# Override DB id explicitly (ignores mapping + env):
python notion_company_snapshot_import.py ./meta --company Meta --database-id xxxxxxxxxxxxxxxxxxxxxx
```
