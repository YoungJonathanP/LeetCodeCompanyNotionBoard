#!/usr/bin/env python3
"""
Upload master.json to target adapter with delta-based sync.

Usage:
  python upload.py --company Meta                          # Notion, latest date
  python upload.py --company Meta --date 2025-10-03
  python upload.py --company Meta --adapter notion --dry-run
  python upload.py --companies "Meta,Amazon,Google"        # Parallel upload
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path
from datetime import datetime, date
from typing import Dict, List, Optional, Any
from concurrent.futures import ThreadPoolExecutor, as_completed

from dotenv import load_dotenv
from upload_adapter import UploadAdapter, UploadStats, create_adapter


COMPANIES_ROOT = os.getenv("COMPANIES_ROOT", "companies")
EPS = 1e-6  # Tolerance for numeric comparisons


def log(msg: str):
    print(msg, file=sys.stdout)


def warn(msg: str):
    print(f"[WARN] {msg}", file=sys.stderr)


def is_date_folder(name: str) -> bool:
    """Check if folder name is YYYY-MM-DD format."""
    try:
        datetime.strptime(name, "%Y-%m-%d")
        return True
    except ValueError:
        return False


def pick_latest_date_folder(company_dir: Path) -> Optional[Path]:
    """Find the most recent date folder, preferring today if it exists."""
    if not company_dir.exists():
        return None

    # Check if today's date exists first
    today = date.today().isoformat()
    today_dir = company_dir / today
    if today_dir.exists():
        return today_dir

    # Otherwise pick latest
    candidates = [d for d in company_dir.iterdir() if d.is_dir() and is_date_folder(d.name)]
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.name)


def load_master(date_dir: Path) -> Dict:
    """Load master.json from date folder."""
    master_path = date_dir / "master.json"
    if not master_path.exists():
        raise FileNotFoundError(
            f"master.json not found in {date_dir}. "
            f"Run: python generate_master.py --company {date_dir.parent.name} --date {date_dir.name}"
        )

    with open(master_path, encoding="utf-8") as f:
        return json.load(f)


def load_upload_state(date_dir: Path) -> Optional[Dict]:
    """Load .upload_state.json if exists."""
    state_path = date_dir / ".upload_state.json"
    if state_path.exists():
        with open(state_path, encoding="utf-8") as f:
            return json.load(f)
    return None


def save_upload_state(
    date_dir: Path,
    master: Dict,
    stats: UploadStats,
    adapter_type: str,
    database_id: str
):
    """Save upload state for future delta comparison."""
    state = {
        "last_uploaded_at": datetime.utcnow().isoformat() + "Z",
        "master_checksum": master["metadata"]["checksum"],
        "adapter": adapter_type,
        "database_id": database_id,
        "stats": {
            "total_records": master["metadata"]["total_questions"],
            "created": stats.created,
            "updated": stats.updated,
            "zeroed": stats.zeroed,
            "skipped": stats.skipped,
            "errors": stats.errors
        },
        "uploaded_questions": {
            slug: {
                "freq_30d": q["freq_30d"],
                "freq_90d": q["freq_90d"],
                "freq_180d": q["freq_180d"],
                "acceptance_rate": q.get("acceptance_rate")
            }
            for slug, q in master["questions"].items()
        }
    }

    state_path = date_dir / ".upload_state.json"
    state_path.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")


def compute_delta_operations(
    master: Dict,
    upload_state: Optional[Dict],
    existing_records: Dict[str, Dict],
    company: Optional[str]
) -> List[Dict]:
    """
    Compute minimal set of operations needed.

    Delta logic:
    1. If no upload_state or checksum changed → compute operations
    2. If checksum same → skip entirely (return empty)
    3. Compare per-question numeric fields and build ops for changed only
    """

    # Fast path: no changes detected
    if upload_state and upload_state.get("master_checksum") == master["metadata"]["checksum"]:
        log(f"  ✓ Checksum match - no changes detected")
        return []

    operations = []
    current_slugs = set(master["questions"].keys())
    uploaded_questions = upload_state.get("uploaded_questions", {}) if upload_state else {}

    # Build title -> slug mapping for existing records
    title_to_record = existing_records

    for slug, question in master["questions"].items():
        # Build title key for lookup
        title_key = (f"{question['frontend_id']}. {question['title']}"
                     if question.get('frontend_id')
                     else question['title'])

        # Check if this question was uploaded before
        prev = uploaded_questions.get(slug)

        needs_update = False
        if not prev:
            needs_update = True  # New question
        else:
            # Compare numeric fields (frequencies and acceptance rate)
            if (abs(prev.get("freq_30d", 0) - question["freq_30d"]) > EPS or
                abs(prev.get("freq_90d", 0) - question["freq_90d"]) > EPS or
                abs(prev.get("freq_180d", 0) - question["freq_180d"]) > EPS):
                needs_update = True

            # Check acceptance rate if present in new data
            if question.get("acceptance_rate") is not None:
                prev_acc = prev.get("acceptance_rate", 0)
                if abs(prev_acc - question.get("acceptance_rate", 0)) > EPS:
                    needs_update = True

        if needs_update:
            # Check if exists in target (create vs update)
            existing = title_to_record.get(title_key)

            # Build properties using adapter's format
            from upload_adapter import NotionAdapter
            adapter = NotionAdapter()
            properties = adapter._build_properties(question, company)

            operations.append({
                "action": "update" if existing else "create",
                "page_id": existing.get("id") if existing else None,
                "slug": slug,
                "properties": properties,
                "question": question,
                "company": company,
                "zeroed": False
            })

    # Handle removed questions (zero frequencies)
    if upload_state:
        removed_slugs = set(uploaded_questions.keys()) - current_slugs

        for slug in removed_slugs:
            # Find in existing records by slug (need to search)
            # For now, we'll match by checking uploaded_questions title
            prev_q = uploaded_questions[slug]

            # Since we don't have title in upload_state, we'll search existing_records
            # This is a limitation - we'll zero all records not in current master
            # that were in previous upload
            continue  # Skip for now - will be handled in full sync

    # Zero questions that exist in DB but not in current master
    uploaded_titles = {
        (f"{q['frontend_id']}. {q['title']}" if q.get('frontend_id') else q['title'])
        for q in master["questions"].values()
    }

    for title_key, record in title_to_record.items():
        if title_key not in uploaded_titles:
            # Question exists in DB but not in current master - zero it
            zero_props = {
                "Freq 30d": {"number": 0.0},
                "Freq 90d": {"number": 0.0},
                "Freq 180d": {"number": 0.0}
            }

            operations.append({
                "action": "update",
                "page_id": record["id"],
                "slug": f"unknown-{record['id'][:8]}",
                "properties": zero_props,
                "question": {},
                "company": company,
                "zeroed": True
            })

    return operations


def load_dbmap(path: str) -> Dict[str, Dict[str, str]]:
    """Load dbmap.json."""
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    for k, v in data.items():
        if not isinstance(v, dict) or "db" not in v or "slug" not in v:
            raise SystemExit(f"dbmap.json entry invalid for '{k}': expected {{'db': '...', 'slug': '...'}}")
    return data


def resolve_database_id(company: str, dbmap: Dict[str, Dict[str, str]], cli_db: Optional[str]) -> str:
    """
    Resolve database ID for a company.
    Priority: CLI arg > dbmap entry > error
    """
    if cli_db:
        return cli_db

    if company in dbmap:
        return dbmap[company]["db"]

    raise RuntimeError(
        f"No database ID found for company '{company}'. "
        f"Add to dbmap.json or use --database-id"
    )


def upload_single_company(
    company: str,
    date_str: Optional[str],
    adapter: UploadAdapter,
    config: Dict,
    dry_run: bool = False
) -> UploadStats:
    """Upload one company's master.json with delta sync."""

    start_time = time.perf_counter()

    # Resolve date folder
    company_dir = Path(config["root"]) / company
    if date_str:
        date_dir = company_dir / date_str
        if not date_dir.exists():
            raise FileNotFoundError(f"Date folder not found: {date_dir}")
    else:
        date_dir = pick_latest_date_folder(company_dir)
        if not date_dir:
            raise FileNotFoundError(f"No date folders found in {company_dir}")

    log(f"[{company}] Using snapshot: {date_dir.name}")

    # Load master
    master = load_master(date_dir)
    log(f"[{company}] Loaded master.json: {master['metadata']['total_questions']} questions")

    # Load previous upload state
    upload_state = load_upload_state(date_dir)
    if upload_state:
        log(f"[{company}] Found previous upload state from {upload_state.get('last_uploaded_at', 'unknown')}")

    # Get existing records from target
    db_id = resolve_database_id(company, config["dbmap"], config.get("database_id"))
    log(f"[{company}] Fetching existing records from database...")
    existing_records = adapter.get_existing_records(db_id, company)
    log(f"[{company}] Found {len(existing_records)} existing records")

    # Compute delta
    log(f"[{company}] Computing delta operations...")
    operations = compute_delta_operations(master, upload_state, existing_records, company)

    if not operations:
        elapsed = time.perf_counter() - start_time
        log(f"[{company}] ✓ No changes needed ({elapsed:.2f}s)")
        return UploadStats(skipped=master["metadata"]["total_questions"])

    log(f"[{company}] Delta: {len(operations)} operations")

    # Count operation types
    creates = sum(1 for op in operations if op["action"] == "create")
    updates = sum(1 for op in operations if op["action"] == "update" and not op.get("zeroed"))
    zeros = sum(1 for op in operations if op.get("zeroed"))
    log(f"[{company}]   Create: {creates}, Update: {updates}, Zero: {zeros}")

    if dry_run:
        log(f"[{company}] [DRY-RUN] Would execute {len(operations)} operations")
        elapsed = time.perf_counter() - start_time
        log(f"[{company}] ✓ Dry-run complete ({elapsed:.2f}s)")
        return UploadStats(created=creates, updated=updates, zeroed=zeros)

    # Execute batch upload
    log(f"[{company}] Uploading changes...")
    stats = adapter.batch_upsert(operations, db_id, dry_run=False)

    if stats.errors > 0:
        warn(f"[{company}] {stats.errors} errors occurred during upload")

    # Save upload state
    save_upload_state(date_dir, master, stats, config["adapter_type"], db_id)

    elapsed = time.perf_counter() - start_time
    log(f"[{company}] ✓ Upload complete: {stats} ({elapsed:.2f}s)")

    return stats


