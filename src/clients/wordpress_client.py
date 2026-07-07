"""WordPress REST API wrapper (media + posts + categories). DRY_RUN returns fakes.

Some WordPress hosts sit behind an aggressive firewall (Imunify360 / CSF / mod_security)
that resets connections from default `python-requests` clients or when requests arrive too
fast. To be resilient we send a browser-like User-Agent and retry connection resets with
exponential backoff, all over a shared session.
"""
from __future__ import annotations

import logging
import mimetypes
import os
import random
import re
import time
import unicodedata

import requests

from ..env import get_env, is_dry_run, require_env

logger = logging.getLogger(__name__)


def vietnamese_slug(text: str) -> str:
    """Turn a Vietnamese keyword into an ASCII, hyphen-joined slug.

    'Thiết kế web du học' -> 'thiet-ke-web-du-hoc'
    """
    text = (text or "").replace("đ", "d").replace("Đ", "D")
    text = unicodedata.normalize("NFD", text)
    text = "".join(c for c in text if unicodedata.category(c) != "Mn")
    text = text.lower()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    return text.strip("-")

_TIMEOUT = 60
_MAX_RETRIES = 4
_BACKOFF_BASE = 5  # seconds: 5, 10, 20, 40...

_BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

_session: requests.Session | None = None


def _get_session() -> requests.Session:
    global _session
    if _session is None:
        s = requests.Session()
        s.headers.update({"User-Agent": _BROWSER_UA, "Accept": "application/json"})
        _session = s
    return _session


def _request(method: str, url: str, **kwargs) -> requests.Response:
    """Issue a request, retrying transient connection resets with backoff."""
    kwargs.setdefault("timeout", _TIMEOUT)
    session = _get_session()
    last_exc: Exception | None = None
    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            return session.request(method, url, **kwargs)
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as exc:
            last_exc = exc
            wait = _BACKOFF_BASE * (2 ** (attempt - 1))
            logger.warning(
                "WordPress connection reset (attempt %d/%d) on %s — retrying in %ds. "
                "Host firewall may be rate-limiting this IP.",
                attempt, _MAX_RETRIES, url, wait,
            )
            if attempt < _MAX_RETRIES:
                time.sleep(wait)
    raise RuntimeError(
        f"WordPress unreachable after {_MAX_RETRIES} attempts ({url}). "
        f"The host firewall is likely blocking this IP (whitelist it or slow the request rate). "
        f"Last error: {last_exc}"
    )


def _base_url() -> str:
    return require_env("WP_BASE_URL").rstrip("/")


def _auth() -> tuple[str, str]:
    return require_env("WP_USER"), require_env("WP_APP_PASSWORD")


def upload_media(local_path: str) -> dict:
    """Upload an image to the WP media library. Returns {'id', 'source_url'}."""
    filename = os.path.basename(local_path)
    if is_dry_run():
        fake_id = random.randint(10000, 99999)
        base = get_env("WP_BASE_URL", "https://example.com").rstrip("/")
        url = f"{base}/wp-content/uploads/dryrun/{filename}"
        logger.info("[DRY_RUN] Pretend-uploaded %s -> %s", filename, url)
        return {"id": fake_id, "source_url": url}

    mime = mimetypes.guess_type(local_path)[0] or "image/webp"
    with open(local_path, "rb") as fh:
        resp = _request(
            "POST",
            f"{_base_url()}/wp-json/wp/v2/media",
            auth=_auth(),
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"',
                "Content-Type": mime,
            },
            data=fh.read(),
        )
    resp.raise_for_status()
    body = resp.json()
    return {"id": body["id"], "source_url": body["source_url"]}


def resolve_category_id(category_name: str) -> int | None:
    """Find a category id by name; returns None if not found (or dry-run)."""
    if is_dry_run() or not category_name:
        return None
    resp = _request(
        "GET",
        f"{_base_url()}/wp-json/wp/v2/categories",
        auth=_auth(),
        params={"search": category_name},
    )
    resp.raise_for_status()
    for cat in resp.json():
        if cat.get("name", "").strip().lower() == category_name.strip().lower():
            return cat["id"]
    cats = resp.json()
    return cats[0]["id"] if cats else None


