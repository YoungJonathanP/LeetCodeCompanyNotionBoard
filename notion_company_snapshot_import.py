#!/usr/bin/env python3
import argparse
import json
import os
import sys
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any, Tuple
from datetime import datetime
import time

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
PROP_COMPANY        = "Company"             # Select
COMPANIES_ROOT      = "companies"

URL_PREFIX = "https://leetcode.com/problems/"

WINDOW_FILENAMES = {
    "30d": "30d.json",
    "90d": "90d.json",
    "180d": "180d.json",
}

# numeric comparison tolerance
EPS = 1e-6

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
        title = (
            meta.get("title")
            or map30.get(slug, (None,))[0]
            or map90.get(slug, (None,))[0]
            or map180.get(slug, (None,))[0]
            or slug.replace("-", " ").title()
        )
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

def get_db_schema(notion: Client, database_id: str) -> dict:
    return notion.databases.retrieve(database_id=database_id)

def _existing_option_names(schema: dict, prop_name: str) -> set:
    prop = (schema.get("properties") or {}).get(prop_name)
    if not prop:
        return set()
    ptype = prop.get("type")
    if ptype not in ("select", "multi_select"):
        return set()
    return {o.get("name") for o in prop[ptype].get("options", []) if o.get("name")}

def batch_add_options(notion: Client, database_id: str, prop_name: str, missing: List[str], schema: dict):
    if not missing:
        return
    prop = schema["properties"].get(prop_name)
    if not prop:
        return
    ptype = prop["type"]   # "select" or "multi_select"
    current = prop[ptype].get("options", [])
    new_options = current + [{"name": v} for v in missing]
    notion.databases.update(
        database_id=database_id,
        properties={prop_name: {ptype: {"options": new_options}}}
    )
    # also update our local schema cache so later calls see it
    prop[ptype]["options"] = new_options

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

def build_title_rich_text(row: ProblemRow) -> List[dict]:
    text_content = build_title_text(row.frontend_id, row.title)
    return [{"type": "text", "text": {"content": text_content, "link": {"url": row.url}}}]

def get_pages_index(notion: Client, database_id: str, company: Optional[str] = None) -> Dict[str, dict]:
    """
    Return map: title_text -> {
        'id': page_id,
        'freq30': float|None,
        'freq90': float|None,
        'freq180': float|None,
        'acc': float|None,
    }
    """
    pages: Dict[str, dict] = {}
    start_cursor = None
    while True:
        query_kwargs = {
            "database_id": database_id,
            "start_cursor": start_cursor,
            "page_size": 100,
        }
        if company:
            query_kwargs["filter"] = {
                "property": PROP_COMPANY,
                "select": { "equals": company }
            }
        resp = notion.databases.query(**query_kwargs)
        for page in resp.get("results", []):
            props = page.get("properties", {})
            name_prop = props.get(PROP_TITLE, {})
            title_text = ""
            if name_prop.get("type") == "title":
                rich = name_prop.get("title") or []
                title_text = "".join(rt.get("plain_text", "") for rt in rich)
            if not title_text:
                continue

            def num(prop):
                p = props.get(prop, {})
                return p.get("number")

            pages[title_text] = {
                "id": page["id"],
                "freq30": num(PROP_FREQ_30),
                "freq90": num(PROP_FREQ_90),
                "freq180": num(PROP_FREQ_180),
                "acc": num(PROP_ACCEPT_RATE),
            }
        if not resp.get("has_more"):
            break
        start_cursor = resp.get("next_cursor")
    return pages

def page_props(row: ProblemRow, company: Optional[str]) -> dict:
    """
    Build properties payload for update/create operations.

    IMPORTANT: Only includes properties managed by this script. User-managed properties
    (e.g., Last Attempted, Notes, custom fields) are intentionally excluded and will be
    preserved during updates since Notion's API only modifies explicitly provided properties.
    """
    props = {
        PROP_TITLE: {"title": build_title_rich_text(row)},
        PROP_FREQ_30: {"number": float(row.freq_30)},
        PROP_FREQ_90: {"number": float(row.freq_90)},
        PROP_FREQ_180: {"number": float(row.freq_180)},
    }
    # Only update Acceptance Rate when JSON provides a value; else leave existing as-is
    if row.acceptance_rate_pct is not None:
        props[PROP_ACCEPT_RATE] = {"number": float(row.acceptance_rate_pct)}
    if row.difficulty:
        props[PROP_DIFFICULTY] = {"select": {"name": row.difficulty}}
    if row.topic_tags:
        props[PROP_TOPIC_TAGS] = {"multi_select": [{"name": t} for t in row.topic_tags]}
    if company:
        props[PROP_COMPANY] = {"select": {"name": company}}
    return props

def needs_numeric_update(existing_meta: dict, new_props: dict) -> bool:
    """
    existing_meta: {'freq30','freq90','freq180','acc'}
    new_props: Notion 'properties' payload we would send
    Returns True iff any of the four numeric fields changed (with EPS tolerance).
    """
    def new_num(key: str) -> Optional[float]:
        p = new_props.get(key, {})
        return p.get("number")

    checks = [
        (existing_meta.get("freq30"), new_num(PROP_FREQ_30)),
        (existing_meta.get("freq90"), new_num(PROP_FREQ_90)),
        (existing_meta.get("freq180"), new_num(PROP_FREQ_180)),
        (existing_meta.get("acc"),    new_num(PROP_ACCEPT_RATE)),
    ]
    for old, new in checks:
        # if new is None, we're intentionally not updating that field
        if new is None:
            continue
        if old is None and new is not None:
            return True
        if old is not None and new is not None and abs((old or 0.0) - (new or 0.0)) > EPS:
            return True
    return False

