"""
Thin wrapper around the Notion REST API.

Only the handful of endpoints this project needs:
  - GET  /v1/pages/{page_id}                          -> page object (has public_url)
  - GET  /v1/pages/{page_id}/markdown                  -> enhanced markdown export
  - GET  /v1/blocks/{block_id}/children                -> child blocks (paginated)
  - GET  /v1/databases/{database_id}                   -> database object (lists data_sources)
  - POST /v1/data_sources/{data_source_id}/query        -> rows (as page objects, paginated)

Docs: https://developers.notion.com/reference
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Iterator

import requests

API_BASE = "https://api.notion.com/v1"
NOTION_VERSION = "2026-03-11"

# Notion's public API is rate limited to roughly 3 requests/second (average).
# Be polite and back off on 429s using the Retry-After header.
_MIN_INTERVAL = 0.34


class NotionAPIError(RuntimeError):
    def __init__(self, status: int, url: str, body: str):
        super().__init__(f"Notion API error {status} for {url}: {body[:500]}")
        self.status = status
        self.url = url
        self.body = body


@dataclass
class NotionClient:
    token: str
    session: requests.Session | None = None
    _last_request_time: float = 0.0

    def __post_init__(self) -> None:
        if self.session is None:
            self.session = requests.Session()
        self.session.headers.update(
            {
                "Authorization": f"Bearer {self.token}",
                "Notion-Version": NOTION_VERSION,
                "Content-Type": "application/json",
            }
        )

    def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_request_time
        if elapsed < _MIN_INTERVAL:
            time.sleep(_MIN_INTERVAL - elapsed)

    def _request(self, method: str, path: str, *, params: dict | None = None,
                 json_body: dict | None = None, max_retries: int = 5) -> dict:
        url = f"{API_BASE}{path}"
        for attempt in range(max_retries + 1):
            self._throttle()
            self._last_request_time = time.monotonic()
            resp = self.session.request(method, url, params=params, json=json_body, timeout=30)
            if resp.status_code == 429:
                retry_after = float(resp.headers.get("Retry-After", "1"))
                time.sleep(retry_after)
                continue
            if resp.status_code >= 500 and attempt < max_retries:
                time.sleep(min(2 ** attempt, 10))
                continue
            if not resp.ok:
                raise NotionAPIError(resp.status_code, url, resp.text)
            return resp.json()
        raise NotionAPIError(resp.status_code, url, resp.text)

    # -- Pages -----------------------------------------------------------

    def get_page(self, page_id: str) -> dict:
        return self._request("GET", f"/pages/{page_id}")

    def get_page_markdown(self, page_id: str) -> dict:
        """Returns {object, id, markdown, truncated, unknown_block_ids}."""
        return self._request("GET", f"/pages/{page_id}/markdown")

    # -- Blocks ------------------------------------------------------------

    def iter_block_children(self, block_id: str) -> Iterator[dict]:
        cursor: str | None = None
        while True:
            params = {"page_size": 100}
            if cursor:
                params["start_cursor"] = cursor
            data = self._request("GET", f"/blocks/{block_id}/children", params=params)
            yield from data.get("results", [])
            if not data.get("has_more"):
                return
            cursor = data.get("next_cursor")

    # -- Databases / data sources -------------------------------------------

    def get_database(self, database_id: str) -> dict:
        return self._request("GET", f"/databases/{database_id}")

    def iter_data_source_rows(self, data_source_id: str) -> Iterator[dict]:
        cursor: str | None = None
        while True:
            body: dict[str, Any] = {"page_size": 100}
            if cursor:
                body["start_cursor"] = cursor
            data = self._request("POST", f"/data_sources/{data_source_id}/query", json_body=body)
            yield from data.get("results", [])
            if not data.get("has_more"):
                return
            cursor = data.get("next_cursor")


def strip_dashes(notion_id: str) -> str:
    """Notion IDs work with or without dashes; normalize by stripping them."""
    return notion_id.replace("-", "")


def extract_id_from_url(url_or_id: str) -> str:
    """Accept a raw ID or a notion.so/app.notion.com URL and return the bare ID."""
    candidate = url_or_id.strip()
    if candidate.startswith("http"):
        # last path segment, then last 32 hex chars of it
        candidate = candidate.rstrip("/").split("/")[-1]
        candidate = candidate.split("?")[0]
        candidate = candidate.split("#")[0]
        # titles are sometimes prefixed, e.g. "My-Page-<id>"
        candidate = candidate.split("-")[-1]
    return strip_dashes(candidate)
