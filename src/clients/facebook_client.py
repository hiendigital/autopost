"""Facebook Graph API wrapper (album via unpublished photos + feed). DRY_RUN returns fakes."""
from __future__ import annotations

import logging
import random
import time

import requests

from ..env import is_dry_run, require_env

logger = logging.getLogger(__name__)

_TIMEOUT = 60

# Retry policy for transient Graph API failures (e.g. FB briefly can't fetch a freshly
# uploaded image URL, rate limits, or 5xx). Delays grow between attempts.
_MAX_ATTEMPTS = 3
_RETRY_DELAYS = [5, 15]  # seconds to wait before attempt 2 and 3


def _graph_base(version: str) -> str:
    return f"https://graph.facebook.com/{version}"


def _fb_error_detail(resp: requests.Response) -> str:
    """Extract Facebook's structured error message from a response body, if present."""
    try:
        err = resp.json().get("error", {})
    except ValueError:
        return resp.text[:500]
    if not err:
        return resp.text[:500]
    parts = [
        f"message={err.get('message')!r}",
        f"type={err.get('type')!r}",
        f"code={err.get('code')!r}",
    ]
    if err.get("error_subcode") is not None:
        parts.append(f"subcode={err.get('error_subcode')!r}")
    if err.get("error_user_msg"):
        parts.append(f"user_msg={err.get('error_user_msg')!r}")
    if err.get("fbtrace_id"):
        parts.append(f"fbtrace_id={err.get('fbtrace_id')!r}")
    return " ".join(parts)


def _is_retryable(resp: requests.Response) -> bool:
    """Whether a failed Graph response is worth retrying.

    Retry on 5xx, on 429 (rate limit), and on the specific 400s where Facebook could not
    fetch/process the source image URL (common right after the image is uploaded to WP).
    Do NOT retry genuine auth/permission errors (they will never succeed on retry).
    """
    if resp.status_code >= 500 or resp.status_code == 429:
        return True
    if resp.status_code == 400:
        try:
            err = resp.json().get("error", {})
        except ValueError:
            return False
        code = err.get("code")
        subcode = err.get("error_subcode")
        # 1 = unknown/transient, 2 = temporary service issue, 4/17/32/613 = rate limit,
        # 324 = missing/invalid image, 1363xxx = upload/fetch image failures.
        transient_codes = {1, 2, 4, 17, 32, 324, 613}
        if code in transient_codes:
            return True
        if subcode in {1363030, 1363019, 1363033, 1363037}:
            return True
    return False


def _post_with_retry(url: str, data: dict, what: str) -> requests.Response:
    """POST to Graph API with detailed error logging and retry on transient failures."""
    last_exc: Exception | None = None
    for attempt in range(1, _MAX_ATTEMPTS + 1):
        try:
            resp = requests.post(url, data=data, timeout=_TIMEOUT)
        except requests.RequestException as exc:
            last_exc = exc
            logger.warning(
                "%s — network error on attempt %d/%d: %s", what, attempt, _MAX_ATTEMPTS, exc
            )
            resp = None
        else:
            if resp.ok:
                return resp
            detail = _fb_error_detail(resp)
            logger.warning(
                "%s — HTTP %d on attempt %d/%d: %s",
                what,
                resp.status_code,
                attempt,
                _MAX_ATTEMPTS,
                detail,
            )
            if not _is_retryable(resp):
                # Raise with the detailed FB message attached so it lands in the error log.
                raise requests.HTTPError(
                    f"{resp.status_code} on {what}: {detail}", response=resp
                )
            last_exc = requests.HTTPError(
                f"{resp.status_code} on {what}: {detail}", response=resp
            )

        if attempt < _MAX_ATTEMPTS:
            delay = _RETRY_DELAYS[min(attempt - 1, len(_RETRY_DELAYS) - 1)]
            logger.info("%s — retrying in %ds…", what, delay)
            time.sleep(delay)

    # Exhausted all attempts.
    assert last_exc is not None
    raise last_exc


def upload_unpublished_photo(image_url: str, version: str = "v19.0") -> str:
    """Upload a photo as unpublished (published=false). Returns its photo id."""
    if is_dry_run():
        photo_id = str(random.randint(10**14, 10**15))
        logger.info("[DRY_RUN] Pretend-uploaded FB photo %s from %s", photo_id, image_url)
        return photo_id

    page_id = require_env("FB_PAGE_ID")
    token = require_env("FB_PAGE_ACCESS_TOKEN")
    resp = _post_with_retry(
        f"{_graph_base(version)}/{page_id}/photos",
        {"url": image_url, "published": "false", "access_token": token},
        what=f"upload photo ({image_url})",
    )
    return str(resp.json()["id"])


def publish_feed_with_media(caption: str, photo_ids: list[str], version: str = "v19.0") -> dict:
    """Publish a feed post attaching the given (unpublished) photo ids. Returns {'id','url'}."""
    if is_dry_run():
        post_id = f"{random.randint(10**14, 10**15)}_{random.randint(10**14, 10**15)}"
        url = f"https://www.facebook.com/{post_id}"
        logger.info("[DRY_RUN] Pretend-published FB album post %s (%d photos)", post_id, len(photo_ids))
        return {"id": post_id, "url": url}

    page_id = require_env("FB_PAGE_ID")
    token = require_env("FB_PAGE_ACCESS_TOKEN")
    data: dict = {"message": caption, "access_token": token}
    for i, pid in enumerate(photo_ids):
        data[f"attached_media[{i}]"] = f'{{"media_fbid":"{pid}"}}'

    resp = _post_with_retry(
        f"{_graph_base(version)}/{page_id}/feed",
        data,
        what="publish feed",
    )
    post_id = str(resp.json()["id"])
    return {"id": post_id, "url": f"https://www.facebook.com/{post_id}"}
