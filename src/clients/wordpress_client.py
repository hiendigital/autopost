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
from urllib.parse import urlparse

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


# Generic Vietnamese modifiers that dilute a keyword search (slug form). They are dropped
# when deriving the "core" query, so an intent-heavy long-tail like
# "web thiết kế thời trang online miễn phí" becomes "web thiết kế thời trang" — otherwise
# words like "online"/"miễn phí" poison WP relevance and off-topic pages outrank money pages.
_SEARCH_STOPWORDS = {
    # generic commercial/modifier words
    "online", "mien", "phi", "free", "gia", "re", "download", "tai", "app",
    "mau", "template", "tot", "nhat", "uy", "tin", "2023", "2024", "2025", "2026",
    # common Vietnamese function words (prepositions/articles) that add no topical signal
    "bang", "tren", "cho", "voi", "va", "cua", "cac", "mot", "khi", "nao",
    "la", "gi", "cach", "nhu", "the", "de", "o", "trong",
}


def _significant_words(keyword: str) -> list[str]:
    """Keyword words with generic modifiers removed, order-preserving and de-duplicated."""
    out: list[str] = []
    seen: set[str] = set()
    for w in re.split(r"\s+", (keyword or "").strip()):
        s = vietnamese_slug(w)
        if s and s not in _SEARCH_STOPWORDS and s not in seen:
            seen.add(s)
            out.append(w)
    return out


# Utility/system pages that must never be used as SEO internal links (homepage, WooCommerce
# cart/checkout/account/shop, contact, etc.), matched on the URL's last path segment.
_NON_CONTENT_PAGE_SLUGS = {
    "trang-chu", "home", "gio-hang", "cart", "thanh-toan", "checkout",
    "tai-khoan", "account", "my-account", "cua-hang", "shop",
    "lien-he", "contact", "list-mess",
}


def _is_uncategorized(url: str) -> bool:
    """True for the WooCommerce/WP default 'Uncategorized' term — never a good link target."""
    last = urlparse(url or "").path.strip("/").rsplit("/", 1)[-1].lower()
    return last in {"uncategorized", "chua-phan-loai"}


def _is_linkable_page(url: str) -> bool:
    """False for the homepage and known utility pages that shouldn't be internal SEO links."""
    path = urlparse(url or "").path.strip("/")
    if not path:  # homepage
        return False
    return path.rsplit("/", 1)[-1].lower() not in _NON_CONTENT_PAGE_SLUGS


def _title_is_relevant(title: str, sig_slugs: set[str]) -> bool:
    """True if the candidate title shares a significant token with the keyword.

    Guards money-page/product/category candidates against WP full-text search returning
    tangential matches (e.g. keyword 'thiết kế thời trang' pulling a 'Chatbot Ai' page whose
    body merely mentions 'web'/'online'). Empty keyword => no filtering.
    """
    if not sig_slugs:
        return True
    toks = {t for t in vietnamese_slug(title).split("-") if t}
    return bool(toks & sig_slugs)


def _shares_niche_word(title: str, sig_slugs: set[str], generic_slugs: set[str]) -> bool:
    """True only if title and keyword share a *distinguishing* (non-generic) word.

    Every keyword on a niche site repeats the same head terms (e.g. 'thiết kế web'), so those
    carry no signal for picking the right product category — matching on them makes a
    'nội thất' article link a 'thời trang' category. We therefore require the shared word to
    lie outside `generic_slugs` (the configured business head terms).
    """
    toks = {t for t in vietnamese_slug(title).split("-") if t}
    return bool((toks & sig_slugs) - generic_slugs)


_TIMEOUT = 60
_MAX_RETRIES = 4
_BACKOFF_BASE = 5  # seconds: 5, 10, 20, 40...

_BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

_session: requests.Session | None = None

# Cache the result of REST source discovery (post types + taxonomies) for the process.
# None = not probed yet; a dict (possibly empty) = already probed.
_sources_cache: dict | None = None


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


def _discover_sources() -> dict:
    """Probe which post types + taxonomies the site exposes over the REST API.

    Returns {'types': {...}, 'taxonomies': {...}} where each inner dict is keyed by the
    slug and carries a 'rest_base'. Best-effort: on any error (or a site that hides these
    index endpoints) returns {} so callers fall back gracefully. Cached for the process.
    """
    global _sources_cache
    if _sources_cache is not None:
        return _sources_cache

    result: dict = {}
    try:
        types_resp = _request("GET", f"{_base_url()}/wp-json/wp/v2/types", auth=_auth())
        types_resp.raise_for_status()
        tax_resp = _request("GET", f"{_base_url()}/wp-json/wp/v2/taxonomies", auth=_auth())
        tax_resp.raise_for_status()
        result = {"types": types_resp.json() or {}, "taxonomies": tax_resp.json() or {}}
    except Exception as exc:  # noqa: BLE001 - discovery is optional, degrade to posts+pages
        logger.warning("Could not discover WP REST sources (%s); using posts+pages only.", exc)
        result = {}

    _sources_cache = result
    return result


