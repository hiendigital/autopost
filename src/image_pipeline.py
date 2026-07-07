"""Module 3: Anonymous 1:1 Image Pipeline (Replicate SDXL + Pillow).

Pipeline per image:
  generate_ai_background -> strip_exif_and_anonymize -> overlay_brand_graphics -> WebP file.
"""
from __future__ import annotations

import logging
import os
import textwrap
import time
import uuid

from PIL import Image, ImageDraw, ImageFont

from .clients import openai_client, replicate_client

logger = logging.getLogger(__name__)


class ImagePipeline:
    def __init__(self, config: dict):
        self.cfg = config
        img = config["image"]
        self.raw_size = img["raw_size"]
        self.crop_px = img["crop_px"]
        self.final_size = img["final_size"]
        self.webp_quality = img["webp_quality"]
        self.overlay_opacity = img["overlay_opacity"]
        self.output_dir = config["paths"]["output_dir"]
        self.logo_path = config["paths"]["logo_path"]
        self.font_path = config["paths"]["font_path"]
        self.style_prompt = config["prompts"]["image_style_prompt"]
        self.model = config["replicate"]["model"]
        self.version = config["replicate"].get("version")
        self.openai_model = config["openai"]["model"]
        os.makedirs(self.output_dir, exist_ok=True)

    # ------------------------------------------------------ generate + clean
    def generate_ai_background(self, h2_text: str) -> Image.Image:
        """Translate the H2 into an English art prompt, then render 1:1 via SDXL."""
        art_prompt = self._translate_to_art_prompt(h2_text)
        full_prompt = f"{art_prompt}, {self.style_prompt}, square 1:1 composition"
        return replicate_client.generate_image(
            full_prompt, self.model, self.version, size=self.raw_size
        )

    def _translate_to_art_prompt(self, h2_text: str) -> str:
        system = "You translate Vietnamese headings into concise English image-generation prompts."
        user = (
            f"H2: {h2_text}\n"
            f"Return a short English art prompt (no more than 25 words) describing an "
            f"editorial illustration for this heading. No text in the image."
        )
        try:
            return openai_client.chat_text(system, user, model=self.openai_model)
        except Exception as exc:  # noqa: BLE001 - fall back gracefully
            logger.warning("Prompt translation failed (%s); using heading verbatim.", exc)
            return h2_text

    def strip_exif_and_anonymize(self, img: Image.Image) -> Image.Image:
        """Crop 8px per edge (changes the image hash), resize, drop all metadata."""
        w, h = img.size
        c = self.crop_px
        cropped = img.crop((c, c, w - c, h - c))
        resized = cropped.resize((self.final_size, self.final_size), Image.LANCZOS)
        # Rebuild the image from raw pixels so no EXIF/metadata carries over.
        clean = Image.new("RGB", resized.size)
        clean.putdata(list(resized.convert("RGB").getdata()))
        return clean

    # ------------------------------------------------------------- branding
    def overlay_brand_graphics(self, img: Image.Image, text_title: str) -> str:
        """Add dark gradient overlay, wrapped VN title, logo; save WebP. Returns path."""
        base = img.convert("RGBA")
        size = base.size[0]

        # Dark translucent overlay on the lower half for title legibility.
        overlay = Image.new("RGBA", base.size, (0, 0, 0, 0))
        odraw = ImageDraw.Draw(overlay)
        alpha = int(255 * self.overlay_opacity)
        odraw.rectangle([0, size // 2, size, size], fill=(0, 0, 0, alpha))
        base = Image.alpha_composite(base, overlay)

        draw = ImageDraw.Draw(base)
        font = self._load_font(size)
        wrapped = self._wrap_title(draw, text_title, font, max_width=int(size * 0.88))

        # Position the block within the lower third, kept above the logo corner.
        line_height = self._line_height(draw, font)
        total_h = line_height * len(wrapped)
        y = size - total_h - int(size * 0.22)
        for line in wrapped:
            tw = self._text_width(draw, line, font)
            x = (size - tw) // 2
            # Soft shadow then white text.
            draw.text((x + 2, y + 2), line, font=font, fill=(0, 0, 0, 180))
            draw.text((x, y), line, font=font, fill=(255, 255, 255, 255))
            y += line_height

        self._paste_logo(base, size)

        out_path = os.path.join(self.output_dir, f"img_{uuid.uuid4().hex[:12]}.webp")
        base.convert("RGB").save(out_path, "WEBP", quality=self.webp_quality)
        logger.info("Saved branded image %s", out_path)
        return out_path

    def build_image_for_text(self, text_title: str) -> str:
        """Full pipeline: background -> anonymize -> brand -> WebP path."""
        raw = self.generate_ai_background(text_title)
        clean = self.strip_exif_and_anonymize(raw)
        return self.overlay_brand_graphics(clean, text_title)

    # --------------------------------------------------------------- helpers
    def _load_font(self, size: int) -> ImageFont.FreeTypeFont:
        font_size = max(28, int(size * 0.058))
        candidates = [self.font_path, r"C:\Windows\Fonts\arialbd.ttf", r"C:\Windows\Fonts\arial.ttf"]
        for path in candidates:
            try:
                if path and os.path.exists(path):
                    return ImageFont.truetype(path, font_size)
            except OSError:
                continue
        logger.warning("No TrueType font found; using PIL default (may lack VN glyphs).")
        return ImageFont.load_default()

    def _wrap_title(self, draw, text: str, font, max_width: int) -> list[str]:
        # Estimate characters-per-line from average glyph width, then refine.
        avg = max(1, self._text_width(draw, "ABCabc123", font) // 9)
        approx = max(8, max_width // avg)
        lines: list[str] = []
        for chunk in textwrap.wrap(text, width=approx) or [text]:
            # Refine: shrink until it fits.
            while self._text_width(draw, chunk, font) > max_width and " " in chunk:
                chunk = chunk.rsplit(" ", 1)[0]
            lines.append(chunk)
        return lines[:4]

    @staticmethod
    def _text_width(draw, text: str, font) -> int:
        box = draw.textbbox((0, 0), text, font=font)
        return box[2] - box[0]

    @staticmethod
    def _line_height(draw, font) -> int:
        box = draw.textbbox((0, 0), "Ag", font=font)
        return int((box[3] - box[1]) * 1.5)

    def _paste_logo(self, base: Image.Image, size: int) -> None:
        if not self.logo_path or not os.path.exists(self.logo_path):
            return
        try:
            logo = Image.open(self.logo_path).convert("RGBA")
        except OSError as exc:
            logger.warning("Could not open logo %s: %s", self.logo_path, exc)
            return
        target_w = int(size * 0.18)
        ratio = target_w / logo.width
        logo = logo.resize((target_w, int(logo.height * ratio)), Image.LANCZOS)
        margin = int(size * 0.03)
        pos = (size - logo.width - margin, size - logo.height - margin)
        base.paste(logo, pos, logo)


def cleanup_images(paths: list[str]) -> None:
    """Delete temp WebP files (spec cleanup mitigation)."""
    for p in paths:
        try:
            if p and os.path.exists(p):
                os.remove(p)
        except OSError as exc:  # noqa: BLE001
            logger.warning("Could not delete temp image %s: %s", p, exc)


def rate_limit_sleep(cfg: dict) -> None:
    """Randomized pause between heavy API calls (429 mitigation)."""
    import random

    rl = cfg.get("rate_limit", {})
    lo = rl.get("min_seconds", 15)
    hi = rl.get("max_seconds", 30)
    # Dry-run should not actually stall the pipeline.
    from .env import is_dry_run

    if is_dry_run():
        return
    time.sleep(random.uniform(lo, hi))