def zero_missing_windows(notion: Client, page_id: str, missing_props: List[str], dry_run: bool):
    if not missing_props:
        return
    props = {p: {"number": 0.0} for p in missing_props}
    if dry_run:
        log(f"[DRY-RUN] ZERO {missing_props} for page {page_id}")
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

# ---------------------------
# CLI
# ---------------------------

def main():
    load_dotenv()
    start_ts = time.perf_counter()  # <-- start stopwatch

    # counters
    created_count = 0
    updated_count = 0
    zeroed_pages = 0
    altered_pages_ids = set()  # unique pages touched (created/updated/zeroed)

    ap = argparse.ArgumentParser(description="Import companies/{company}/{date}/{30d,90d,180d}.json into Notion.")
    ap.add_argument("path", help=
                    "Path to a company folder (./companies/meta) OR a date folder (./companies/meta/2025-09-29). "
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

    # ---- Batch ensure options ONCE (Difficulty / Topic Tags / Company) ----
    schema = get_db_schema(notion, dbid)
    need_diffs = {r.difficulty for r in master.values() if r.difficulty}
    need_tags  = set()
    for r in master.values():
        for t in r.topic_tags:
            need_tags.add(t)
    need_company = {company} if company else set()

    missing_diffs = list(need_diffs - _existing_option_names(schema, PROP_DIFFICULTY))
    missing_tags  = list(need_tags  - _existing_option_names(schema, PROP_TOPIC_TAGS))
    missing_comp  = list(need_company - _existing_option_names(schema, PROP_COMPANY))

    # At most 3 DB updates total:
    batch_add_options(notion, dbid, PROP_DIFFICULTY, missing_diffs, schema)
    batch_add_options(notion, dbid, PROP_TOPIC_TAGS,  missing_tags,  schema)
    batch_add_options(notion, dbid, PROP_COMPANY,     missing_comp,  schema)

    # ---- Build pages index (with current numeric fields) ----
    pages_idx = get_pages_index(notion, dbid, company)

    # create/update from snapshot (diff-only updates)
    for row in master.values():
        title_key = build_title_text(row.frontend_id, row.title)
        page_meta = pages_idx.get(title_key)
        props = page_props(row, company)

        if page_meta:
            if needs_numeric_update(page_meta, props):
                if args.dry_run:
                    log(f"[DRY-RUN] UPDATE {title_key} | 30d={row.freq_30}, 90d={row.freq_90}, 180d={row.freq_180}, acc={row.acceptance_rate_pct}")
                    updated_count += 1
                    altered_pages_ids.add(page_meta["id"] or title_key)
                else:
                    notion.pages.update(page_id=page_meta["id"], properties=props)
                    updated_count += 1
                    altered_pages_ids.add(page_meta["id"])
            else:
                # unchanged numerics → skip update
                continue
        else:
            # CREATE
            if args.dry_run:
                log(f"[DRY-RUN] CREATE {title_key}")
                created_count += 1
                altered_pages_ids.add(title_key)  # use title as a stand-in ID in dry-run
            else:
                created = notion.pages.create(parent={"database_id": dbid}, properties=props)
                created_count += 1
                created_id = created["id"]
                altered_pages_ids.add(created_id)
                pages_idx[title_key] = {
                    "id": created_id,
                    "freq30": props.get(PROP_FREQ_30, {}).get("number"),
                    "freq90": props.get(PROP_FREQ_90, {}).get("number"),
                    "freq180": props.get(PROP_FREQ_180, {}).get("number"),
                    "acc": props.get(PROP_ACCEPT_RATE, {}).get("number"),
                }

    # zero missing windows for already-existing pages (single update per page)
    title_to_row = { build_title_text(r.frontend_id, r.title): r for r in master.values() }

    for title_text, meta in pages_idx.items():
        page_id = meta["id"]
        row = title_to_row.get(title_text)

        # Determine which windows are missing in this snapshot
        if row:
            missing = []
            if row.freq_30 == 0.0: missing.append(PROP_FREQ_30)
            if row.freq_90 == 0.0: missing.append(PROP_FREQ_90)
            if row.freq_180 == 0.0: missing.append(PROP_FREQ_180)
        else:
            # Entirely absent this snapshot → all three are considered missing
            missing = [PROP_FREQ_30, PROP_FREQ_90, PROP_FREQ_180]

        if not missing:
            continue

        # Check current values; if all already zero/None, skip the update
        currently_nonzero = []
        for p in missing:
            curr = None
            if p == PROP_FREQ_30:  curr = meta.get("freq30")
            elif p == PROP_FREQ_90: curr = meta.get("freq90")
            elif p == PROP_FREQ_180: curr = meta.get("freq180")
            if curr is not None and abs(curr) > EPS:
                currently_nonzero.append(p)

        if not currently_nonzero:
            # nothing to zero on this page
            continue

        if args.dry_run:
            log(f"[DRY-RUN] ZERO {currently_nonzero} for {title_text}")
            zeroed_pages += 1
            altered_pages_ids.add(page_id or title_text)
        else:
            notion.pages.update(
                page_id=page_id,
                properties={p: {"number": 0.0} for p in currently_nonzero}
            )
            zeroed_pages += 1
            altered_pages_ids.add(page_id)


    # ---- Summary with elapsed seconds ----
    elapsed = time.perf_counter() - start_ts
    log(f"Done.")
    log(f"Summary: created={created_count}, updated={updated_count}, zeroed_pages={zeroed_pages}")
    log(f"Altered rows (unique pages affected): {len(altered_pages_ids)}")
    log(f"Elapsed: {elapsed:.2f} seconds")

if __name__ == "__main__":
    main()