#!/usr/bin/env python3
import os
import sys
import json
import argparse
import datetime
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Any, Tuple, Set

from dotenv import load_dotenv
from notion_client import Client

# ---------------------------
# Configuration / Property Names
# ---------------------------

# Combined DB property names (matching user's database schema)
PROP_TITLE           = "Name"                 # Title (hyperlinked "{frontendId}. {Title}")
PROP_DIFFICULTY      = "Difficulty"           # Select (mode or highest frequency source)
PROP_TOPIC_TAGS      = "Topic Tags"           # Multi-select (union across companies)
PROP_ACCEPT_RATE     = "Acceptance Rate"      # Number (percent 0..100, static per question)
PROP_COMPANIES       = "Companies"            # Multi-select (which companies contributed)
PROP_FREQ30_AVG      = "Freq 30d Avg"         # Number (average with missing = 0)
PROP_FREQ90_AVG      = "Freq 90d Avg"         # Number (average with missing = 0)
PROP_FREQ180_AVG     = "Freq 180d Avg"        # Number (average with missing = 0)
PROP_RELEVANCE_SCORE = "Relevance Score"      # Number (0.5*30d + 0.3*90d + 0.2*180d)
PROP_LAST_COMPUTED   = "Last Computed"        # Date (when row was last updated)

URL_PREFIX = "https://leetcode.com/problems/"

COMPANIES_ROOT = os.getenv("COMPANIES_ROOT", "companies")

# Numeric comparison tolerance
EPS = 1e-6

# ---------------------------
# Data models
# ---------------------------

@dataclass
class ProblemAgg:
    slug: Optional[str] = None
    title: str = ""
    url: Optional[str] = None
    frontend_id: Optional[str] = None

    sum30: float = 0.0
    sum90: float = 0.0
    sum180: float = 0.0

    diff_counts: Dict[str, int] = field(default_factory=lambda: {"Easy":0, "Medium":0, "Hard":0})
    tags: Set[str] = field(default_factory=set)

    ar_sum: float = 0.0
    ar_cnt: int = 0

    companies: Set[str] = field(default_factory=set)

    # derived
    avg30: float = 0.0
    avg90: float = 0.0
    avg180: float = 0.0
    score: float = 0.0
    difficulty: Optional[str] = None
    acceptance: Optional[float] = None

# ---------------------------
# Utilities
# ---------------------------

def log(msg: str): print(msg, file=sys.stdout)
def warn(msg: str): print(f"[WARN] {msg}", file=sys.stderr)

def normalize_title(t: str) -> str:
    return " ".join(t.strip().lower().split())

def pick_difficulty(diff_counts: Dict[str, int]) -> Optional[str]:
    # Mode with tie-breaker Hard > Medium > Easy
    maxc = max(diff_counts.values()) if diff_counts else 0
    if maxc <= 0:
        return None
    for d in ["Hard", "Medium", "Easy"]:
        if diff_counts.get(d, 0) == maxc:
            return d
    return None

