"""OpenAI GPT-4o wrapper. Returns realistic fakes when DRY_RUN is on."""
from __future__ import annotations

import json
import logging

from ..env import is_dry_run, require_env

logger = logging.getLogger(__name__)

_client = None


def _real_client():
    global _client
    if _client is None:
        from openai import OpenAI

        _client = OpenAI(api_key=require_env("OPENAI_API_KEY"))
    return _client


def chat_json(system_prompt: str, user_prompt: str, model: str = "gpt-4o") -> dict:
    """Call the model expecting a JSON object back. Returns a parsed dict."""
    if is_dry_run():
        return _fake_json(user_prompt)

    client = _real_client()
    resp = client.chat.completions.create(
        model=model,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.9,
    )
    content = resp.choices[0].message.content or "{}"
    return json.loads(content)


def chat_text(system_prompt: str, user_prompt: str, model: str = "gpt-4o") -> str:
    """Call the model expecting free-form text back."""
    if is_dry_run():
        return _fake_text(user_prompt)

    client = _real_client()
    resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.9,
    )
    return (resp.choices[0].message.content or "").strip()


# ---------------------------------------------------------------- dry-run fakes
def _fake_json(user_prompt: str) -> dict:
    """Best-effort fake that adapts to what the caller asked for."""
    low = user_prompt.lower()
    if "meta_description" in low or "html_content" in low or "bài viết" in low:
        kw = _guess_keyword(user_prompt)
        return {
            "intent": _guess_intent(user_prompt),
            "seo_title": f"{kw}: Hướng Dẫn Chi Tiết A-Z"[:60],
            "h1": f"Kinh Nghiệm Thực Tế Về {kw} Cho Người Mới",
            "meta_description": (
                f"Tôi chia sẻ tất tần tật về {kw.lower()} sau nhiều năm làm nghề. "
                f"Ngắn gọn, dễ áp dụng, không lý thuyết suông, xem ngay để không bỏ lỡ."
            )[:160],
            "sapo": (
                f"Bạn đang loay hoay với {kw.lower()}? Bài viết này gói lại toàn bộ kinh nghiệm "
                f"thực chiến của tôi, từ nền tảng tới mẹo nâng cao, để bạn áp dụng được ngay."
            ),
            "lsi_keywords": [kw.lower(), f"{kw.lower()} hiệu quả", f"cách {kw.lower()}", "kinh nghiệm"],
            "html_content": _fake_article_html(kw),
        }
    # Prompt-translation style request.
    return {"prompt": _fake_text(user_prompt)}


def _fake_text(user_prompt: str) -> str:
    kw = _guess_keyword(user_prompt)
    if "prompt" in user_prompt.lower() or "english" in user_prompt.lower():
        return (
            f"editorial flat illustration about {kw.lower()}, soft gradient, "
            f"clean modern vector, professional, high detail"
        )
    return (
        f"✅ {kw} — chuyện tưởng khó mà hóa dễ!\n\n"
        f"Mình vừa thử và thấy ổn áp thật sự. Ai đang quan tâm thì lưu lại nhé 👇\n\n"
        f"#{kw.replace(' ', '')} #mẹohay #chiasekinhnghiem"
    )


def _fake_article_html(kw: str) -> str:
    return (
        f"<p>Tôi bắt đầu với {kw.lower()} từ khá lâu rồi. Ban đầu cũng loay hoay. "
        f"Nhưng rồi mọi thứ sáng ra.</p>"
        f"<h2>{kw} là gì?</h2>"
        f"<p>Nói đơn giản thôi. Đây là thứ ai cũng nên biết. "
        f"Trong quá trình làm việc thực tế, tôi nhận ra nó ảnh hưởng nhiều hơn ta tưởng.</p>"
        f"<h2>Vì sao {kw.lower()} lại quan trọng?</h2>"
        f"<p>Có ba lý do chính. Thứ nhất là hiệu quả. Thứ hai là tiết kiệm thời gian. "
        f"Và thứ ba, quan trọng nhất, là sự an tâm khi bạn đã hiểu rõ mình đang làm gì.</p>"
        f"<h2>Kinh nghiệm cá nhân của tôi</h2>"
        f"<p>Tôi từng mắc lỗi. Nhiều là đằng khác. Chính những lần vấp ngã đó dạy tôi bài học "
        f"mà không sách vở nào ghi lại đầy đủ.</p>"
        f"<h2>Kết luận</h2>"
        f"<p>Hãy bắt đầu từ những bước nhỏ. Bạn sẽ thấy khác biệt.</p>"
    )


def _guess_intent(user_prompt: str) -> str:
    """Echo a pre-provided intent if present, else pick a plausible one (dry-run only)."""
    if "đã được xác định sẵn:" in user_prompt:
        tail = user_prompt.split("đã được xác định sẵn:", 1)[1].strip()
        line = tail.splitlines()[0].strip(" .\"'")
        if line:
            return line[:40]
    low = user_prompt.lower()
    if any(w in low for w in ("mua", "giá", "đăng ký", "dịch vụ")):
        return "Transactional"
    if any(w in low for w in ("so sánh", "review", "đánh giá", "tốt nhất")):
        return "Commercial"
    return "Informational"


def _guess_keyword(user_prompt: str) -> str:
    """Pull a keyword out of the user prompt for nicer fakes; fallback generic."""
    for marker in ("Từ khóa:", "từ khóa:", "keyword:", "Keyword:", "H2:", "h2:"):
        if marker in user_prompt:
            tail = user_prompt.split(marker, 1)[1].strip()
            line = tail.splitlines()[0].strip(" .\"'")
            if line:
                return line[:80]
    return "Chủ đề mẫu"