def _resolve_rest_base(slug: str, container_key: str) -> str | None:
    """Look up the real REST base for a post type ('types') or taxonomy ('taxonomies').

    Returns None when the site does not expose that slug (e.g. no WooCommerce).
    """
    container = _discover_sources().get(container_key, {})
    entry = container.get(slug) if isinstance(container, dict) else None
    if isinstance(entry, dict) and entry.get("rest_base"):
        return str(entry["rest_base"])
    return None


def _product_rest_bases(il_cfg: dict) -> tuple[str | None, str | None]:
    """Return (product_rest_base, product_category_rest_base) for this site.

    With auto_detect on, the bases come from REST discovery (None when absent so the
    site is treated as non-WooCommerce). With auto_detect off, the configured slugs are
    used directly as the REST bases.
    """
    pt_slug = il_cfg.get("product_post_type", "product")
    tax_slug = il_cfg.get("product_taxonomy", "product_cat")
    if il_cfg.get("auto_detect", True):
        return (
            _resolve_rest_base(pt_slug, "types"),
            _resolve_rest_base(tax_slug, "taxonomies"),
        )
    return pt_slug, tax_slug


def _fetch_term_items(rest_base: str, params: dict) -> list[dict]:
    """GET a WP taxonomy-term listing and return [{'title','url'}]; [] on any error.

    Terms differ from posts: the label is a plain `name` string (not `title.rendered`)
    and the archive URL is `link`.
    """
    try:
        resp = _request("GET", f"{_base_url()}/wp-json/wp/v2/{rest_base}", auth=_auth(), params=params)
        resp.raise_for_status()
        out: list[dict] = []
        for term in resp.json():
            name = (term.get("name") or "").strip()
            url = (term.get("link") or "").strip()
            if name and url:
                out.append({"title": name, "url": url})
        return out
    except Exception as exc:  # noqa: BLE001 - internal links are optional
        logger.warning("Could not fetch terms %s for internal links: %s", rest_base, exc)
        return []


def _fetch_all_terms(rest_base: str, max_terms: int = 300) -> list[dict]:
    """Fetch every term of a taxonomy (paginated) as [{'title','url','count'}].

    Used for internal linking: we pull the whole (small) product-category list once and match
    names word-by-word locally, which is more reliable than WP's substring `search` param.
    Ordered by product count desc (most commercial first). [] on any error.
    """
    out: list[dict] = []
    page = 1
    while len(out) < max_terms:
        batch = _request_terms_page(rest_base, page)
        if batch is None:  # error
            break
        for term in batch:
            name = (term.get("name") or "").strip()
            url = (term.get("link") or "").strip()
            if name and url:
                out.append({"title": name, "url": url, "count": int(term.get("count") or 0)})
        if len(batch) < 100:  # last page
            break
        page += 1
    return out[:max_terms]


def _request_terms_page(rest_base: str, page: int) -> list | None:
    """One page (100) of a taxonomy listing; None on error so the caller can stop paging."""
    try:
        resp = _request(
            "GET", f"{_base_url()}/wp-json/wp/v2/{rest_base}", auth=_auth(),
            params={"per_page": 100, "page": page, "orderby": "count", "order": "desc",
                    "_fields": "name,link,count"},
        )
        resp.raise_for_status()
        return resp.json()
    except Exception as exc:  # noqa: BLE001 - internal links are optional
        logger.warning("Could not list terms %s (page %d): %s", rest_base, page, exc)
        return None


def _fake_link_candidates(keyword: str, max_products: int, include_products: bool) -> list[dict]:
    """Dry-run candidates covering every kind so the prompt build can be verified offline."""
    base = get_env("WP_BASE_URL", "https://example.com").rstrip("/")
    slug = vietnamese_slug(keyword) or "san-pham"
    items = [
        {"title": f"Danh mục {keyword}", "url": f"{base}/danh-muc/{slug}/", "kind": "category"},
        {"title": f"Dịch vụ {keyword}", "url": f"{base}/dich-vu/{slug}/", "kind": "page"},
    ]
    if include_products:
        for i in range(1, max_products + 1):
            items.append(
                {"title": f"Sản phẩm {keyword} {i}", "url": f"{base}/san-pham/{slug}-{i}/",
                 "kind": "product"}
            )
    for i in range(1, 5):
        items.append(
            {"title": f"Bài viết liên quan {i}", "url": f"{base}/bai-viet-lien-quan-{i}/",
             "kind": "post"}
        )
    return items


