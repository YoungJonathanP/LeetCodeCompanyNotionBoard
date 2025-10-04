#!/usr/bin/env python3
import os, json, argparse, datetime, subprocess, sys
from pathlib import Path
from typing import Dict
from dotenv import load_dotenv

COMPANIES_ROOT = os.getenv("COMPANIES_ROOT", "companies")

def load_dbmap(path: str) -> Dict[str, Dict[str, str]]:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    for k, v in data.items():
        if not isinstance(v, dict) or "db" not in v or "slug" not in v:
            raise SystemExit(f"dbmap.json entry invalid for '{k}': expected {{'db': '...', 'slug': '...'}}")
    return data

def parse_companies_arg(arg: str, dbmap: Dict[str, Dict[str, str]]) -> Dict[str, Dict[str, str]]:
    want = [x.strip() for x in arg.split(",") if x.strip()]
    if not want:
        raise SystemExit("No companies provided. Use --companies 'Meta, Amazon, ...'")
    missing = [w for w in want if w not in dbmap]
    if missing:
        known = ", ".join(sorted(dbmap.keys()))
        raise SystemExit(f"Companies not in dbmap: {missing}\nKnown: {known}")
    return {name: dbmap[name] for name in want}

def latest_date_folder(company_dir: Path) -> Path | None:
    if not company_dir.exists(): return None
    candidates = [p for p in company_dir.iterdir() if p.is_dir()]
    # folders are expected to be YYYY-MM-DD; sort lexicographically descending
    candidates = sorted(candidates, key=lambda p: p.name, reverse=True)
    return candidates[0] if candidates else None

def three_files_exist(dirpath: Path) -> bool:
    paths = [dirpath / "30d.json", dirpath / "90d.json", dirpath / "180d.json"]
    for p in paths:
        if not p.exists() or p.stat().st_size <= 2:
            return False
    return True

def run(cmd: list[str]) -> int:
    print("[RUN]", " ".join(cmd))
    return subprocess.call(cmd)

def main():
    load_dotenv()
    ap = argparse.ArgumentParser(description="Pull from LeetCode → Generate Master → Upload to Notion")
    ap.add_argument("--dbmap", default=os.getenv("NOTION_DB_MAP_FILE", "./dbmap.json"))
    ap.add_argument("--companies", required=True, help="Comma-separated display names that exist in dbmap.json, e.g. 'Meta, Amazon'")
    ap.add_argument("--date", help="YYYY-MM-DD (default=today)")
    ap.add_argument("--root", default=os.getenv("COMPANIES_ROOT", "companies"))
    ap.add_argument("--top-n", type=int, default=100, help="Number of top questions to pull (default=100)")
    ap.add_argument("--dry-run", action="store_true", help="Dry-run mode for upload (skip actual Notion writes)")
    args = ap.parse_args()

    dbmap = load_dbmap(args.dbmap)
    selected = parse_companies_arg(args.companies, dbmap)
    date_str = args.date or datetime.date.today().isoformat()

    print(f"=== Pull and Import Workflow ===")
    print(f"Companies: {', '.join(selected.keys())}")
    print(f"Date: {date_str}")
    print(f"Top N questions: {args.top_n}")
    print()

    # Step 1: Pull from LeetCode
    print("[Step 1/3] Pulling from LeetCode...")
    pull_cmd = [
        sys.executable, "leetcode_pull.py",
        "--dbmap", args.dbmap,
        "--companies", ",".join(selected.keys()),
        "--root", args.root,
        "--date", date_str,
        "--top-n", str(args.top_n)
    ]
    rc = run(pull_cmd)
    if rc != 0:
        sys.exit(rc)

    # Step 2: Generate master.json files
    print("\n[Step 2/3] Generating master.json files...")
    generate_cmd = [
        sys.executable, "generate_master.py",
        "--companies", ",".join(selected.keys()),
        "--root", args.root,
        "--date", date_str
    ]
    rc = run(generate_cmd)
    if rc != 0:
        print(f"[WARN] Master generation failed (exit {rc})", file=sys.stderr)
        sys.exit(rc)

    # Step 3: Upload to Notion (new optimized system)
    print("\n[Step 3/3] Uploading to Notion...")
    upload_cmd = [
        sys.executable, "upload.py",
        "--companies", ",".join(selected.keys()),
        "--date", date_str,
        "--adapter", "notion"
    ]
    if args.dry_run:
        upload_cmd.append("--dry-run")

    rc = run(upload_cmd)
    if rc != 0:
        print(f"[WARN] Upload step failed (exit {rc})", file=sys.stderr)
        sys.exit(rc)

    print("\n=== All done ===")
    print(f"Successfully processed {len(selected)} companies for {date_str}")

if __name__ == "__main__":
    main()
