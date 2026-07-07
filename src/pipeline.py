"""Pipeline orchestrator — implements execution_flow steps 3.1–3.12.

One `run_once()` call processes a single Pending keyword end-to-end, writing results (or an
error) back to the workbook, and always cleaning up temporary images afterwards.
"""
from __future__ import annotations

import logging

from . import content_generator, html_parser, publisher
from .clients import wordpress_client
from .config_manager import ConfigManager, now_string
from .image_pipeline import ImagePipeline, cleanup_images, rate_limit_sleep

logger = logging.getLogger(__name__)


def run_once(keep_images: bool = False) -> bool:
    """Process the next Pending row. Returns True if a row was processed, False if none."""
    cm = ConfigManager()
    config = cm.load_configurations()

    row = cm.fetch_next_pending_row()
    if row is None:
        logger.info("No Pending keyword found. Nothing to do this cycle.")
        return False

    row_index = row["row_index"]
    logger.info("Processing row %d — keyword: %s", row_index, row["keyword"])
    temp_paths: list[str] = []

    try:
        cm.set_status(row_index, "Processing")

        prompts = config["prompts"]
        banned = prompts.get("banned_words", [])
        model = config["openai"]["model"]

        # Resolve category + gather existing posts to use as internal links.
        category_id = wordpress_client.resolve_category_id(row["category"])
        internal_links = wordpress_client.fetch_internal_link_candidates(
            row["keyword"], category_id=category_id, config=config
        )
        logger.info("Found %d internal link candidate(s).", len(internal_links))

        # 3.4 — content + caption
        post = content_generator.generate_seo_post(
            keyword=row["keyword"],
            audience=row["audience"],
            intent=row["intent"],
            category=row["category"],
            system_prompt_web_seo=prompts["system_prompt_web_seo"],
            banned_words=banned,
            model=model,
            internal_links=internal_links,
            all_keywords=row.get("all_keywords", ""),
        )
        rate_limit_sleep(config)
        caption = content_generator.generate_fanpage_caption(
            keyword=row["keyword"],
            audience=row["audience"],
            system_prompt_social=prompts["system_prompt_social"],
            banned_words=banned,
            model=model,
            intent=row["intent"],
        )

        image_pipeline = ImagePipeline(config)

        # 3.5 / 3.6 — featured image -> WP media
        featured_path = image_pipeline.build_image_for_text(post["title"])
        temp_paths.append(featured_path)
        featured = publisher.upload_to_wordpress_media(featured_path)
        featured_url = featured["source_url"]
        cm.update_row_data(row_index, {"featured_image_url": featured_url})
        rate_limit_sleep(config)

        # 3.7 — inject H2 images
        modified_html, content_urls = html_parser.process_and_inject_media(
            post["html_content"], image_pipeline, config, temp_paths=temp_paths
        )
        cm.update_row_data(row_index, {"content_image_urls": ", ".join(content_urls)})

        # 3.8 — publish WP post (H1 = post title; SEO title pinned separately in RankMath)
        slug = wordpress_client.vietnamese_slug(row["keyword"])
        post_url = publisher.publish_wordpress_post(
            title=post["h1"],
            content=modified_html,
            meta_description=post["meta_description"],
            featured_image_id=featured["attachment_id"],
            category_name=row["category"],
            config=config,
            seo_title=post["seo_title"],
            focus_keyword=row["keyword"],
            slug=slug,
            category_id=category_id,
        )

        # 3.9 / 3.10 — Facebook album (featured + content images).
        # Skipped when facebook.enabled is false (default true preserves old behavior).
        # IMPORTANT: the WordPress post is already published at this point, so a Facebook
        # failure must NOT fail the whole row — otherwise a retry would create a DUPLICATE
        # WP post. We keep the row as Success, record the FB error, and leave link_fanpage blank.
        fb_url = ""
        fb_error = ""
        if config.get("facebook", {}).get("enabled", True):
            album_urls = [featured_url] + content_urls
            try:
                fb_url = publisher.publish_facebook_album(caption, album_urls, config)
            except Exception as fb_exc:  # noqa: BLE001 - FB failure is non-fatal after WP publish
                fb_error = f"Facebook thất bại (WP đã đăng): {type(fb_exc).__name__}: {fb_exc}"
                logger.warning("Row %d — %s", row_index, fb_error)
        else:
            logger.info("Facebook disabled (facebook.enabled=false) — skipping album.")

        # 3.11 — record success. Write the AI outputs into their dedicated columns:
        # Title Website (seo_title) / Meta description / H1 / Nội dung website. The sapo is
        # kept only as the opening paragraph inside the body, so the standalone "Đoạn Sapo
        # website" column is intentionally left blank (redundant).
        # Intent + audience are user INPUT columns and are left untouched.
        cm.update_row_data(
            row_index,
            {
                "seo_title": post["seo_title"],
                "meta_description": post["meta_description"],
                "h1": post["h1"],
                "html_content": modified_html,
                "fanpage_content": caption,
                "status": "Success",
                "link_website": post_url,
                "link_fanpage": fb_url,
                "published_at": now_string(),
                "error_log": fb_error,
            },
        )
        logger.info("Row %d SUCCESS — web: %s | fb: %s", row_index, post_url, fb_url)
        return True

    except Exception as exc:  # noqa: BLE001 - 3.12 catch-all
        logger.exception("Row %d FAILED", row_index)
        try:
            cm.log_error_to_sheet(row_index, f"{type(exc).__name__}: {exc}")
        except Exception:  # noqa: BLE001
            logger.exception("Could not write error to sheet for row %d", row_index)
        return True

    finally:
        if not keep_images:
            cleanup_images(temp_paths)
        else:
            logger.info("Kept %d temp image(s) in output/ for inspection.", len(temp_paths))


