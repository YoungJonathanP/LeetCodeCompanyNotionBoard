#!/usr/bin/env python3
import os
import sys
import json
import argparse
import datetime
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Any, Tuple, Set

from dotenv import load_dotenv
from notion_client import Client

# ---------------------------
# Configuration / Property Names
# ---------------------------

# Combined DB property names (edit these to match your Combined Notion DB)
PROP_TITLE           = "Name"                 # Title (hyperlinked "{id}. {Title}")
PROP_SLUG            = "Slug"                 # Rich text (preferred unique key)
PROP_DIFFICULTY      = "Difficulty"           # Select
PROP_TOPIC_TAGS      = "Topic Tags"           # Multi-select
PROP_ACCEPT_RATE     = "Acceptance Rate"      # Number (percent 0..100, optional)
PROP_COMPANIES       = "Companies"            # Multi-select (which companies contributed)
PROP_COMPANY_COUNT   = "Companies Count"      # Number (N selected)
PROP_FREQ30_AVG      = "Freq 30d Avg"         # Number
PROP_FREQ90_AVG      = "Freq 90d Avg"         # Number
PROP_FREQ180_AVG     = "Freq 180d Avg"        # Number
PROP_OVERALL_SCORE   = "Overall Score"        # Number (for sorting)
PROP_LAST_COMBINED   = "Last Combined At"     # Date (now)

URL_PREFIX = "https://leetcode.com/problems/"

COMPANIES_ROOT = os.getenv("COMPANIES_ROOT", "companies")

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
    """Return mapping: slug_or_normtitle -> per-problem dict with window frequencies and metadata.
       Missing files are treated as empty (zeros)."""
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

def find_page_by_slug(notion: Client, database_id: str, slug: Optional[str]) -> Optional[str]:
    if not slug:
        return None
    try:
        resp = notion.databases.query(
            database_id=database_id,
            filter={
                "property": PROP_SLUG,
                "rich_text": {"equals": slug}
            },
            page_size=1,
        )
        results = resp.get("results", [])
        return results[0]["id"] if results else None
    except Exception as e:
        warn(f"Query by slug failed: {e}")
        return None

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

def build_props_for_combined(row: ProblemAgg, include_companies: bool, companies: List[str]) -> dict:
    props = {
        PROP_TITLE: {"title": title_rich_text(row.frontend_id, row.title, row.url)},
        PROP_FREQ30_AVG: {"number": float(row.avg30)},
        PROP_FREQ90_AVG: {"number": float(row.avg90)},
        PROP_FREQ180_AVG: {"number": float(row.avg180)},
        PROP_OVERALL_SCORE: {"number": float(row.score)},
        PROP_COMPANY_COUNT: {"number": len(companies)},
        PROP_LAST_COMBINED: {"date": {"start": datetime.date.today().isoformat()}},
    }
    if row.slug:
        props[PROP_SLUG] = {"rich_text": [{"type": "text", "text": {"content": row.slug}}]}
    if row.difficulty:
        props[PROP_DIFFICULTY] = {"select": {"name": row.difficulty}}
    if row.tags:
        props[PROP_TOPIC_TAGS] = {"multi_select": [{"name": t} for t in sorted(row.tags)]}
    if row.acceptance is not None:
        props[PROP_ACCEPT_RATE] = {"number": float(row.acceptance)}
    if include_companies and companies:
        props[PROP_COMPANIES] = {"multi_select": [{"name": c} for c in companies]}
    return props

def upsert_combined_page(
    notion: Client,
    database_id: str,
    row: ProblemAgg,
    include_companies: bool,
    companies: List[str],
    dry_run: bool = False,
):
    # Ensure select options exist
    if row.difficulty:
        ensure_select_option(notion, database_id, PROP_DIFFICULTY, row.difficulty)
    if row.tags:
        for t in row.tags:
            ensure_select_option(notion, database_id, PROP_TOPIC_TAGS, t)
    if include_companies:
        for c in companies:
            ensure_select_option(notion, database_id, PROP_COMPANIES, c)

    props = build_props_for_combined(row, include_companies, companies)

    # Try find by slug first, then by title
    page_id = find_page_by_slug(notion, database_id, row.slug)
    if not page_id:
        title_text = f"{row.frontend_id}. {row.title}" if row.frontend_id else row.title
        page_id = find_page_by_title(notion, database_id, title_text)

    if dry_run:
        action = "UPDATE" if page_id else "CREATE"
        log(f"[DRY-RUN] {action} "
            f"{row.slug or row.title} | avg30={row.avg30:.2f}, avg90={row.avg90:.2f}, avg180={row.avg180:.2f}, "
            f"score={row.score:.2f}")
        return

    if page_id:
        notion.pages.update(page_id=page_id, properties=props)
    else:
        notion.pages.create(parent={"database_id": database_id}, properties=props)

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
    ap = argparse.ArgumentParser(description="Combine selected companies into a single Top-N Notion DB based on averaged frequencies.")
    ap.add_argument("--dbmap", default=os.getenv("NOTION_DB_MAP_FILE", "./dbmap.json"), help="Path to dbmap.json (display -> {db, slug})")
    ap.add_argument("--companies", required=True, help='Comma-separated display names matching dbmap.json keys, e.g. "Meta, Google"')
    ap.add_argument("--root", default=os.getenv("COMPANIES_ROOT", "companies"))
    ap.add_argument("--date", help="YYYY-MM-DD (default: latest per company)")
    ap.add_argument("--combined-db", default=os.getenv("COMBINED_DATABASE_ID"), help="Combined Notion database ID (or set COMBINED_DATABASE_ID)")
    ap.add_argument("--score", choices=["simple","weighted"], default="simple", help="Scoring method for ranking top N")
    ap.add_argument("--top", type=int, default=100, help="Top N rows to upsert (default 100)")
    ap.add_argument("--dry-run", action="store_true", help="Preview without writing to Notion")
    args = ap.parse_args()

    if not args.combined_db:
        raise SystemExit("Missing --combined-db or COMBINED_DATABASE_ID.")

    dbmap = parse_dbmap(Path(args.dbmap))
    # Resolve selected companies (must exist as keys in dbmap)
    selected = [x.strip() for x in args.companies.split(",") if x.strip()]
    missing = [c for c in selected if c not in dbmap]
    if missing:
        known = ", ".join(sorted(dbmap.keys()))
        raise SystemExit(f"Companies not in dbmap: {missing}
Known: {known}")

    rows, N, used_dirs = combine_from_snapshots(
        companies=selected,
        dbmap=dbmap,
        root=Path(args.root),
        date=args.date,
        score_mode=args.score,
    )

    log(f"Combined {len(rows)} unique problems across {N} companies.")
    if used_dirs:
        for c, p in used_dirs.items():
            log(f"  {c}: {p}")

    topN = rows[: args.top]

    notion = notion_client_from_env()
    include_companies = db_has_property(notion, args.combined_db, PROP_COMPANIES)

    # Upsert
    for i, row in enumerate(topN, 1):
        upsert_combined_page(
            notion,
            args.combined_db,
            row,
            include_companies=include_companies,
            companies=selected,
            dry_run=args.dry_run,
        )

    log(f"Done. {'(dry-run)' if args.dry_run else ''}")

if __name__ == "__main__":
    main()
