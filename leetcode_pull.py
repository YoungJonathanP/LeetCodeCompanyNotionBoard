#!/usr/bin/env python3
import os, json, time, argparse, datetime, sys
from pathlib import Path
from typing import Dict, Tuple
from dotenv import load_dotenv
from playwright.sync_api import sync_playwright, BrowserContext, Page

COMPANIES_ROOT = os.getenv("COMPANIES_ROOT", "companies")
WINDOWS = {
    "30d":  "thirty-days",
    "90d":  "three-months",
    "180d": "six-months",
}

# Full GraphQL query (as provided)
FQL = """
query favoriteQuestionList($favoriteSlug: String!, $filter: FavoriteQuestionFilterInput, $filtersV2: QuestionFilterInput, $searchKeyword: String, $sortBy: QuestionSortByInput, $limit: Int, $skip: Int, $version: String = "v2") {
  favoriteQuestionList(
    favoriteSlug: $favoriteSlug
    filter: $filter
    filtersV2: $filtersV2
    searchKeyword: $searchKeyword
    sortBy: $sortBy
    limit: $limit
    skip: $skip
    version: $version
  ) {
    questions {
      difficulty
      id
      paidOnly
      questionFrontendId
      status
      title
      titleSlug
      translatedTitle
      isInMyFavorites
      frequency
      acRate
      contestPoint
      topicTags {
        name
        nameTranslated
        slug
      }
    }
    totalLength
    hasMore
  }
}
""".strip()

def load_dbmap(path: str) -> Dict[str, Dict[str, str]]:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    # Validate minimal schema
    for k, v in data.items():
        if not isinstance(v, dict) or "db" not in v or "slug" not in v:
            raise SystemExit(f"dbmap.json entry invalid for '{k}': expected {{'db': '...', 'slug': '...'}}")
    return data

def parse_companies_arg(arg: str, dbmap: Dict[str, Dict[str, str]]) -> Dict[str, Dict[str, str]]:
    # arg example: "Meta, Amazon, Microsoft"
    want = [x.strip() for x in arg.split(",") if x.strip()]
    if not want:
        raise SystemExit("No companies provided. Use --companies 'Meta, Amazon, ...'")
    missing = [w for w in want if w not in dbmap]
    if missing:
        known = ", ".join(sorted(dbmap.keys()))
        raise SystemExit(f"Companies not in dbmap: {missing}\nKnown: {known}")
    return {name: dbmap[name] for name in want}

def ensure_logged_in(page: Page, timeout_s: int = 120):
    page.goto("https://leetcode.com/", wait_until="domcontentloaded")
    t0 = time.time()
    while time.time() - t0 < timeout_s:
        cookies = page.context.cookies()
        names = {c["name"] for c in cookies}
        if "LEETCODE_SESSION" in names and "csrftoken" in names:
            return
        time.sleep(1)
    raise SystemExit("Login not detected (timeout). Please log in and rerun.")

def get_csrf(page: Page) -> str:
    for c in page.context.cookies():
        if c["name"] == "csrftoken":
            return c["value"]
    return ""

def graphql_fetch(page: Page, company_slug: str, window_slug: str, *, limit: int = 1000) -> dict:
    """Fetch questions from LeetCode API.

    NOTE: We use a high limit (1000) to fetch ALL questions,
    then sort client-side to ensure we get top N by frequency.
    LeetCode API sorting is unreliable.
    """
    favorite_slug = f"{company_slug}-{window_slug}"
    variables = {
        "skip": 0,
        "limit": limit,
        "favoriteSlug": favorite_slug,
        "filtersV2": {
            "filterCombineType": "ALL",
            "statusFilter": {"questionStatuses": [], "operator": "IS"},
            "difficultyFilter": {"difficulties": [], "operator": "IS"},
            "languageFilter": {"languageSlugs": [], "operator": "IS"},
            "topicFilter": {"topicSlugs": [], "operator": "IS"},
            "acceptanceFilter": {},
            "frequencyFilter": {},
            "frontendIdFilter": {},
            "lastSubmittedFilter": {},
            "publishedFilter": {},
            "companyFilter": {"companySlugs": [], "operator": "IS"},
            "positionFilter": {"positionSlugs": [], "operator": "IS"},
            "contestPointFilter": {"contestPoints": [], "operator": "IS"},
            "premiumFilter": {"premiumStatus": [], "operator": "IS"}
        },
        "searchKeyword": "",
        "sortBy": {"sortField": "CUSTOM", "sortOrder": "ASCENDING"}
    }
    csrf = get_csrf(page)

    # Get additional headers from browser context to match real requests
    import uuid
    random_uuid = str(uuid.uuid4())

    headers = {
        "content-type": "application/json",
        "x-csrftoken": csrf,
        "referer": f"https://leetcode.com/company/{company_slug}/?favoriteSlug={favorite_slug}",
        "origin": "https://leetcode.com",
        "random-uuid": random_uuid,
        "accept": "*/*",
        "sec-fetch-dest": "empty",
        "sec-fetch-mode": "cors",
        "sec-fetch-site": "same-origin",
    }
    body = {"query": FQL, "variables": variables, "operationName": "favoriteQuestionList"}

    resp = page.evaluate("""
      async ({url, headers, body}) => {
        const r = await fetch(url, { method: 'POST', headers, body: JSON.stringify(body) });
        const text = await r.text();
        return { status: r.status, text };
      }
    """, {"url": "https://leetcode.com/graphql", "headers": headers, "body": body})

    if resp["status"] != 200:
        raise RuntimeError(f"GraphQL status {resp['status']}: {resp['text'][:300]}")
    return json.loads(resp["text"])