def fetch_internal_link_candidates(
    keyword: str,
    category_id: int | None = None,
    config: dict | None = None,
) -> list[dict]:
    """Internal-link candidates for the article, ordered by SEO priority.

    Returns [{'title','url','kind'}] where kind is one of category|page|product|post.
    Priority (money pages first): product category -> service/landing page -> product ->
    same-category blog post. Product sources are auto-detected from the site's REST index,
    so a non-WooCommerce site simply yields pages+posts (old behaviour). De-duplicated by
    URL and capped at `max_candidates`; the model then picks 3-6 to actually insert.
    """
    config = config or {}
    il_cfg = (config.get("wordpress", {}) or {}).get("internal_links", {}) or {}
    max_candidates = int(il_cfg.get("max_candidates", 10))
    max_products = int(il_cfg.get("max_products", 2))
    include_products = bool(il_cfg.get("include_products", True))

    if is_dry_run():
        return _fake_link_candidates(keyword, max_products, include_products)[:max_candidates]

    fields = "title,link"
    prod_base, cat_base = _product_rest_bases(il_cfg)
    candidates: list[dict] = []

    # Derive the topical "core" of the keyword. Modifiers like 'online'/'miễn phí' are dropped
    # so they can't poison WP relevance, and category/product terms are searched token-by-token
    # (WP term search is a substring match, so a short name like 'Thời trang' never matches a
    # multi-word phrase). Every search-based candidate is then filtered by title relevance.
    sig_words = _significant_words(keyword)
    sig_slugs = {vietnamese_slug(w) for w in sig_words}
    core_query = " ".join(sig_words)
    # core phrase first, then individual niche tokens (capped to bound request count).
    token_queries: list[str] = []
    for q in ([core_query] if core_query else []) + sig_words[:4]:
        if q and q not in token_queries:
            token_queries.append(q)
    # Business head terms that every keyword repeats (e.g. 'thiết kế web'); ignored when
    # matching product categories so the *niche* word decides which category is relevant.
    generic_slugs: set[str] = set()
    for term in il_cfg.get("generic_terms", ["thiết kế web", "thiết kế", "website", "web"]):
        generic_slugs |= {t for t in vietnamese_slug(term).split("-") if t}

    # 1. Product categories (highest-priority money pages). Pull the whole category list and
    #    keep any whose NAME shares a *distinguishing* (non-generic) word with the keyword —
    #    so a 'nội thất' article links 'nội thất' categories, not every 'thiết kế web X' one.
    #    Already ordered by product count (commercial value).
    if cat_base and sig_slugs - generic_slugs:
        seen_cat: set[str] = set()
        for it in _fetch_all_terms(cat_base):
            if (
                it["url"] not in seen_cat
                and not _is_uncategorized(it["url"])
                and _shares_niche_word(it["title"], sig_slugs, generic_slugs)
            ):
                seen_cat.add(it["url"])
                candidates.append({"title": it["title"], "url": it["url"], "kind": "category"})

    # 2. Service/landing pages. WP page search is AND across words, so the full phrase returns
    #    nothing whenever the keyword carries a niche/brand token the generic money page lacks
    #    (e.g. 'Canva', 'WordPress', 'thời trang'). We therefore search per token as well and
    #    let the title guard drop tangential hits (e.g. a 'Chatbot Ai' page bleeding in on 'web').
    if sig_slugs:
        seen_page: set[str] = set()
        for q in token_queries:
            for it in _fetch_link_items(
                "pages",
                {"per_page": 10, "search": q, "orderby": "relevance", "_fields": fields},
            ):
                if (
                    it["url"] not in seen_page
                    and _is_linkable_page(it["url"])
                    and _title_is_relevant(it["title"], sig_slugs)
                ):
                    seen_page.add(it["url"])
                    candidates.append({**it, "kind": "page"})

    # 3. Individual products (contextual, capped).
    if include_products and prod_base and sig_slugs:
        seen_prod: set[str] = set()
        prod_hits: list[dict] = []
        for q in token_queries:
            for it in _fetch_link_items(
                prod_base, {"per_page": 5, "search": q, "_fields": fields}
            ):
                if it["url"] not in seen_prod and _title_is_relevant(it["title"], sig_slugs):
                    seen_prod.add(it["url"])
                    prod_hits.append({**it, "kind": "product"})
        candidates += prod_hits[:max_products]

    # 4. Same-category recent blog posts (topical cluster).
    post_params: dict = {"per_page": 5, "orderby": "date", "order": "desc", "_fields": fields}
    if category_id:
        post_params["categories"] = category_id
    post_items = _fetch_link_items("posts", post_params)
    candidates += [{**it, "kind": "post"} for it in post_items]

    # De-dupe by URL, preserving the priority order above, then cap.
    merged: list[dict] = []
    seen: set[str] = set()
    for item in candidates:
        if item["url"] not in seen:
            seen.add(item["url"])
            merged.append(item)
    return merged[:max_candidates]


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
