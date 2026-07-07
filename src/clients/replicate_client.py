"""Replicate image wrapper (Flux / SDXL). Generates a real local placeholder image when DRY_RUN is on."""
from __future__ import annotations

import io
import logging
import random

from PIL import Image, ImageDraw

from ..env import is_dry_run, require_env

logger = logging.getLogger(__name__)


def generate_image(prompt: str, model: str, version: str | None, size: int = 1024) -> Image.Image:
    """Return a Pillow RGB image of `size` x `size` for the given prompt."""
    if is_dry_run():
        logger.info("[DRY_RUN] Fabricating placeholder SDXL image for prompt: %.60s", prompt)
        return _fake_background(size, prompt)

    import replicate
    import requests

    require_env("REPLICATE_API_TOKEN")  # SDK reads it from env automatically
    ref = f"{model}:{version}" if version else model
    output = replicate.run(ref, input=_build_input(model, prompt, size))
    url = output[0] if isinstance(output, (list, tuple)) else output
    # `url` may be a FileOutput-like object or a plain URL string.
    if hasattr(url, "read"):
        data = url.read()
    else:
        data = requests.get(str(url), timeout=120).content
    return Image.open(io.BytesIO(data)).convert("RGB")


# Shared negative prompt for models that support one (SDXL). Flux ignores negatives.
_NEGATIVE = (
    "text, watermark, signature, logo, blurry, lowres, low quality, jpeg artifacts, "
    "deformed, distorted, extra limbs, bad anatomy, ugly, grainy, oversaturated"
)


def _build_input(model: str, prompt: str, size: int) -> dict:
    """Return the model-appropriate `input` payload for `replicate.run`."""
    name = model.lower()
    if "recraft" in name:
        # Recraft v3 takes a string `size` enum (e.g. "1024x1024") + a `style`.
        # "digital_illustration" fits the flat/editorial look; use "vector_illustration"
        # for an even flatter vector feel, or "realistic_image" for photos.
        return {
            "prompt": prompt,
            "size": f"{size}x{size}",
            "style": "digital_illustration",
        }
    if "flux" in name:
        # Flux uses natural-language prompts + aspect_ratio (no width/height/num_outputs,
        # no negative_prompt). 1:1 defaults to 1024x1024 which matches raw_size.
        payload = {
            "prompt": prompt,
            "aspect_ratio": "1:1",
            "output_format": "png",
            "output_quality": 95,
        }
        # `prompt_upsampling` + `safety_tolerance` are only valid on the "pro" endpoints.
        if "pro" in name:
            payload["prompt_upsampling"] = True
            payload["safety_tolerance"] = 2
        return payload
    # SDXL / default: enable the refiner and add a negative prompt for cleaner output.
    return {
        "prompt": prompt,
        "negative_prompt": _NEGATIVE,
        "width": size,
        "height": size,
        "num_outputs": 1,
        "num_inference_steps": 40,
        "guidance_scale": 7.5,
        "refine": "expert_ensemble_refiner",
        "apply_watermark": False,
    }


def _fake_background(size: int, prompt: str) -> Image.Image:
    """A deterministic-ish colorful gradient so dry-run images look plausible."""
    seed = sum(ord(c) for c in prompt) or 1
    rng = random.Random(seed)
    c1 = (rng.randint(30, 120), rng.randint(30, 120), rng.randint(60, 160))
    c2 = (rng.randint(120, 230), rng.randint(120, 230), rng.randint(140, 240))
    img = Image.new("RGB", (size, size), c1)
    draw = ImageDraw.Draw(img)
    for y in range(size):
        t = y / size
        r = int(c1[0] + (c2[0] - c1[0]) * t)
        g = int(c1[1] + (c2[1] - c1[1]) * t)
        b = int(c1[2] + (c2[2] - c1[2]) * t)
        draw.line([(0, y), (size, y)], fill=(r, g, b))
    # A few soft circles for texture.
    for _ in range(6):
        x, y = rng.randint(0, size), rng.randint(0, size)
        rad = rng.randint(size // 12, size // 4)
        shade = tuple(min(255, v + rng.randint(-30, 30)) for v in c2)
        draw.ellipse([x - rad, y - rad, x + rad, y + rad], outline=shade, width=3)
    return img
