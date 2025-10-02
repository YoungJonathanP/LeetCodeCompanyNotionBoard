#!/usr/bin/env python3
import argparse
import json
import os
import sys
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any, Tuple
from datetime import datetime

from notion_client import Client
from dotenv import load_dotenv

# ---------------------------
# Notion property names (edit if your DB uses different names)
# ---------------------------
PROP_TITLE          = "Name"                # Title (hyperlinked "983. Title")
PROP_DIFFICULTY     = "Difficulty"          # Select
PROP_ACCEPT_RATE    = "Acceptance Rate"     # Number (percent)
PROP_TOPIC_TAGS     = "Topic Tags"          # Multi-select
PROP_LAST_ATTEMPTED = "Last Attempted"      # Date (user-managed)
PROP_FREQ_30        = "Freq 30d"            # Number
PROP_FREQ_90        = "Freq 90d"            # Number
PROP_FREQ_180       = "Freq 180d"           # Number
PROP_COMPANY        = "Company"             # Select (NEW)
COMPANIES_ROOT      = "companies"


URL_PREFIX = "https://leetcode.com/problems/"

WINDOW_FILENAMES = {
    "30d": "30d.json",
    "90d": "90d.json",
    "180d": "180d.json",
}

@dataclass
class ProblemRow:
    title: str
    slug: str
    url: str
    frontend_id: Optional[str] = None
    difficulty: Optional[str] = None
    acceptance_rate_pct: Optional[float] = None
    topic_tags: List[str] = field(default_factory=list)
    # Window scores (0 if missing for that window)
    freq_30: float = 0.0
    freq_90: float = 0.0
    freq_180: float = 0.0

def log(msg: str): print(msg, file=sys.stdout)
def warn(msg: str): print(f"[WARN] {msg}", file=sys.stderr)

# ---------------------------
# JSON parsing
# ---------------------------

