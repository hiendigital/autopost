"""Module 4: Dynamic HTML Parser (BeautifulSoup4).

For the first N (<=3) H2 headings: build a 1:1 image from the heading, upload it to the
WordPress media library to obtain a public URL, then inject a SEO <figure> right after the H2.
"""
from __future__ import annotations

import logging

from bs4 import BeautifulSoup

from .clients import wordpress_client
from .image_pipeline import ImagePipeline, rate_limit_sleep

logger = logging.getLogger(__name__)


def process_and_inject_media(
    html_content: str,
    image_pipeline: ImagePipeline,
    config: dict,
    temp_paths: list[str] | None = None,
) -> tuple[str, list[str]]:
    """Return (modified_html, [public_image_urls]).

    `temp_paths` (if provided) accumulates local WebP paths for later cleanup.
    """
    max_h2 = config["image"].get("max_h2_images", 3)
    soup = BeautifulSoup(html_content, "html.parser")
    h2_tags = soup.find_all("h2")[:max_h2]

    image_urls: list[str] = []
    for h2 in h2_tags:
        h2_text = h2.get_text(strip=True)
        if not h2_text:
            continue

        local_path = image_pipeline.build_image_for_text(h2_text)
        if temp_paths is not None:
            temp_paths.append(local_path)

        media = wordpress_client.upload_media(local_path)
        web_url = media["source_url"]
        image_urls.append(web_url)

        figure = _build_figure(soup, web_url, h2_text)
        h2.insert_after(figure)

        rate_limit_sleep(config)

    return str(soup), image_urls


def _build_figure(soup: BeautifulSoup, web_url: str, h2_text: str):
    figure = soup.new_tag("figure")
    img = soup.new_tag("img", src=web_url, alt=h2_text)
    img["class"] = "aligncenter"
    figcaption = soup.new_tag("figcaption")
    figcaption.string = f"Hình ảnh: {h2_text}"
    figure.append(img)
    figure.append(figcaption)
    return figure