def upload_parallel(
    companies: List[str],
    date_str: Optional[str],
    adapter_type: str,
    config: Dict,
    dry_run: bool = False
) -> Dict[str, UploadStats]:
    """
    Upload multiple companies in parallel.

    Data integrity: each company uses separate DB → fully parallel safe
    If same DB shared → falls back to sequential with warning
    """

    # Safety check: ensure different databases
    db_ids = []
    for company in companies:
        try:
            db_id = resolve_database_id(company, config["dbmap"], config.get("database_id"))
            db_ids.append(db_id)
        except Exception as e:
            warn(f"[{company}] Could not resolve DB ID: {e}")
            db_ids.append(None)

    if len(set(db_ids)) < len([d for d in db_ids if d]):
        warn("Multiple companies share same DB - using sequential upload for data integrity")
        results = {}
        for company in companies:
            try:
                adapter = create_adapter(adapter_type)
                adapter.initialize(config)
                results[company] = upload_single_company(company, date_str, adapter, config, dry_run)
            except Exception as e:
                warn(f"[{company}] Upload failed: {e}")
                results[company] = UploadStats(errors=1)
        return results

    # Parallel upload (each company gets own adapter instance)
    results = {}

    def _upload_company(company):
        try:
            adapter = create_adapter(adapter_type)
            adapter.initialize(config)
            return company, upload_single_company(company, date_str, adapter, config, dry_run)
        except Exception as e:
            warn(f"[{company}] Upload failed: {e}")
            return company, UploadStats(errors=1)

    max_workers = min(3, len(companies))  # Max 3 parallel uploads

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [executor.submit(_upload_company, c) for c in companies]

        for future in as_completed(futures):
            company, stats = future.result()
            results[company] = stats

    return results