def retry_facebook(include_skipped: bool = False, limit: int | None = None) -> int:
    """Re-post Facebook ONLY for rows whose WordPress post is already published but whose
    Facebook album failed (or was skipped). Reuses the image URLs + caption already stored
    in the sheet, so WordPress is never touched — there is no risk of a duplicate WP post.

    Returns the number of rows successfully posted to Facebook this run.
    """
    cm = ConfigManager()
    config = cm.load_configurations()

    rows = cm.fetch_facebook_retry_rows(include_skipped=include_skipped)
    if not rows:
        logger.info("No rows need a Facebook retry.")
        return 0
    if limit is not None:
        rows = rows[:limit]

    logger.info("Found %d row(s) to retry on Facebook.", len(rows))
    succeeded = 0
    for row in rows:
        row_index = row["row_index"]
        caption = row.get("fanpage_content", "") or ""
        featured = row.get("featured_image_url", "") or ""
        content_raw = row.get("content_image_urls", "") or ""
        album_urls = [u.strip() for u in ([featured] + content_raw.split(",")) if u and u.strip()]

        if not caption:
            logger.warning("Row %d — no fanpage caption stored; skipping.", row_index)
            continue
        if not album_urls:
            logger.warning("Row %d — no image URLs stored; skipping.", row_index)
            continue

        logger.info("Row %d — retrying Facebook with %d image(s).", row_index, len(album_urls))
        try:
            fb_url = publisher.publish_facebook_album(caption, album_urls, config)
        except Exception as fb_exc:  # noqa: BLE001 - record and move on to the next row
            fb_error = f"Facebook retry thất bại: {type(fb_exc).__name__}: {fb_exc}"
            logger.warning("Row %d — %s", row_index, fb_error)
            cm.update_row_data(row_index, {"error_log": fb_error})
            continue

        # Success — record the FB link and clear the previous Facebook error.
        cm.update_row_data(
            row_index,
            {"link_fanpage": fb_url, "error_log": ""},
        )
        logger.info("Row %d — Facebook OK: %s", row_index, fb_url)
        succeeded += 1

    logger.info("Facebook retry done — %d/%d row(s) posted.", succeeded, len(rows))
    return succeeded