def three_files_exist(dirpath: Path) -> bool:
    paths = [dirpath / "30d.json", dirpath / "90d.json", dirpath / "180d.json"]
    for p in paths:
        if not p.exists():
            return False
        try:
            if p.stat().st_size <= 2:  # "{}" or empty
                return False
        except Exception:
            return False
    return True

def pull_snapshot_for_company(page: Page, display_name: str, leetcode_slug: str, out_dir: Path, *, top_n: int, throttle_ms: int):
    """Pull snapshots for a company.

    Args:
        page: Playwright page
        display_name: Display name (e.g., "Meta")
        leetcode_slug: LeetCode company slug (e.g., "facebook")
        out_dir: Output directory
        top_n: Number of top questions to keep (sorted by frequency descending)
        throttle_ms: Delay between requests in milliseconds
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    for short, long_slug in WINDOWS.items():
        favorite = f"{leetcode_slug}-{long_slug}"
        try:
            # Fetch with high limit to get all questions
            data = graphql_fetch(page, leetcode_slug, long_slug, limit=1000)

            # CRITICAL FIX: LeetCode API sometimes returns unsorted data
            # Sort by frequency descending to ensure we get top N questions
            try:
                questions = data["data"]["favoriteQuestionList"]["questions"]
                total_length = data["data"]["favoriteQuestionList"]["totalLength"]

                # Sort by frequency descending
                questions_sorted = sorted(questions, key=lambda q: q.get("frequency", 0), reverse=True)

                # Take top N questions
                questions_top = questions_sorted[:top_n]

                # Update the data structure
                data["data"]["favoriteQuestionList"]["questions"] = questions_top
                data["data"]["favoriteQuestionList"]["totalLength"] = total_length  # Keep original total
                data["data"]["favoriteQuestionList"]["hasMore"] = len(questions_sorted) > top_n

                print(f"[{display_name}] {short}: Fetched {len(questions)}/{total_length}, sorted and kept top {len(questions_top)} by frequency")

                # Show top 3 for verification
                if questions_top:
                    top3 = questions_top[:3]
                    print(f"  Top 3: ", end="")
                    for q in top3:
                        qid = q.get('questionFrontendId', '?')
                        freq = q.get('frequency', 0)
                        print(f"{qid}(f={freq:.1f}) ", end="")
                    print()
            except (KeyError, TypeError) as e:
                print(f"[WARN] Could not sort {display_name} {short}: {e}", file=sys.stderr)

            out_path = out_dir / f"{short}.json"
            out_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"[OK] {display_name} {short} -> {out_path}")
            time.sleep(throttle_ms / 1000.0)
        except Exception as e:
            print(f"[WARN] Pull failed for {display_name} ({favorite}): {e}", file=sys.stderr)

def main():
    load_dotenv()
    ap = argparse.ArgumentParser(description="LeetCode puller (login required every run).")
    ap.add_argument("--dbmap", default=os.getenv("NOTION_DB_MAP_FILE", "./dbmap.json"), help="Path to dbmap.json (display -> {db, slug})")
    ap.add_argument("--companies", required=True, help="Comma-separated display names that exist in dbmap.json, e.g. 'Meta, Amazon'")
    ap.add_argument("--root", default=os.getenv("COMPANIES_ROOT", "companies"))
    ap.add_argument("--date", help="YYYY-MM-DD (default=today)")
    ap.add_argument("--top-n", type=int, default=int(os.getenv("PULL_TOP_N", "100")),
                    help="Number of top questions to keep (sorted by frequency descending, default=100)")
    ap.add_argument("--throttle-ms", type=int, default=int(os.getenv("PULL_THROTTLE_MS", "400")))
    args = ap.parse_args()

    dbmap = load_dbmap(args.dbmap)
    selected = parse_companies_arg(args.companies, dbmap)

    date_str = args.date or datetime.date.today().isoformat()
    out_root = Path(args.root)

    print(f"=== LeetCode Pull Configuration ===")
    print(f"Companies: {', '.join(selected.keys())}")
    print(f"Date: {date_str}")
    print(f"Top N questions: {args.top_n}")
    print()

    # Always require login: no storage state, headed session each run
    with sync_playwright() as p:
        context: BrowserContext = p.chromium.launch_persistent_context(
            user_data_dir=None,  # incognito-like: no persistence
            headless=False       # force headed so user can log in
        )
        page = context.new_page()
        ensure_logged_in(page)

        for display, meta in selected.items():
            slug = meta["slug"]
            company_dir = out_root / display / date_str
            if three_files_exist(company_dir):
                print(f"[SKIP] {display} already has 30d/90d/180d for {date_str} at {company_dir}")
                continue
            pull_snapshot_for_company(page, display, slug, company_dir, top_n=args.top_n, throttle_ms=args.throttle_ms)

        context.close()

if __name__ == "__main__":
    main()
