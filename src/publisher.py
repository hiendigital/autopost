"""Module 5: Multi-channel Publisher (WordPress + Facebook)."""
from __future__ import annotations

import logging

from .clients import facebook_client, wordpress_client
from .image_pipeline import rate_limit_sleep

logger = logging.getLogger(__name__)


def upload_to_wordpress_media(local_image_path: str) -> dict:
    """Returns {'attachment_id', 'source_url'}."""
    media = wordpress_client.upload_media(local_image_path)
    return {"attachment_id": media["id"], "source_url": media["source_url"]}


def publish_wordpress_post(
    title: str,
    content: str,
    meta_description: str,
    featured_image_id: int | None,
    category_name: str,
    config: dict,
    seo_title: str = "",
    focus_keyword: str = "",
    slug: str | None = None,
    category_id: int | None = None,
) -> str:
    """Publish a WP post; returns the post URL.

    `title` becomes the post H1. `seo_title` is the SEO/meta title pinned in RankMath
    (kept separate so the H1 can differ). `slug` sets the URL.
    """
    wp_cfg = config.get("wordpress", {})
    status = wp_cfg.get("default_post_status", "draft")
    if category_id is None:
        category_id = wordpress_client.resolve_category_id(category_name)

    meta = _seo_meta(
        meta_description,
        wp_cfg.get("seo_plugin", "none"),
        seo_title=seo_title,
        focus_keyword=focus_keyword,
    )
    result = wordpress_client.create_post(
        title=title,
        content=content,
        excerpt=meta_description,
        status=status,
        featured_media=featured_image_id,
        category_id=category_id,
        meta=meta,
        slug=slug,
    )
    return result["link"]


def _seo_meta(
    meta_description: str,
    plugin: str,
    seo_title: str = "",
    focus_keyword: str = "",
) -> dict | None:
    """Build SEO-plugin meta fields (title + description + focus keyword).

    NOTE for RankMath: these `rank_math_*` keys are only accepted over the REST API if
    they are registered with `show_in_rest` on the WordPress side (see the mu-plugin in
    README). Without that registration WordPress silently drops them.
    """
    plugin = (plugin or "none").lower()
    if plugin == "yoast":
        meta: dict = {}
        if meta_description:
            meta["_yoast_wpseo_metadesc"] = meta_description
        if seo_title:
            meta["_yoast_wpseo_title"] = seo_title
        if focus_keyword:
            meta["_yoast_wpseo_focuskw"] = focus_keyword
        return meta or None
    if plugin == "rankmath":
        meta = {}
        if meta_description:
            meta["rank_math_description"] = meta_description
        if seo_title:
            meta["rank_math_title"] = seo_title
        if focus_keyword:
            meta["rank_math_focus_keyword"] = focus_keyword
        return meta or None
    return None  # 'none' -> rely on excerpt only


def publish_facebook_album(caption: str, image_urls: list[str], config: dict) -> str:
    """Upload each image as an unpublished photo, then publish a feed post. Returns URL."""
    version = config.get("facebook", {}).get("graph_version", "v19.0")
    photo_ids: list[str] = []
    for url in image_urls:
        if not url:
            continue
        photo_id = facebook_client.upload_unpublished_photo(url, version=version)
        photo_ids.append(photo_id)
        rate_limit_sleep(config)

    if not photo_ids:
        raise ValueError("No images available to build the Facebook album.")

    result = facebook_client.publish_feed_with_media(caption, photo_ids, version=version)
    return result["url"]