def load_json(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def parse_questions(doc: dict) -> List[dict]:
    """Return a list of raw question dicts from the given JSON shape."""
    try:
        return doc["data"]["favoriteQuestionList"]["questions"]
    except Exception as e:
        raise SystemExit("JSON does not have data.favoriteQuestionList.questions[]") from e

def normalize_acceptance(ac: Any) -> Optional[float]:
    if ac is None:
        return None
    try:
        x = float(ac)
        # If <= 1 assume it's a ratio; convert to percent
        return round(x * 100, 2) if x <= 1.0 else round(x, 2)
    except Exception:
        return None

def rows_from_window(doc: dict) -> Dict[str, Tuple[str, float]]:
    out: Dict[str, Tuple[str, float]] = {}
    for q in parse_questions(doc):
        slug = q.get("titleSlug")
        title = q.get("title")
        if not slug or not title:
            continue
        freq = q.get("frequency")
        try:
            freq_val = float(freq) if freq is not None else 0.0
        except Exception:
            freq_val = 0.0
        out[slug] = (title, freq_val)
    return out

def build_master_index(doc30: Optional[dict], doc90: Optional[dict], doc180: Optional[dict]) -> Dict[str, ProblemRow]:
    """Union of all slugs across the three windows and fill window scores (0 where absent)."""
    idx: Dict[str, ProblemRow] = {}

    map30 = rows_from_window(doc30) if doc30 else {}
    map90 = rows_from_window(doc90) if doc90 else {}
    map180 = rows_from_window(doc180) if doc180 else {}

    all_slugs = set().union(set(map30.keys()), set(map90.keys()), set(map180.keys()))

    def find_metadata(slug: str) -> dict:
        for d in (doc30, doc90, doc180):
            if not d: 
                continue
            for q in parse_questions(d):
                if q.get("titleSlug") == slug:
                    return q
        return {}

    for slug in all_slugs:
        meta = find_metadata(slug)
        title = meta.get("title") or \
                map30.get(slug, (None,))[0] or \
                map90.get(slug, (None,))[0] or \
                map180.get(slug, (None,))[0] or \
                slug.replace("-", " ").title()
        url = URL_PREFIX + slug + "/"
        diff = meta.get("difficulty")
        if isinstance(diff, str):
            diff = diff.title()
        ac_pct = normalize_acceptance(meta.get("acRate"))
        tags = [t.get("name") for t in (meta.get("topicTags") or []) if isinstance(t, dict) and t.get("name")]
        frontend_id = meta.get("questionFrontendId")
        if frontend_id is not None:
            frontend_id = str(frontend_id)

        idx[slug] = ProblemRow(
            title=title,
            slug=slug,
            url=url,
            frontend_id=frontend_id,
            difficulty=diff,
            acceptance_rate_pct=ac_pct,
            topic_tags=tags,
            freq_30=map30.get(slug, (None, 0.0))[1],
            freq_90=map90.get(slug, (None, 0.0))[1],
            freq_180=map180.get(slug, (None, 0.0))[1],
        )
    return idx

# ---------------------------
# Notion helpers
# ---------------------------

def notion_client_from_env() -> Client:
    token = os.environ.get("NOTION_TOKEN")
    if not token:
        raise RuntimeError("Missing NOTION_TOKEN (set in .env)")
    return Client(auth=token)

def get_db_id(arg_db: Optional[str]) -> str:
    db = arg_db or os.environ.get("NOTION_DATABASE_ID")
    if not db:
        raise RuntimeError("Missing database id (use --database-id or NOTION_DATABASE_ID)")
    return db

def build_title_text(frontend_id: Optional[str], title: str) -> str:
    return f"{frontend_id}. {title}" if frontend_id else title

def ensure_select_option(notion: Client, database_id: str, prop_name: str, value: str):
    try:
        db = notion.databases.retrieve(database_id=database_id)
        prop = db.get("properties", {}).get(prop_name)
        if not prop:
            return
        ptype = prop["type"]
        if ptype not in ("select", "multi_select"):
            return
        options = prop[ptype].get("options", [])
        if any(o.get("name") == value for o in options):
            return
        notion.databases.update(
            database_id=database_id,
            properties={prop_name: {ptype: {"options": options + [{"name": value}]}}}
        )
    except Exception as e:
        warn(f"Failed to ensure option '{value}' for {prop_name}: {e}")

def ensure_multi_select(notion: Client, database_id: str, prop_name: str, values: List[str]):
    for v in values:
        ensure_select_option(notion, database_id, prop_name, v)

def build_title_rich_text(row: ProblemRow) -> List[dict]:
    text_content = build_title_text(row.frontend_id, row.title)
    return [{"type": "text", "text": {"content": text_content, "link": {"url": row.url}}}]

def get_all_pages_by_title(notion: Client, database_id: str) -> Dict[str, str]:
    """Return map: title_text -> page_id for all pages (paginated)."""
    pages: Dict[str, str] = {}
    start_cursor = None
    while True:
        resp = notion.databases.query(
            database_id=database_id,
            start_cursor=start_cursor,
            page_size=100,
        )
        for page in resp.get("results", []):
            props = page.get("properties", {})
            name_prop = props.get(PROP_TITLE, {})
            title_text = ""
            if name_prop.get("type") == "title":
                rich = name_prop.get("title") or []
                # concatenate all title pieces (we set only one, but be safe)
                title_text = "".join(rt.get("plain_text","") for rt in rich)
            if title_text:
                pages[title_text] = page["id"]
        if not resp.get("has_more"):
            break
        start_cursor = resp.get("next_cursor")
    return pages

def page_props(row: ProblemRow, company: Optional[str]) -> dict:
    props = {
        PROP_TITLE: {"title": build_title_rich_text(row)},
        PROP_FREQ_30: {"number": float(row.freq_30)},
        PROP_FREQ_90: {"number": float(row.freq_90)},
        PROP_FREQ_180: {"number": float(row.freq_180)},
    }
    if row.difficulty:
        props[PROP_DIFFICULTY] = {"select": {"name": row.difficulty}}
    # Only update Acceptance Rate when JSON provides a value; else leave existing as-is
    if row.acceptance_rate_pct is not None:
        props[PROP_ACCEPT_RATE] = {"number": float(row.acceptance_rate_pct)}
    if row.topic_tags:
        props[PROP_TOPIC_TAGS] = {"multi_select": [{"name": t} for t in row.topic_tags]}
    if company:
        props[PROP_COMPANY] = {"select": {"name": company}}
    return props

def create_or_update_page(notion: Client, database_id: str, page_id: Optional[str], row: ProblemRow, company: Optional[str], dry_run: bool):
    # Ensure select options
    if row.difficulty:
        ensure_select_option(notion, database_id, PROP_DIFFICULTY, row.difficulty)
    if row.topic_tags:
        ensure_multi_select(notion, database_id, PROP_TOPIC_TAGS, row.topic_tags)
    if company:
        ensure_select_option(notion, database_id, PROP_COMPANY, company)

    props = page_props(row, company)
    if dry_run:
        action = "UPDATE" if page_id else "CREATE"
        log(f"[DRY-RUN] {action} {build_title_text(row.frontend_id, row.title)} | 30d={row.freq_30}, 90d={row.freq_90}, 180d={row.freq_180}"
            + (f" | Company={company}" if company else ""))
        return

    if page_id:
        notion.pages.update(page_id=page_id, properties=props)
    else:
        notion.pages.create(parent={"database_id": database_id}, properties=props)

def set_missing_window_to_zero(notion: Client, page_id: str, prop_name: str, *, dry_run: bool):
    props = {prop_name: {"number": 0.0}}
    if dry_run:
        log(f"[DRY-RUN] ZERO {prop_name} for page {page_id}")
    else:
        notion.pages.update(page_id=page_id, properties=props)

# ---------------------------
# Filesystem helpers
# ---------------------------
def is_date_folder(name: str) -> bool:
    try:
        datetime.strptime(name, "%Y-%m-%d")
        return True
    except ValueError:
        return False

def pick_latest_date_folder(company_dir: str) -> Optional[str]:
    candidates = [d for d in os.listdir(company_dir) if os.path.isdir(os.path.join(company_dir, d)) and is_date_folder(d)]
    if not candidates: return None
    latest = max(candidates)  # YYYY-MM-DD sorts lexicographically
    return os.path.join(company_dir, latest)

def load_window_jsons(base_dir: str) -> Tuple[Optional[dict], Optional[dict], Optional[dict]]:
    # base_dir must contain 30d.json, 90d.json, 180d.json (any subset OK)
    d30 = os.path.join(base_dir, WINDOW_FILENAMES["30d"])
    d90 = os.path.join(base_dir, WINDOW_FILENAMES["90d"])
    d180= os.path.join(base_dir, WINDOW_FILENAMES["180d"])
    doc30 = load_json(d30) if os.path.exists(d30) else None
    doc90 = load_json(d90) if os.path.exists(d90) else None
    doc180= load_json(d180) if os.path.exists(d180) else None
    return doc30, doc90, doc180

# ---------------------------
# DB mapping
# ---------------------------

def _load_mapping() -> Dict[str, Any]:
    """Load mapping from NOTION_DB_MAP (JSON string or file path) or NOTION_DB_MAP_FILE."""
    env_map_str = os.environ.get("NOTION_DB_MAP")
    env_map_file = os.environ.get("NOTION_DB_MAP_FILE")

    # Prefer explicit file if set
    if env_map_file and os.path.exists(env_map_file):
        try:
            with open(env_map_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            warn(f"Failed to read NOTION_DB_MAP_FILE: {e}")

    # If NOTION_DB_MAP is set, accept either inline JSON or a path to a .json
    if env_map_str:
        try:
            if env_map_str.lower().endswith(".json") and os.path.exists(env_map_str):
                with open(env_map_str, "r", encoding="utf-8") as f:
                    return json.load(f)
            return json.loads(env_map_str)
        except Exception as e:
            warn(f"Failed to parse NOTION_DB_MAP (as JSON or file): {e}")

    return {}

def _coerce_db_id(entry: Any) -> Optional[str]:
    """Accept either a string DB id or an object with {'db': '...'}."""
    if isinstance(entry, str):
        return entry
    if isinstance(entry, dict):
        dbid = entry.get("db")
        if isinstance(dbid, str) and dbid:
            return dbid
    return None

def resolve_database_id_for_company(company: Optional[str], cli_db: Optional[str]) -> str:
    """
    Priority:
      1) --database-id (explicit override)
      2) NOTION_DB_MAP/NOTION_DB_MAP_FILE → match for company (supports flat or nested schema)
      3) NOTION_DATABASE_ID (fallback)
    """
    if cli_db:
        return cli_db

    mapping = _load_mapping()

    if company and mapping:
        # Try exact, lower, and title-case keys
        for key in (company, company.lower(), company.title()):
            if key in mapping:
                dbid = _coerce_db_id(mapping[key])
                if dbid:
                    return dbid
                else:
                    warn(f"Mapping for '{key}' exists but does not contain a valid DB id.")

    fallback = os.environ.get("NOTION_DATABASE_ID")
    if fallback:
        return fallback

    raise RuntimeError(
        "No database id found. Provide --database-id, or set NOTION_DB_MAP / NOTION_DB_MAP_FILE, "
        "or NOTION_DATABASE_ID."
    )
# def resolve_database_id_for_company(company: Optional[str], cli_db: Optional[str]) -> str:
#     """
#     Priority:
#       1) --database-id (explicit override)
#       2) --db-map (JSON file mapping), or NOTION_DB_MAP (JSON string) → use match for company
#       3) NOTION_DATABASE_ID (fallback)
#     """
#     # 1) explicit CLI always wins
#     if cli_db: 
#         return cli_db

#     # 2) env JSON map (string) or file path
#     env_map_str = os.environ.get("NOTION_DB_MAP")  # JSON string (e.g. {"Meta":"xxxx","Google":"yyyy"})
#     env_map_file = os.environ.get("NOTION_DB_MAP_FILE")  # path to JSON file

#     mapping: Dict[str, str] = {}
#     if env_map_str:
#         try:
#             mapping = json.loads(env_map_str)
#         except Exception as e:
#             warn(f"Failed to parse NOTION_DB_MAP JSON: {e}")
#     elif env_map_file and os.path.exists(env_map_file):
#         try:
#             with open(env_map_file, "r", encoding="utf-8") as f:
#                 mapping = json.load(f)
#         except Exception as e:
#             warn(f"Failed to read NOTION_DB_MAP_FILE: {e}")

#     if company and mapping:
#         db = mapping.get(company) or mapping.get(company.lower()) or mapping.get(company.title())
#         if db[db]:
#             return db

#     # 3) final fallback
#     fallback = os.environ.get("NOTION_DATABASE_ID")
#     if not fallback:
#         raise RuntimeError("No database id found. Provide --database-id, or set NOTION_DB_MAP / NOTION_DB_MAP_FILE, or NOTION_DATABASE_ID.")
#     return fallback

# ---------------------------
# CLI
# ---------------------------

def main():
    load_dotenv()
    ap = argparse.ArgumentParser(description="Import companies/{company}/{date}/{30d,90d,180d}.json into Notion.")
    ap.add_argument("path", help=
                    "Path to a company folder (./companies/meta) OR a date folder (./companies/meta/2025-09-29). " \
                    "You may also pass just the company name (e.g., 'Meta') and the script will resolve companies/Meta.")
    ap.add_argument("--company", help="Company name to set in Notion (Select). If omitted, inferred from folder name.")
    ap.add_argument("--database-id", help="Override NOTION_DATABASE_ID")
    ap.add_argument("--dry-run", action="store_true", help="Preview without writing")
    args = ap.parse_args()

    # Resolve path: accept absolute, relative, bare company name, or companies/{company}[/{date}]
    raw = args.path
    # 1) If the user passed an existing path, use it as-is.
    if os.path.exists(raw):
        path = os.path.abspath(raw)
    else:
        # 2) Otherwise, try to resolve under companies/{arg}
        candidate = os.path.join(COMPANIES_ROOT, raw)
        if os.path.exists(candidate):
            path = os.path.abspath(candidate)
        else:
            raise SystemExit(
                f"Path not found: {os.path.abspath(raw)} "
                f"(also tried {os.path.abspath(candidate)})"
            )

    # Detect if user passed a date folder (ends with YYYY-MM-DD) or a company folder
    base_dir = None
    company = args.company
    if os.path.isdir(path) and is_date_folder(os.path.basename(path)):
        # path == company/date
        base_dir = path
        if not company:
            company = os.path.basename(os.path.dirname(path)) or None
    else:
        # path == companies/{company} (pick latest date subfolder)
        latest = pick_latest_date_folder(path)
        if not latest:
            raise SystemExit(f"No date subfolders found under: {path}")
        base_dir = latest
        if not company:
            company = os.path.basename(path)

    if not company:
        warn("No company name resolved; Company property will be omitted.")
    else:
        log(f"Company: {company}")
    log(f"Using snapshot folder: {base_dir}")

    doc30, doc90, doc180 = load_window_jsons(base_dir)
    if not (doc30 or doc90 or doc180):
        raise SystemExit("No window JSONs found (need at least one of 30d.json, 90d.json, 180d.json)")

    master = build_master_index(doc30, doc90, doc180)
    log(f"Collected {len(master)} unique problems.")

    notion = notion_client_from_env()
    dbid = resolve_database_id_for_company(company, args.database_id)
    log(f"Using database: {dbid}")

    existing = get_all_pages_by_title(notion, dbid)

    # create/update from snapshot
    for row in master.values():
        title_key = build_title_text(row.frontend_id, row.title)
        page_id = existing.get(title_key)
        create_or_update_page(notion, dbid, page_id, row, company, dry_run=args.dry_run)

    # zero missing windows for already-existing pages
    # build title->row map for quick lookup
    title_to_row = { build_title_text(r.frontend_id, r.title): r for r in master.values() }

    for title_text, page_id in existing.items():
        row = title_to_row.get(title_text)
        if row:
            missing = []
            if row.freq_30 == 0.0: missing.append(PROP_FREQ_30)
            if row.freq_90 == 0.0: missing.append(PROP_FREQ_90)
            if row.freq_180 == 0.0: missing.append(PROP_FREQ_180)
        else:
            # absent in this snapshot: zero all
            missing = [PROP_FREQ_30, PROP_FREQ_90, PROP_FREQ_180]
        for prop in missing:
            set_missing_window_to_zero(notion, page_id, prop, dry_run=args.dry_run)

    log("Done.")

if __name__ == "__main__":
    main()