#!/usr/bin/env python3
"""
Upload adapter abstraction for different upload targets.
Provides base class and concrete implementations (Notion, Excel, Google Sheets).
"""

from abc import ABC, abstractmethod
from typing import Dict, List, Any, Optional, Set
from dataclasses import dataclass, field
from concurrent.futures import ThreadPoolExecutor, as_completed
import time
import sys


@dataclass
class UploadStats:
    """Statistics from upload operations."""
    created: int = 0
    updated: int = 0
    zeroed: int = 0
    skipped: int = 0
    errors: int = 0

    def total_operations(self) -> int:
        return self.created + self.updated + self.zeroed

    def __str__(self) -> str:
        return (f"created={self.created}, updated={self.updated}, "
                f"zeroed={self.zeroed}, skipped={self.skipped}, errors={self.errors}")


class UploadAdapter(ABC):
    """Base adapter for all upload targets."""

    @abstractmethod
    def initialize(self, config: Dict[str, Any]) -> None:
        """Setup connection (called once per adapter instance)."""
        pass

    @abstractmethod
    def get_existing_records(self, database_id: str, company: Optional[str] = None) -> Dict[str, Dict]:
        """
        Fetch existing records from target.

        Args:
            database_id: Target database/sheet identifier
            company: Optional company filter

        Returns:
            Dict mapping slug -> {
                'id': page_id,
                'title': title_text,
                'freq_30d': float,
                'freq_90d': float,
                'freq_180d': float,
                'acceptance_rate': float
            }
        """
        pass

    @abstractmethod
    def batch_upsert(
        self,
        operations: List[Dict[str, Any]],
        database_id: str,
        dry_run: bool = False
    ) -> UploadStats:
        """
        Execute batch create/update/zero operations.

        Args:
            operations: List of {action: "create"|"update", slug, properties, ...}
            database_id: Target database identifier
            dry_run: If True, simulate without actual writes

        Returns:
            UploadStats with counts
        """
        pass

    def supports_parallel(self) -> bool:
        """Whether this adapter can safely run parallel uploads (multi-company)."""
        return True


