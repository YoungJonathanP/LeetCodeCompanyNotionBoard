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
    ap = argparse.ArgumentParser(description="Login → Pull JSON → Import to Notion")
    ap.add_argument("--dbmap", default=os.getenv("NOTION_DB_MAP_FILE", "./dbmap.json"))
    ap.add_argument("--companies", required=True, help="Comma-separated display names that exist in dbmap.json, e.g. 'Meta, Amazon'")
    ap.add_argument("--date", help="YYYY-MM-DD (default=today)")
    ap.add_argument("--root", default=os.getenv("COMPANIES_ROOT", "companies"))
    ap.add_argument("--dry-run-import", action="store_true", help="Pass --dry-run to the Notion importer step")
    args = ap.parse_args()

    dbmap = load_dbmap(args.dbmap)
    selected = parse_companies_arg(args.companies, dbmap)
    date_str = args.date or datetime.date.today().isoformat()

    # 1) Login + Pull (always requires login; handled by leetcode_pull.py)
    pull_cmd = [
        sys.executable, "leetcode_pull.py",
        "--dbmap", args.dbmap,
        "--companies", ",".join(selected.keys()),
        "--root", args.root,
        "--date", date_str
    ]
    rc = run(pull_cmd)
    if rc != 0:
        sys.exit(rc)

    # 2) Import to Notion per company (auto-picks latest date via your importer)
    for display, meta in selected.items():
        company_root = Path(args.root) / display
        latest = latest_date_folder(company_root)
        if not latest:
            print(f"[WARN] No snapshot folder found for {display} under {company_root}, skipping import")
            continue
        # If a specific date was requested, ensure we're importing that date
        if args.date and latest.name != date_str:
            intended = company_root / date_str
            if not intended.exists():
                print(f"[WARN] Requested date {date_str} not found for {display}, skipping import")
                continue
            latest = intended

        importer_cmd = [
            sys.executable, "notion_company_snapshot_import.py",
            str(company_root), "--company", display
        ]
        if args.dry_run_import:
            importer_cmd.append("--dry-run")

        rc2 = run(importer_cmd)
        if rc2 != 0:
            print(f"[WARN] Import step failed for {display} (exit {rc2})", file=sys.stderr)

    print("All done.")

if __name__ == "__main__":
    main()
