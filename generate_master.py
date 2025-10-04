#!/usr/bin/env python3
"""
Generates master.json from 30d/90d/180d.json files.

Usage:
  python generate_master.py --company Meta                    # Uses latest date
  python generate_master.py --company Meta --date 2025-10-03
  python generate_master.py --all                             # All companies in dbmap.json
  python generate_master.py --all --date 2025-10-03           # All companies for specific date
"""

import argparse
import json
import hashlib
import os
import sys
from pathlib import Path
from datetime import datetime, date
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass, field

from dotenv import load_dotenv

# Constants
COMPANIES_ROOT = os.getenv("COMPANIES_ROOT", "companies")
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
    freq_30: float = 0.0
    freq_90: float = 0.0
    freq_180: float = 0.0

def log(msg: str):
    print(msg, file=sys.stdout)

def warn(msg: str):
    print(f"[WARN] {msg}", file=sys.stderr)

def load_json(path: Path) -> dict:
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

def load_window_jsons(base_dir: Path) -> Tuple[Optional[dict], Optional[dict], Optional[dict]]:
    d30 = base_dir / WINDOW_FILENAMES["30d"]
    d90 = base_dir / WINDOW_FILENAMES["90d"]
    d180 = base_dir / WINDOW_FILENAMES["180d"]
    doc30 = load_json(d30) if d30.exists() else None
    doc90 = load_json(d90) if d90.exists() else None
    doc180 = load_json(d180) if d180.exists() else None
    return doc30, doc90, doc180

def is_date_folder(name: str) -> bool:
    try:
        datetime.strptime(name, "%Y-%m-%d")
        return True
    except ValueError:
        return False

def pick_latest_date_folder(company_dir: Path) -> Optional[Path]:
    """Find the most recent date folder (YYYY-MM-DD format)."""
    if not company_dir.exists():
        return None
    candidates = [d for d in company_dir.iterdir() if d.is_dir() and is_date_folder(d.name)]
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.name)

def compute_checksum(data: dict) -> str:
    """SHA256 of sorted JSON for consistent hashing."""
    canonical = json.dumps(data, sort_keys=True, ensure_ascii=False)
    return f"sha256:{hashlib.sha256(canonical.encode()).hexdigest()[:16]}"

def compute_overall_score(freq_30: float, freq_90: float, freq_180: float) -> float:
    """Simple average of the three frequencies."""
    return round((freq_30 + freq_90 + freq_180) / 3.0, 2)

def generate_master(company: str, date_str: Optional[str], root: Path) -> Path:
    """
    Generate master.json for a company/date.
    Returns path to generated file.
    """
    company_dir = root / company

    if date_str:
        date_dir = company_dir / date_str
        if not date_dir.exists():
            raise FileNotFoundError(f"Date folder not found: {date_dir}")
    else:
        # Pick latest date folder (prefer today's date if it exists)
        today = date.today().isoformat()
        today_dir = company_dir / today
        if today_dir.exists():
            date_dir = today_dir
        else:
            date_dir = pick_latest_date_folder(company_dir)
            if not date_dir:
                raise FileNotFoundError(f"No date folders found in {company_dir}")

    log(f"[{company}] Generating master from {date_dir.name}")

    # Load 3 window files
    doc30, doc90, doc180 = load_window_jsons(date_dir)
    if not (doc30 or doc90 or doc180):
        raise RuntimeError(f"No window JSONs found in {date_dir}")

    # Merge windows
    master_index = build_master_index(doc30, doc90, doc180)

    # Build questions dict for master.json
    questions = {}
    for slug, row in master_index.items():
        questions[slug] = {
            "slug": row.slug,
            "title": row.title,
            "frontend_id": row.frontend_id,
            "url": row.url,
            "difficulty": row.difficulty,
            "acceptance_rate": row.acceptance_rate_pct,
            "topic_tags": row.topic_tags,
            "freq_30d": row.freq_30,
            "freq_90d": row.freq_90,
            "freq_180d": row.freq_180,
            "overall_score": compute_overall_score(row.freq_30, row.freq_90, row.freq_180),
        }

    # Build master document
    master = {
        "metadata": {
            "company": company,
            "date": date_dir.name,
            "generated_at": datetime.utcnow().isoformat() + "Z",
            "total_questions": len(questions),
            "checksum": ""  # Computed after
        },
        "questions": questions
    }

    # Compute checksum (only of questions, not metadata)
    master["metadata"]["checksum"] = compute_checksum(master["questions"])

    # Write master.json
    output_path = date_dir / "master.json"
    output_path.write_text(json.dumps(master, ensure_ascii=False, indent=2), encoding="utf-8")

    log(f"[{company}] ✓ Generated master.json with {len(questions)} questions")
    log(f"[{company}]   Checksum: {master['metadata']['checksum']}")
    log(f"[{company}]   Output: {output_path}")

    return output_path

def load_dbmap(path: str) -> Dict[str, Dict[str, str]]:
    """Load dbmap.json."""
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    for k, v in data.items():
        if not isinstance(v, dict) or "db" not in v or "slug" not in v:
            raise SystemExit(f"dbmap.json entry invalid for '{k}': expected {{'db': '...', 'slug': '...'}}")
    return data

def main():
    load_dotenv()

    parser = argparse.ArgumentParser(
        description="Generate master.json from 30d/90d/180d.json files",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python generate_master.py --company Meta
  python generate_master.py --company Meta --date 2025-10-03
  python generate_master.py --all
  python generate_master.py --all --date 2025-10-03
        """
    )

    parser.add_argument("--company", help="Single company to process")
    parser.add_argument("--companies", help="Comma-separated list of companies")
    parser.add_argument("--all", action="store_true", help="Process all companies in dbmap.json")
    parser.add_argument("--date", help="YYYY-MM-DD (default: latest, prefer today if exists)")
    parser.add_argument("--root", default=os.getenv("COMPANIES_ROOT", "companies"), help="Root companies folder")
    parser.add_argument("--dbmap", default=os.getenv("NOTION_DB_MAP_FILE", "./dbmap.json"), help="Path to dbmap.json")

    args = parser.parse_args()

    root = Path(args.root)

    # Determine which companies to process
    companies_to_process = []

    if args.all:
        # Load all companies from dbmap.json
        if not Path(args.dbmap).exists():
            raise FileNotFoundError(f"dbmap.json not found: {args.dbmap}")
        dbmap = load_dbmap(args.dbmap)
        companies_to_process = list(dbmap.keys())
        log(f"Processing all {len(companies_to_process)} companies from dbmap.json")
    elif args.companies:
        companies_to_process = [c.strip() for c in args.companies.split(",") if c.strip()]
    elif args.company:
        companies_to_process = [args.company]
    else:
        parser.error("Must specify --company, --companies, or --all")

    if not companies_to_process:
        raise SystemExit("No companies to process")

    # Generate master for each company
    success_count = 0
    error_count = 0

    for company in companies_to_process:
        try:
            generate_master(company, args.date, root)
            success_count += 1
        except Exception as e:
            warn(f"[{company}] Failed: {e}")
            error_count += 1

    # Summary
    log(f"\n=== Summary ===")
    log(f"Success: {success_count}")
    if error_count > 0:
        log(f"Errors: {error_count}")

if __name__ == "__main__":
    main()