class NotionAdapter(UploadAdapter):
    """Notion API implementation with batching and concurrency."""

    # Property name mappings (from notion_company_snapshot_import.py)
    PROP_TITLE = "Name"
    PROP_SLUG = "Slug"  # We'll use title matching since slug prop may not exist
    PROP_DIFFICULTY = "Difficulty"
    PROP_ACCEPT_RATE = "Acceptance Rate"
    PROP_TOPIC_TAGS = "Topic Tags"
    PROP_FREQ_30 = "Freq 30d"
    PROP_FREQ_90 = "Freq 90d"
    PROP_FREQ_180 = "Freq 180d"
    PROP_COMPANY = "Company"

    URL_PREFIX = "https://leetcode.com/problems/"
    EPS = 1e-6  # Tolerance for numeric comparisons

    def __init__(self):
        self.client = None
        self.schema_cache: Dict[str, dict] = {}

    def initialize(self, config: Dict[str, Any]) -> None:
        """Initialize Notion client."""
        from notion_client import Client

        notion_token = config.get("notion_token")
        if not notion_token:
            raise RuntimeError("Missing 'notion_token' in config")

        self.client = Client(auth=notion_token)

    def _build_title_text(self, frontend_id: Optional[str], title: str) -> str:
        """Build title string: '123. Problem Title'"""
        return f"{frontend_id}. {title}" if frontend_id else title

    def _build_title_rich_text(self, frontend_id: Optional[str], title: str, url: Optional[str]) -> List[dict]:
        """Build Notion title rich text with link."""
        text_content = self._build_title_text(frontend_id, title)
        link = {"url": url} if url else None
        return [{"type": "text", "text": {"content": text_content, "link": link}}]

    def _get_schema(self, database_id: str) -> dict:
        """Get database schema (cached)."""
        if database_id not in self.schema_cache:
            self.schema_cache[database_id] = self.client.databases.retrieve(database_id=database_id)
        return self.schema_cache[database_id]

    def _ensure_options_exist(self, database_id: str, prop_name: str, values: Set[str]):
        """Batch-add missing select/multi-select options."""
        if not values:
            return

        schema = self._get_schema(database_id)
        prop = schema.get("properties", {}).get(prop_name)
        if not prop:
            return

        ptype = prop.get("type")
        if ptype not in ("select", "multi_select"):
            return

        existing = {o.get("name") for o in prop[ptype].get("options", []) if o.get("name")}
        missing = values - existing

        if missing:
            current_options = prop[ptype].get("options", [])
            new_options = current_options + [{"name": v} for v in missing]
            self.client.databases.update(
                database_id=database_id,
                properties={prop_name: {ptype: {"options": new_options}}}
            )
            # Update cache
            prop[ptype]["options"] = new_options

    def get_existing_records(self, database_id: str, company: Optional[str] = None) -> Dict[str, Dict]:
        """
        Fetch all pages from Notion DB.
        Returns mapping: title_text -> {id, freq_30d, freq_90d, freq_180d, acceptance_rate}
        """
        records = {}
        start_cursor = None

        while True:
            query_kwargs = {
                "database_id": database_id,
                "start_cursor": start_cursor,
                "page_size": 100,
            }

            if company:
                query_kwargs["filter"] = {
                    "property": self.PROP_COMPANY,
                    "select": {"equals": company}
                }

            resp = self.client.databases.query(**query_kwargs)

            for page in resp.get("results", []):
                props = page.get("properties", {})

                # Get title
                name_prop = props.get(self.PROP_TITLE, {})
                title_text = ""
                if name_prop.get("type") == "title":
                    rich = name_prop.get("title") or []
                    title_text = "".join(rt.get("plain_text", "") for rt in rich)

                if not title_text:
                    continue

                def get_num(prop_name):
                    p = props.get(prop_name, {})
                    return p.get("number")

                records[title_text] = {
                    "id": page["id"],
                    "title": title_text,
                    "freq_30d": get_num(self.PROP_FREQ_30),
                    "freq_90d": get_num(self.PROP_FREQ_90),
                    "freq_180d": get_num(self.PROP_FREQ_180),
                    "acceptance_rate": get_num(self.PROP_ACCEPT_RATE),
                }

            if not resp.get("has_more"):
                break
            start_cursor = resp.get("next_cursor")

        return records

    def _build_properties(self, question: Dict[str, Any], company: Optional[str]) -> dict:
        """Build Notion properties from question data."""
        props = {
            self.PROP_TITLE: {
                "title": self._build_title_rich_text(
                    question.get("frontend_id"),
                    question["title"],
                    question.get("url")
                )
            },
            self.PROP_FREQ_30: {"number": float(question.get("freq_30d", 0))},
            self.PROP_FREQ_90: {"number": float(question.get("freq_90d", 0))},
            self.PROP_FREQ_180: {"number": float(question.get("freq_180d", 0))},
        }

        if question.get("acceptance_rate") is not None:
            props[self.PROP_ACCEPT_RATE] = {"number": float(question["acceptance_rate"])}

        if question.get("difficulty"):
            props[self.PROP_DIFFICULTY] = {"select": {"name": question["difficulty"]}}

        if question.get("topic_tags"):
            props[self.PROP_TOPIC_TAGS] = {
                "multi_select": [{"name": t} for t in question["topic_tags"]]
            }

        if company:
            props[self.PROP_COMPANY] = {"select": {"name": company}}

        return props

    def batch_upsert(
        self,
        operations: List[Dict[str, Any]],
        database_id: str,
        dry_run: bool = False
    ) -> UploadStats:
        """
        Execute operations with concurrent requests.

        Uses ThreadPoolExecutor for parallelism with rate limiting.
        """
        stats = UploadStats()

        if not operations:
            return stats

        if dry_run:
            for op in operations:
                action = op.get("action")
                if action == "create":
                    stats.created += 1
                elif op.get("zeroed"):
                    stats.zeroed += 1
                else:
                    stats.updated += 1
            return stats

        # Pre-ensure all options exist
        difficulties = set()
        tags = set()
        companies = set()

        for op in operations:
            q = op.get("question", {})
            if q.get("difficulty"):
                difficulties.add(q["difficulty"])
            for tag in q.get("topic_tags", []):
                tags.add(tag)
            if op.get("company"):
                companies.add(op["company"])

        self._ensure_options_exist(database_id, self.PROP_DIFFICULTY, difficulties)
        self._ensure_options_exist(database_id, self.PROP_TOPIC_TAGS, tags)
        self._ensure_options_exist(database_id, self.PROP_COMPANY, companies)

        # Execute operations in batches with concurrency
        batch_size = 10
        max_workers = 5

        def _execute_single(op):
            """Execute a single operation."""
            try:
                action = op["action"]
                props = op["properties"]

                if action == "create":
                    self.client.pages.create(
                        parent={"database_id": database_id},
                        properties=props
                    )
                    return "created"
                elif action == "update":
                    self.client.pages.update(
                        page_id=op["page_id"],
                        properties=props
                    )
                    if op.get("zeroed"):
                        return "zeroed"
                    return "updated"
            except Exception as e:
                print(f"[ERROR] {op.get('slug', 'unknown')}: {e}", file=sys.stderr)
                return "error"

        # Process in batches to respect rate limits
        for i in range(0, len(operations), batch_size):
            batch = operations[i:i+batch_size]

            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = [executor.submit(_execute_single, op) for op in batch]

                for future in as_completed(futures):
                    result = future.result()
                    if result == "created":
                        stats.created += 1
                    elif result == "updated":
                        stats.updated += 1
                    elif result == "zeroed":
                        stats.zeroed += 1
                    elif result == "error":
                        stats.errors += 1

            # Rate limit: pause between batches
            if i + batch_size < len(operations):
                time.sleep(0.3)  # ~3 req/sec average

        return stats


# Factory function
def create_adapter(adapter_type: str) -> UploadAdapter:
    """Create an adapter instance by type."""
    if adapter_type.lower() == "notion":
        return NotionAdapter()
    else:
        raise ValueError(f"Unknown adapter type: {adapter_type}")