def load_json(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def latest_date_folder(company_dir: Path) -> Optional[Path]:
    if not company_dir.exists():
        return None
    dated = [d for d in company_dir.iterdir() if d.is_dir()]
    dated = sorted(dated, key=lambda p: p.name, reverse=True)
    return dated[0] if dated else None

def parse_dbmap(path: Path) -> Dict[str, Dict[str, str]]:
    with open(path, "r", encoding="utf-8") as f:
        m = json.load(f)
    for k, v in m.items():
        if not isinstance(v, dict) or "db" not in v or "slug" not in v:
            raise SystemExit(f"dbmap.json entry invalid for '{k}': expected {{'db':'...','slug':'...'}}")
    return m

# ---------------------------
# Snapshot JSON parsing
# ---------------------------

def extract_questions(doc: dict) -> List[dict]:
    try:
        return doc["data"]["favoriteQuestionList"]["questions"]
    except Exception:
        return []

def normalize_acceptance(ac: Any) -> Optional[float]:
    if ac is None:
        return None
    try:
        x = float(ac)
        return round(x*100, 2) if x <= 1.0 else round(x, 2)
    except Exception:
        return None

def read_company_snapshot(company_display: str, date_dir: Path) -> Dict[str, dict]:
    """
    Read master.json from company date directory.
    Returns mapping: slug_or_normtitle -> per-problem dict with window frequencies and metadata.

    If master.json doesn't exist, falls back to reading 30d/90d/180d.json files.
    """
    master_path = date_dir / "master.json"

    # Prefer master.json if it exists
    if master_path.exists():
        try:
            master = load_json(master_path)
            questions = master.get("questions", {})
            out = {}

            for slug, q in questions.items():
                key = slug or normalize_title(q.get("title", ""))
                if not key:
                    continue

                diff = q.get("difficulty")
                if isinstance(diff, str):
                    diff = diff.title()

                out[key] = {
                    "slug": q.get("slug"),
                    "title": q.get("title", ""),
                    "url": q.get("url"),
                    "frontend_id": str(q.get("frontend_id")) if q.get("frontend_id") is not None else None,
                    "difficulty": diff,
                    "topic_tags": q.get("topic_tags", []),
                    "ac": q.get("acceptance_rate"),
                    "freq_30": float(q.get("freq_30d", 0)),
                    "freq_90": float(q.get("freq_90d", 0)),
                    "freq_180": float(q.get("freq_180d", 0)),
                }

            return out
        except Exception as e:
            warn(f"{company_display}: failed to load master.json from {date_dir}: {e}")
            # Fall through to legacy method

    # Legacy: read from 30d/90d/180d.json files
    files = {
        "30d": date_dir / "30d.json",
        "90d": date_dir / "90d.json",
        "180d": date_dir / "180d.json",
    }
    docs = {}
    for k, p in files.items():
        if p.exists():
            try:
                docs[k] = load_json(p)
            except Exception as e:
                warn(f"{company_display}: failed to load {p}: {e}")
                docs[k] = None
        else:
            docs[k] = None

    # Build union of questions from any available doc
    seen_keys: Set[str] = set()
    out: Dict[str, dict] = {}

    all_q = extract_questions(docs.get("30d") or {}) + extract_questions(docs.get("90d") or {}) + extract_questions(docs.get("180d") or {})
    for q in all_q:
        slug = q.get("titleSlug")
        title = q.get("title","").strip()
        key = slug or normalize_title(title)
        if not key or key in seen_keys:
            continue
        seen_keys.add(key)

        # Build frequencies (default 0.0)
        def freq_from(dockey: str, key_slug: Optional[str], key_title: str) -> float:
            d = docs.get(dockey)
            if not d:
                return 0.0
            for qq in extract_questions(d):
                if key_slug and qq.get("titleSlug") == key_slug:
                    return float(qq.get("frequency") or 0.0)
                if not key_slug and qq.get("title","").strip() == key_title:
                    return float(qq.get("frequency") or 0.0)
            return 0.0

        diff = q.get("difficulty")
        if isinstance(diff, str):
            diff = diff.title()

        tags = []
        for t in (q.get("topicTags") or []):
            if isinstance(t, dict) and t.get("name"):
                tags.append(t["name"])

        out[key] = {
            "slug": slug,
            "title": title or (slug.replace("-"," ").title() if slug else key.title()),
            "url": (URL_PREFIX + slug + "/") if slug else None,
            "frontend_id": str(q.get("questionFrontendId")) if q.get("questionFrontendId") is not None else None,
            "difficulty": diff,
            "topic_tags": tags,
            "ac": normalize_acceptance(q.get("acRate")),
            "freq_30": freq_from("30d", slug, title),
            "freq_90": freq_from("90d", slug, title),
            "freq_180": freq_from("180d", slug, title),
        }

    return out

# ---------------------------
# Notion helpers
# ---------------------------

def notion_client_from_env() -> Client:
    token = os.environ.get("NOTION_TOKEN")
    if not token:
        raise RuntimeError("Missing NOTION_TOKEN (set in .env)")
    return Client(auth=token)

def _existing_option_names(schema: dict, prop_name: str) -> set:
    """Extract existing option names from database schema for a select/multi_select property."""
    prop = (schema.get("properties") or {}).get(prop_name)
    if not prop:
        return set()
    ptype = prop.get("type")
    if ptype not in ("select", "multi_select"):
        return set()
    return {o.get("name") for o in prop[ptype].get("options", []) if o.get("name")}

def batch_add_options(notion: Client, database_id: str, prop_name: str, missing: List[str], schema: dict):
    """
    Add multiple missing options to a select/multi_select property in a SINGLE API call.
    This is vastly more efficient than calling ensure_select_option per value.
    """
    if not missing:
        return
    prop = schema["properties"].get(prop_name)
    if not prop:
        return
    ptype = prop["type"]  # "select" or "multi_select"
    current = prop[ptype].get("options", [])
    new_options = current + [{"name": v} for v in missing]
    try:
        notion.databases.update(
            database_id=database_id,
            properties={prop_name: {ptype: {"options": new_options}}}
        )
        # Update local schema cache
        prop[ptype]["options"] = new_options
    except Exception as e:
        warn(f"Failed to batch-add options to {prop_name}: {e}")

def ensure_select_option(notion: Client, database_id: str, prop_name: str, value: str):
    """
    DEPRECATED: Use batch_add_options() instead for performance.
    This function makes 1 API call per value which is extremely slow.
    """
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

def db_has_property(notion: Client, database_id: str, prop_name: str) -> bool:
    try:
        db = notion.databases.retrieve(database_id=database_id)
        return prop_name in (db.get("properties", {}) or {})
    except Exception as e:
        warn(f"Could not retrieve database schema to check '{prop_name}': {e}")
        return False

def title_rich_text(frontend_id: Optional[str], title: str, url: Optional[str]) -> List[dict]:
    text_content = f"{frontend_id}. {title}" if frontend_id else title
    link = {"url": url} if url else None
    return [{"type": "text", "text": {"content": text_content, "link": link}}]

def find_page_by_title(notion: Client, database_id: str, title_text: str) -> Optional[str]:
    try:
        resp = notion.databases.query(
            database_id=database_id,
            filter={
                "property": PROP_TITLE,
                "title": {"equals": title_text}
            },
            page_size=1,
        )
        results = resp.get("results", [])
        return results[0]["id"] if results else None
    except Exception as e:
        warn(f"Query by title failed: {e}")
        return None

def needs_numeric_update(existing_meta: dict, new_props: dict) -> bool:
    """
    Compare existing numeric values with new ones.
    Returns True iff any numeric field changed (with EPS tolerance).

    existing_meta: {'freq30', 'freq90', 'freq180', 'score'}
    new_props: Notion properties payload
    """
    def new_num(key: str) -> Optional[float]:
        p = new_props.get(key, {})
        return p.get("number")

    checks = [
        (existing_meta.get("freq30"), new_num(PROP_FREQ30_AVG)),
        (existing_meta.get("freq90"), new_num(PROP_FREQ90_AVG)),
        (existing_meta.get("freq180"), new_num(PROP_FREQ180_AVG)),
        (existing_meta.get("score"), new_num(PROP_RELEVANCE_SCORE)),
    ]

    for old, new in checks:
        if new is None:
            continue
        if old is None and new is not None:
            return True
        if old is not None and new is not None and abs((old or 0.0) - (new or 0.0)) > EPS:
            return True

    return False

def build_props_for_combined(row: ProblemAgg, companies_list: List[str]) -> dict:
    """
    Build Notion properties for combined database.

    IMPORTANT: Only includes properties managed by this script. User-managed properties
    (e.g., Last Attempted, Notes, custom fields) are intentionally excluded and will be
    preserved during updates since Notion's API only modifies explicitly provided properties.
    """
    props = {
        PROP_TITLE: {"title": title_rich_text(row.frontend_id, row.title, row.url)},
        PROP_FREQ30_AVG: {"number": round(row.avg30, 2)},
        PROP_FREQ90_AVG: {"number": round(row.avg90, 2)},
        PROP_FREQ180_AVG: {"number": round(row.avg180, 2)},
        PROP_RELEVANCE_SCORE: {"number": round(row.score, 2)},
        PROP_LAST_COMPUTED: {"date": {"start": datetime.date.today().isoformat()}},
    }
    if row.difficulty:
        props[PROP_DIFFICULTY] = {"select": {"name": row.difficulty}}
    if row.tags:
        props[PROP_TOPIC_TAGS] = {"multi_select": [{"name": t} for t in sorted(row.tags)]}
    if row.acceptance is not None:
        props[PROP_ACCEPT_RATE] = {"number": round(row.acceptance, 2)}
    if companies_list:
        props[PROP_COMPANIES] = {"multi_select": [{"name": c} for c in sorted(companies_list)]}
    return props

def upsert_combined_page(
    notion: Client,
    database_id: str,
    row: ProblemAgg,
    companies: List[str],
    existing_pages: Dict[str, dict],
    dry_run: bool = False,
) -> str:
    """
    Upsert a single problem to the combined database with delta detection.

    Args:
        existing_pages: Dict mapping title_text -> {id, freq30, freq90, freq180, score}

    Returns: "created", "updated", or "skipped"

    NOTE: All select/multi-select options must be ensured BEFORE calling this function
    (via batch_add_options in main). This function no longer ensures options per-row for performance.
    """
    title_text = f"{row.frontend_id}. {row.title}" if row.frontend_id else row.title

    if dry_run:
        # Skip all Notion API calls in dry-run mode
        companies_str = ", ".join(sorted(row.companies))
        log(f"[DRY-RUN] CREATE/UPDATE {title_text}")
        log(f"  Companies: {companies_str}")
        log(f"  Freq: 30d={row.avg30:.2f}, 90d={row.avg90:.2f}, 180d={row.avg180:.2f}")
        log(f"  Relevance Score: {row.score:.2f}")
        log("")
        return "created"

    props = build_props_for_combined(row, sorted(row.companies))

    # Check if exists using pre-fetched pages
    page_meta = existing_pages.get(title_text)

    if page_meta:
        # Page exists - check if numeric values changed
        if needs_numeric_update(page_meta, props):
            notion.pages.update(page_id=page_meta["id"], properties=props)
            return "updated"
        else:
            # No changes needed
            return "skipped"
    else:
        # Create new page
        notion.pages.create(parent={"database_id": database_id}, properties=props)
        return "created"

# ---------------------------
# Core combining logic
# ---------------------------

def combine_from_snapshots(
    companies: List[str],
    dbmap: Dict[str, Dict[str, str]],
    root: Path,
    date: Optional[str],
    score_mode: str,
) -> Tuple[List[ProblemAgg], int, Dict[str, Path]]:
    """
    Returns (rows_sorted, N, company_date_dirs)
      - rows_sorted: list of ProblemAgg with averages and score computed
      - N: number of selected companies (divisor for averages; missing data counts as 0)
      - company_date_dirs: mapping company->Path used
    """
    N = len(companies)
    company_date_dirs: Dict[str, Path] = {}
    master: Dict[str, ProblemAgg] = {}

    def add_problem(company: str, prob: dict):
        key = prob.get("slug") or normalize_title(prob["title"])
        acc = master.get(key)
        if not acc:
            acc = ProblemAgg(
                slug=prob.get("slug"),
                title=prob["title"],
                url=prob.get("url"),
                frontend_id=prob.get("frontend_id"),
            )
            master[key] = acc
        # Aggregate
        acc.sum30 += float(prob.get("freq_30") or 0.0)
        acc.sum90 += float(prob.get("freq_90") or 0.0)
        acc.sum180 += float(prob.get("freq_180") or 0.0)
        d = prob.get("difficulty")
        if isinstance(d, str):
            d = d.title()
            if d in acc.diff_counts:
                acc.diff_counts[d] += 1
        tags = prob.get("topic_tags") or []
        for t in tags:
            acc.tags.add(t)
        ar = prob.get("ac")
        if ar is not None:
            try:
                acc.ar_sum += float(ar)
                acc.ar_cnt += 1
            except Exception:
                pass
        acc.companies.add(company)

        # Keep first non-None URL/frontend_id/slug/title
        if not acc.url and prob.get("url"):
            acc.url = prob["url"]
        if not acc.frontend_id and prob.get("frontend_id"):
            acc.frontend_id = prob["frontend_id"]
        if not acc.slug and prob.get("slug"):
            acc.slug = prob["slug"]
        if not acc.title and prob.get("title"):
            acc.title = prob["title"]

    for company in companies:
        cdir = root / company
        dated_dir = (cdir / date) if date else latest_date_folder(cdir)
        if not dated_dir:
            warn(f"{company}: no dated snapshot folder under {cdir}; treating as zeros.")
            # Missing entirely -> contribute zeros by simply not adding sums (division by N applied later)
            continue
        company_date_dirs[company] = dated_dir
        data_map = read_company_snapshot(company, dated_dir)
        for prob in data_map.values():
            add_problem(company, prob)

    # Compute averages & scores
    for acc in master.values():
        acc.avg30 = acc.sum30 / N
        acc.avg90 = acc.sum90 / N
        acc.avg180 = acc.sum180 / N
        # acceptance (mean of available)
        acc.acceptance = (acc.ar_sum / acc.ar_cnt) if acc.ar_cnt else None
        # difficulty mode
        acc.difficulty = pick_difficulty(acc.diff_counts)
        # score
        if score_mode == "weighted":
            acc.score = 0.5*acc.avg30 + 0.3*acc.avg90 + 0.2*acc.avg180
        else:
            acc.score = (acc.avg30 + acc.avg90 + acc.avg180) / 3.0

    # Sort by score desc, then title asc for stability
    rows = sorted(master.values(), key=lambda r: (-r.score, r.title))
    return rows, N, company_date_dirs

# ---------------------------
# CLI
# ---------------------------

def main():
    load_dotenv()
    start_time = time.perf_counter()

    ap = argparse.ArgumentParser(description="Combine selected companies into a Top-N Notion DB with weighted relevance scoring.")
    ap.add_argument("--dbmap", default=os.getenv("NOTION_DB_MAP_FILE", "./dbmap.json"), help="Path to dbmap.json (display -> {db, slug})")
    ap.add_argument("--companies", required=True, help='Comma-separated display names matching dbmap.json keys, e.g. "Meta, Google"')
    ap.add_argument("--root", default=os.getenv("COMPANIES_ROOT", "companies"))
    ap.add_argument("--date", help="YYYY-MM-DD (default: latest per company)")
    ap.add_argument("--combined-db", default=os.getenv("NOTION_COMBINED_DATABASE_ID"), help="Combined Notion database ID (or set NOTION_COMBINED_DATABASE_ID)")
    ap.add_argument("--score", choices=["simple","weighted"], default="weighted", help="Scoring method: weighted (0.5*30d + 0.3*90d + 0.2*180d, default) or simple (mean)")
    ap.add_argument("--top", type=int, default=150, help="Top N rows to upsert (default 150)")
    ap.add_argument("--dry-run", action="store_true", help="Preview without writing to Notion")
    args = ap.parse_args()

    if not args.combined_db:
        raise SystemExit("Missing --combined-db or NOTION_COMBINED_DATABASE_ID in .env")

    dbmap = parse_dbmap(Path(args.dbmap))
    # Resolve selected companies (must exist as keys in dbmap)
    selected = [x.strip() for x in args.companies.split(",") if x.strip()]
    missing = [c for c in selected if c not in dbmap]
    if missing:
        known = ", ".join(sorted(dbmap.keys()))
        raise SystemExit(f"Companies not in dbmap: {missing}\nKnown: {known}")

    log(f"=== Combining Companies ===")
    log(f"Companies: {', '.join(selected)}")
    log(f"Scoring: {args.score}")
    log(f"Top N: {args.top}")
    log("")

    t_start_combine = time.perf_counter()
    rows, N, used_dirs = combine_from_snapshots(
        companies=selected,
        dbmap=dbmap,
        root=Path(args.root),
        date=args.date,
        score_mode=args.score,
    )
    t_combine = time.perf_counter() - t_start_combine

    log(f"Combined {len(rows)} unique problems across {N} companies ({t_combine:.2f}s)")
    if used_dirs:
        for c, p in used_dirs.items():
            log(f"  {c}: {p}")
    log("")

    topN = rows[: args.top]
    log(f"Upserting top {len(topN)} questions to combined database...")
    log("")

    notion = notion_client_from_env()

    # Batch-ensure all select/multi-select options BEFORE upserting (massive performance improvement)
    if not args.dry_run:
        t_start_schema = time.perf_counter()
        log("Retrieving database schema and ensuring options...")
        schema = notion.databases.retrieve(database_id=args.combined_db)

        # Collect all unique values needed
        need_diffs = {r.difficulty for r in topN if r.difficulty}
        need_tags = set()
        for r in topN:
            need_tags.update(r.tags)
        need_companies = set(selected)

        # Compute missing options
        missing_diffs = list(need_diffs - _existing_option_names(schema, PROP_DIFFICULTY))
        missing_tags = list(need_tags - _existing_option_names(schema, PROP_TOPIC_TAGS))
        missing_companies = list(need_companies - _existing_option_names(schema, PROP_COMPANIES))

        # Batch-add (at most 3 API calls total, regardless of how many options)
        batch_add_options(notion, args.combined_db, PROP_DIFFICULTY, missing_diffs, schema)
        batch_add_options(notion, args.combined_db, PROP_TOPIC_TAGS, missing_tags, schema)
        batch_add_options(notion, args.combined_db, PROP_COMPANIES, missing_companies, schema)

        t_schema = time.perf_counter() - t_start_schema
        log(f"Schema updated ({t_schema:.2f}s)")
        log(f"  Added: {len(missing_diffs)} difficulties, {len(missing_tags)} tags, {len(missing_companies)} companies")
        log("")

    # Fetch all existing pages once (for update detection with numeric values)
    t_start_fetch = time.perf_counter()
    existing_pages = {}  # title -> {id, freq30, freq90, freq180, score}
    if not args.dry_run:
        log("Fetching existing records...")
        start_cursor = None
        while True:
            query_params = {"database_id": args.combined_db, "page_size": 100}
            if start_cursor:
                query_params["start_cursor"] = start_cursor

            resp = notion.databases.query(**query_params)
            for page in resp.get("results", []):
                props = page.get("properties", {})
                title_prop = props.get(PROP_TITLE, {})
                if title_prop.get("type") == "title":
                    title_parts = title_prop.get("title", [])
                    if title_parts:
                        title_text = "".join([t.get("text", {}).get("content", "") for t in title_parts])

                        # Extract numeric values for delta detection
                        def num(prop_name):
                            p = props.get(prop_name, {})
                            return p.get("number")

                        existing_pages[title_text] = {
                            "id": page["id"],
                            "freq30": num(PROP_FREQ30_AVG),
                            "freq90": num(PROP_FREQ90_AVG),
                            "freq180": num(PROP_FREQ180_AVG),
                            "score": num(PROP_RELEVANCE_SCORE),
                        }

            if not resp.get("has_more"):
                break
            start_cursor = resp.get("next_cursor")

        t_fetch = time.perf_counter() - t_start_fetch
        log(f"Found {len(existing_pages)} existing records ({t_fetch:.2f}s)")
        log("")

    # Track stats
    created = 0
    updated = 0
    skipped = 0
    zeroed = 0

    # Upsert top N
    t_start_upsert = time.perf_counter()
    topN_titles = set()  # Track which titles we're keeping in top N
    for i, row in enumerate(topN, 1):
        title_text = f"{row.frontend_id}. {row.title}" if row.frontend_id else row.title
        topN_titles.add(title_text)

        result = upsert_combined_page(
            notion,
            args.combined_db,
            row,
            companies=selected,
            existing_pages=existing_pages,
            dry_run=args.dry_run,
        )
        if result == "created":
            created += 1
        elif result == "updated":
            updated += 1
        elif result == "skipped":
            skipped += 1
    t_upsert = time.perf_counter() - t_start_upsert

    # Zero out questions that fell off the top N (preserves user data, notes, etc.)
    t_start_zero = time.perf_counter()
    if not args.dry_run and existing_pages:
        log("")
        log("Zeroing questions that fell off the top N list...")
        for title_text, page_meta in existing_pages.items():
            if title_text not in topN_titles:
                # This page exists but is not in top N anymore - zero its values
                # Only zero if currently non-zero (optimization)
                if (page_meta.get("freq30") or 0) > EPS or \
                   (page_meta.get("freq90") or 0) > EPS or \
                   (page_meta.get("freq180") or 0) > EPS or \
                   (page_meta.get("score") or 0) > EPS:
                    zero_props = {
                        PROP_FREQ30_AVG: {"number": 0.0},
                        PROP_FREQ90_AVG: {"number": 0.0},
                        PROP_FREQ180_AVG: {"number": 0.0},
                        PROP_RELEVANCE_SCORE: {"number": 0.0},
                    }
                    try:
                        notion.pages.update(page_id=page_meta["id"], properties=zero_props)
                        zeroed += 1
                    except Exception as e:
                        warn(f"Failed to zero page '{title_text}': {e}")

        t_zero = time.perf_counter() - t_start_zero
        if zeroed > 0:
            log(f"Zeroed {zeroed} questions ({t_zero:.2f}s)")
    elif args.dry_run:
        # Calculate how many would be zeroed in dry-run
        for title_text in existing_pages.keys():
            if title_text not in topN_titles:
                zeroed += 1
        if zeroed > 0:
            log(f"[DRY-RUN] Would zero {zeroed} questions that fell off top {args.top}")

    total_time = time.perf_counter() - start_time

    log("")
    log(f"=== Summary ===")
    log(f"Created: {created}, Updated: {updated}, Skipped: {skipped}, Zeroed: {zeroed}")
    log(f"Time: combine={t_combine:.2f}s, upsert={t_upsert:.2f}s, total={total_time:.2f}s")
    log(f"{'(dry-run)' if args.dry_run else 'Done!'}")

if __name__ == "__main__":
    main()