def _fetch_link_items(endpoint: str, params: dict) -> list[dict]:
    """GET a WP listing endpoint and return [{'title','url'}]; [] on any error."""
    try:
        resp = _request("GET", f"{_base_url()}/wp-json/wp/v2/{endpoint}", auth=_auth(), params=params)
        resp.raise_for_status()
        out: list[dict] = []
        for p in resp.json():
            title = (p.get("title") or {}).get("rendered", "").strip()
            url = p.get("link", "").strip()
            if title and url:
                out.append({"title": title, "url": url})
        return out
    except Exception as exc:  # noqa: BLE001 - internal links are optional
        logger.warning("Could not fetch %s for internal links: %s", endpoint, exc)
        return []


def fetch_related_posts(
    keyword: str,
    category_id: int | None = None,
    count: int = 6,
    include_pages: bool = True,
) -> list[dict]:
    """Best-effort list of existing posts + pages to use as internal links.

    Returns [{'title', 'url'}]. Posts are same-category recent posts; pages are matched by
    keyword `search` so only relevant service/landing pages (not Contact/Privacy) get linked
    — helpful when a service page needs to rank. Results are merged and de-duplicated by URL.
    On any error (e.g. host firewall blocking a listing endpoint) that source is skipped so
    the pipeline can continue without internal links rather than failing.
    """
    if is_dry_run():
        base = get_env("WP_BASE_URL", "https://example.com").rstrip("/")
        items = [
            {"title": f"Bài viết liên quan {i}", "url": f"{base}/bai-viet-lien-quan-{i}/"}
            for i in range(1, count + 1)
        ]
        if include_pages:
            items += [
                {"title": f"Dịch vụ {i}", "url": f"{base}/dich-vu-{i}/"}
                for i in range(1, 3)
            ]
        return items

    fields = "title,link"
    post_params: dict = {"per_page": count, "orderby": "date", "order": "desc", "_fields": fields}
    if category_id:
        post_params["categories"] = category_id
    posts = _fetch_link_items("posts", post_params)

    pages: list[dict] = []
    if include_pages and keyword:
        # Search pages by keyword so only topically-relevant pages become link candidates.
        page_params = {"per_page": count, "search": keyword, "orderby": "relevance",
                       "_fields": fields}
        pages = _fetch_link_items("pages", page_params)

    # Merge posts + pages, de-dupe by URL, preserving order (posts first, then pages).
    merged: list[dict] = []
    seen: set[str] = set()
    for item in posts + pages:
        if item["url"] not in seen:
            seen.add(item["url"])
            merged.append(item)
    return merged


def create_post(
    title: str,
    content: str,
    excerpt: str = "",
    status: str = "draft",
    featured_media: int | None = None,
    category_id: int | None = None,
    meta: dict | None = None,
    slug: str | None = None,
) -> dict:
    """Create a post. Returns {'id', 'link'}."""
    if is_dry_run():
        fake_id = random.randint(1000, 9999)
        base = get_env("WP_BASE_URL", "https://example.com").rstrip("/")
        link = f"{base}/{slug or fake_id}/"
        logger.info("[DRY_RUN] Pretend-created %s post '%s' -> %s", status, title, link)
        return {"id": fake_id, "link": link}

    payload: dict = {
        "title": title,
        "content": content,
        "excerpt": excerpt,
        "status": status,
    }
    if slug:
        payload["slug"] = slug
    if featured_media:
        payload["featured_media"] = featured_media
    if category_id:
        payload["categories"] = [category_id]
    if meta:
        payload["meta"] = meta

    resp = _request(
        "POST",
        f"{_base_url()}/wp-json/wp/v2/posts",
        auth=_auth(),
        json=payload,
    )
    resp.raise_for_status()
    body = resp.json()
    return {"id": body["id"], "link": body["link"]}