def main():
    load_dotenv()

    parser = argparse.ArgumentParser(
        description="Upload master.json to target adapter with delta-based sync",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python upload.py --company Meta
  python upload.py --company Meta --date 2025-10-03 --dry-run
  python upload.py --companies "Meta,Amazon,Google"
        """
    )

    parser.add_argument("--company", help="Single company to upload")
    parser.add_argument("--companies", help="Comma-separated companies for parallel upload")
    parser.add_argument("--date", help="YYYY-MM-DD (default: latest, prefer today)")
    parser.add_argument("--adapter", default="notion", choices=["notion"], help="Upload adapter type")
    parser.add_argument("--database-id", help="Override database ID (from dbmap)")
    parser.add_argument("--dbmap", default=os.getenv("NOTION_DB_MAP_FILE", "./dbmap.json"))
    parser.add_argument("--root", default=os.getenv("COMPANIES_ROOT", "companies"))
    parser.add_argument("--dry-run", action="store_true", help="Simulate without writing")

    args = parser.parse_args()

    # Validate arguments
    if not args.company and not args.companies:
        parser.error("Must specify --company or --companies")

    # Load dbmap
    if not Path(args.dbmap).exists():
        raise FileNotFoundError(f"dbmap.json not found: {args.dbmap}")
    dbmap = load_dbmap(args.dbmap)

    # Build config
    config = {
        "adapter_type": args.adapter,
        "notion_token": os.getenv("NOTION_TOKEN"),
        "root": args.root,
        "dbmap": dbmap,
        "database_id": args.database_id,
    }

    if not config["notion_token"]:
        raise RuntimeError("NOTION_TOKEN not set in environment")

    # Parse companies
    if args.companies:
        companies = [c.strip() for c in args.companies.split(",") if c.strip()]
    else:
        companies = [args.company]

    # Validate companies exist in dbmap (unless database_id override provided)
    if not args.database_id:
        missing = [c for c in companies if c not in dbmap]
        if missing:
            known = ", ".join(sorted(dbmap.keys()))
            raise SystemExit(
                f"Companies not in dbmap: {missing}\n"
                f"Known companies: {known}\n"
                f"Use --database-id to override"
            )

    log(f"=== Upload Starting ===")
    log(f"Adapter: {args.adapter}")
    log(f"Companies: {', '.join(companies)}")
    if args.dry_run:
        log(f"Mode: DRY-RUN")
    log("")

    start_time = time.perf_counter()

    # Upload
    if len(companies) > 1:
        results = upload_parallel(companies, args.date, args.adapter, config, args.dry_run)

        # Summary
        log(f"\n=== Summary ===")
        total_stats = UploadStats()
        for company, stats in results.items():
            log(f"[{company}] {stats}")
            total_stats.created += stats.created
            total_stats.updated += stats.updated
            total_stats.zeroed += stats.zeroed
            total_stats.skipped += stats.skipped
            total_stats.errors += stats.errors

        log(f"\nTotal: {total_stats}")
    else:
        adapter = create_adapter(args.adapter)
        adapter.initialize(config)
        stats = upload_single_company(companies[0], args.date, adapter, config, args.dry_run)

        log(f"\n=== Summary ===")
        log(f"Total: {stats}")

    elapsed = time.perf_counter() - start_time
    log(f"\nTotal time: {elapsed:.2f}s")


if __name__ == "__main__":
    main()
