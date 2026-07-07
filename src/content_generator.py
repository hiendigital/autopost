"""Module 2: Humanized Content Generator (OpenAI GPT-4o).

Encodes the anti-AI-detector rules from the spec: perplexity/burstiness (mix of short
punchy sentences and long explanatory ones), banned-words filtering, and a first-person
E-E-A-T expert voice.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime

from .clients import openai_client

logger = logging.getLogger(__name__)

_ANTI_DETECTOR_RULES = (
    "QUY TẮC BẮT BUỘC để văn bản tự nhiên, CUỐN HÚT như một người thật giàu kinh nghiệm viết:\n"
    "1. Nhịp điệu (burstiness): xen kẽ câu ngắn dưới 10 từ để nhấn mạnh và câu dài để giải thích. "
    "Tránh việc mọi câu đều dài đều đều một nhịp.\n"
    "2. Ngôi kể E-E-A-T: dùng ngôi thứ nhất (tôi/chúng tôi), kể trải nghiệm THỰC TẾ và CỤ THỂ của "
    "một chuyên gia — có tình huống, có con số, có ví dụ đời thường, kể cả lần từng làm sai và bài học rút ra.\n"
    "3. Chi tiết cụ thể thay cho nói chung chung: nêu con số, mốc thời gian, tên công cụ, chi phí ước "
    "lượng, ví dụ tình huống thật... thay vì những phát biểu mơ hồ ai cũng nói được.\n"
    "4. Có quan điểm cá nhân: dám khuyên nên/không nên, chỉ ra lỗi người mới hay mắc; thỉnh thoảng "
    "dùng câu hỏi tu từ để trò chuyện trực tiếp với người đọc.\n"
    "5. Tuyệt đối KHÔNG dùng các từ/cụm sáo rỗng sau: {banned}.\n"
    "6. Không mở đầu bằng câu chào máy móc, không liệt kê lộ liễu kiểu AI, không viết kiểu 'an toàn' "
    "chung chung nhạt nhẽo.\n"
)


def _rules_block(banned_words: list[str]) -> str:
    return _ANTI_DETECTOR_RULES.format(banned=", ".join(banned_words) if banned_words else "(không có)")


def generate_seo_post(
    keyword: str,
    audience: str,
    intent: str,
    category: str,
    system_prompt_web_seo: str,
    banned_words: list[str],
    model: str = "gpt-4o",
    internal_links: list[dict] | None = None,
    all_keywords: str = "",
) -> dict:
    """Return a fully-structured SEO article.

    Keys: 'intent', 'seo_title', 'h1', 'title', 'meta_description', 'sapo',
    'lsi_keywords', 'html_content'. ('title' aliases 'h1' for backward compatibility.)

    Inputs come straight from the Data sheet: `keyword` = "Từ khóa chính", `all_keywords`
    = "Toàn bộ từ khóa cùng nhóm" (comma/newline list to weave in naturally), `intent` =
    "Ý định tìm kiếm của người dùng", `audience` = "Bài viết sẽ nhắm đến đối tượng".
    The body must directly SOLVE the given intent for the given audience.

    If `intent` is empty, the model self-analyzes the keyword + audience to determine the
    search intent. `internal_links` is a list of {'title','url'} of existing site posts the
    model must weave into the body as contextual internal links (5-7 recommended).
    """
    system = f"{system_prompt_web_seo}\n\n{_rules_block(banned_words)}"

    intent = (intent or "").strip()
    if intent:
        intent_instruction = (
            f"Ý định tìm kiếm (intent) đã được xác định sẵn: {intent}.\n"
            f"Giữ nguyên intent này và viết bài GIẢI QUYẾT ĐÚNG ý định đó."
        )
    else:
        intent_instruction = (
            "Ý định tìm kiếm CHƯA được cung cấp. Hãy TỰ PHÂN TÍCH từ khóa và đối tượng độc giả "
            "để xác định ý định tìm kiếm phù hợp nhất, chọn ĐÚNG MỘT trong: "
            "Informational, Commercial, Transactional, Navigational."
        )

    all_keywords = (all_keywords or "").strip()
    if all_keywords:
        group_kw_instruction = (
            f"BỘ TỪ KHÓA CÙNG NHÓM (từ khóa mục tiêu có thật, BẮT BUỘC bao phủ): {all_keywords}.\n"
            f"Hãy phủ TỰ NHIÊN các từ khóa mục tiêu này trong thân bài và một vài thẻ <h2> để "
            f"bài viết bao trọn ý định tìm kiếm của cả nhóm. TUYỆT ĐỐI không nhồi nhét gượng ép."
        )
    else:
        group_kw_instruction = (
            "Không có bộ từ khóa cùng nhóm; hãy tự bám sát từ khóa chính và ý định tìm kiếm."
        )

    internal_links = internal_links or []
    if internal_links:
        links_lines = "\n".join(
            f'  - {l["title"]} -> {l["url"]}' for l in internal_links if l.get("url")
        )
        internal_link_instruction = (
            f"CHÈN LINK NỘI BỘ (bắt buộc): dưới đây là các bài viết VÀ trang dịch vụ đã có trên "
            f"website (ưu tiên trỏ tới các trang dịch vụ liên quan để hỗ trợ SEO cho chúng). "
            f"Hãy chèn TỰ NHIÊN 5-7 link trong số này vào thân bài dưới dạng "
            f'<a href="URL">anchor text có nghĩa</a>, đặt trong ngữ cảnh liên quan. '
            f"CHỈ dùng đúng các URL sau, KHÔNG bịa URL mới:\n{links_lines}\n"
        )
    else:
        internal_link_instruction = (
            "CHÈN LINK NỘI BỘ: không có dữ liệu bài viết nội bộ, hãy BỎ QUA phần link nội bộ "
            "(tuyệt đối không bịa link nội bộ)."
        )

    current_year = datetime.now().year

    user = (
        f"Viết một bài blog chuẩn SEO hoàn chỉnh, chuyên sâu, dài KHOẢNG 1500 từ.\n"
        f"Từ khóa chính: {keyword}\n"
        f"Đối tượng độc giả: {audience}\n"
        f"Chuyên mục: {category}\n"
        f"{intent_instruction}\n"
        f"{group_kw_instruction}\n\n"
        f"MỤC TIÊU QUAN TRỌNG NHẤT của phần Nội dung website: phải BÁM SÁT và GIẢI QUYẾT TRỌN VẸN "
        f"ý định tìm kiếm ('{intent or 'tự xác định'}') cho đúng đối tượng độc giả ('{audience}'). "
        f"Viết như thể trả lời trực tiếp cho chính người đó: đúng mối bận tâm, đúng trình độ, đúng "
        f"ngữ cảnh của họ; mỗi mục <h2> nên xử lý một khía cạnh của nhu cầu đó.\n\n"
        f"YÊU CẦU CẤU TRÚC & SEO (bắt buộc tuân thủ):\n"
        f"1. seo_title: tiêu đề SEO tối đa 60 ký tự, chứa từ khóa chính, hấp dẫn. "
        f"NÊN chèn một CON SỐ khi hợp tự nhiên (số lượng mẹo/bước/mẫu/lưu ý, hoặc năm) "
        f"để tăng tỉ lệ nhấp — VD 'Top 7...', '5 cách...', '... {current_year}'. Chỉ thêm khi hợp "
        f"ngữ cảnh, không gán số một cách gượng ép. NẾU dùng năm, BẮT BUỘC dùng năm hiện tại "
        f"{current_year} — TUYỆT ĐỐI không dùng năm cũ (2023/2024/...).\n"
        f"2. h1: tiêu đề H1 KHÁC seo_title (diễn đạt khác đi) nhưng VẪN chứa từ khóa chính. "
        f"H1 chính là TÊN BÀI VIẾT hiển thị.\n"
        f"3. meta_description: mô tả meta 150-160 ký tự, chứa từ khóa chính, kêu gọi nhấp chuột.\n"
        f"4. sapo: đoạn mở bài 200-300 ký tự, cuốn hút, chứa từ khóa chính, đặt vấn đề.\n"
        f"5. Bài viết ~1500 từ, ít nhất 5-6 thẻ <h2> (mỗi mục có nhiều đoạn <p>). "
        f"Có thể dùng <h3>, <ul>/<li>, <strong> khi hợp lý.\n"
        f"6. MẬT ĐỘ TỪ KHÓA CHÍNH: dùng chính xác từ khóa chính '{keyword}' một cách TỰ NHIÊN ở các "
        f"vị trí quan trọng — trong đoạn mở đầu (100 từ đầu), trong ÍT NHẤT 1-2 thẻ <h2>, và rải "
        f"thêm vài lần hợp lý trong thân bài (mục tiêu mật độ ~1%). Nếu từ khóa quá dài/gượng, được "
        f"phép dùng biến thể gần đúng, NHƯNG tuyệt đối KHÔNG nhồi nhét làm câu văn trúc trắc.\n"
        f"7. TỪ KHÓA LSI: hãy TỰ NGHĨ RA 8-12 từ khóa LSI (từ/cụm đồng nghĩa, liên quan ngữ nghĩa, "
        f"hay đi kèm chủ đề) — ĐÂY LÀ VIỆC CỦA BẠN, KHÁC với bộ từ khóa cùng nhóm cho sẵn ở trên. "
        f"Rải các từ khóa LSI này THẬT TỰ NHIÊN khắp bài và trong vài thẻ <h2> để tăng độ liên quan "
        f"chủ đề; ưu tiên câu văn mượt, TUYỆT ĐỐI không nhồi nhét gượng ép. "
        f"Liệt kê đúng các từ khóa LSI bạn đã dùng ở khóa lsi_keywords.\n"
        f"8. {internal_link_instruction}\n"
        f"9. CHÈN LINK NGOÀI (bắt buộc): thêm 1-2 link ra ngoài trỏ tới các nguồn UY TÍN, có thật, "
        f'ổn định (ví dụ Wikipedia, trang cơ quan nhà nước .gov.vn, báo lớn, tổ chức chính thống). '
        f'ÍT NHẤT MỘT link phải là DOFOLLOW dạng <a href="URL" target="_blank" rel="noopener">anchor</a> '
        f"(KHÔNG có nofollow) — đây là link uy tín chính. "
        f"Chỉ dùng domain nổi tiếng có thật, không bịa đường dẫn sâu khó xác thực.\n"
        f"10. html_content phải BẮT ĐẦU bằng đoạn sapo (thẻ <p>), rồi tới các mục <h2>. "
        f"TUYỆT ĐỐI KHÔNG chèn thẻ <h1> trong html_content (H1 do WordPress tự render). "
        f"KHÔNG bọc trong <html> hay <body>.\n\n"
        f"Bám sát intent đã xác định: Informational→giải thích/hướng dẫn đầy đủ; "
        f"Commercial→so sánh, đánh giá ưu nhược; Transactional→kêu gọi hành động cụ thể; "
        f"Navigational→dẫn tới thương hiệu/trang cụ thể.\n\n"
        f"Trả về JSON với đúng các khóa: "
        f'"intent", "seo_title", "h1", "meta_description", "sapo", "lsi_keywords" (mảng chuỗi), '
        f'"html_content".'
    )
    data = openai_client.chat_json(system, user, model=model)

    lsi = data.get("lsi_keywords", [])
    if isinstance(lsi, str):
        lsi = [w.strip() for w in lsi.split(",") if w.strip()]

    seo_title = _truncate(_strip_banned(str(data.get("seo_title", "")).strip(), banned_words), 60)
    h1 = _strip_banned(str(data.get("h1", data.get("title", ""))).strip(), banned_words)
    meta_description = _truncate(
        _strip_banned(str(data.get("meta_description", "")).strip(), banned_words), 160
    )
    result = {
        "intent": str(data.get("intent", "")).strip() or intent,
        "seo_title": seo_title or h1,
        "h1": h1,
        "title": h1,  # backward-compat: callers that used post["title"] as the H1
        "meta_description": meta_description,
        "sapo": _strip_banned(str(data.get("sapo", "")).strip(), banned_words),
        "lsi_keywords": [str(w).strip() for w in lsi if str(w).strip()],
        "html_content": _strip_banned(str(data.get("html_content", "")).strip(), banned_words),
    }
    return result


def _truncate(text: str, limit: int) -> str:
    """Trim to `limit` chars without cutting a word in half."""
    if not text or len(text) <= limit:
        return text
    cut = text[:limit]
    if " " in cut:
        cut = cut[: cut.rfind(" ")]
    return cut.rstrip(" ,.;:-")


def generate_fanpage_caption(
    keyword: str,
    audience: str,
    system_prompt_social: str,
    banned_words: list[str],
    model: str = "gpt-4o",
    intent: str = "",
) -> str:
    """Return a Facebook post that hooks the reader and solves their problem.

    `audience` ("Bài viết sẽ nhắm đến đối tượng") picks and shapes the TONE. `intent`
    ("Ý định tìm kiếm của người dùng") answers "what are they worried about?" and becomes
    the hook + the substance of the post. Output has emojis + hashtags, reads like a real
    person (not AI), stays within Facebook page policy.
    """
    system = f"{system_prompt_social}\n\n{_rules_block(banned_words)}"

    intent = (intent or "").strip()
    intent_line = (
        f"Mối bận tâm của người đọc (họ đang lo/khó ở điều gì): {intent}\n"
        if intent
        else "Mối bận tâm của người đọc: tự suy luận từ chủ đề và đối tượng bên dưới.\n"
    )
    user = (
        f"Viết một bài đăng Facebook (KHÔNG phải bài blog) cho Fanpage về chủ đề: {keyword}\n"
        f"Đối tượng nhắm tới: {audience}\n"
        f"{intent_line}\n"
        f"YÊU CẦU BẮT BUỘC:\n"
        f"1. GIỌNG ĐIỆU: dựa vào đối tượng ở trên để chọn cách xưng hô và giọng phù hợp — "
        f"trẻ trung nhưng chuyên nghiệp, gần gũi, đáng tin, KHÔNG sến, KHÔNG như quảng cáo rao vặt.\n"
        f"2. HOOK: 1-2 câu đầu phải chạm ngay vào mối bận tâm của người đọc (biến điều họ đang lo "
        f"thành câu hỏi hoặc tình huống thật) để họ dừng lại và đọc tiếp.\n"
        f"3. NỘI DUNG: đi thẳng vào GIẢI QUYẾT vấn đề đó — cho lời khuyên/bước làm/gợi ý cụ thể, "
        f"có giá trị thật, đọc xong thấy được gỡ rối. Độ dài vừa phải cho Facebook (khoảng 120-250 từ), "
        f"chia đoạn ngắn dễ đọc, xuống dòng thoáng.\n"
        f"4. VĂN PHONG NGƯỜI THẬT: viết như một người thật đang chia sẻ, có cảm xúc và trải nghiệm; "
        f"TUYỆT ĐỐI không đều đều, khuôn mẫu, lộ chất AI.\n"
        f"5. ĐỊNH DẠNG FANPAGE: chèn icon/emoji hợp cảnh rải tự nhiên (kể cả đầu vài dòng), kết thúc "
        f"bằng một CTA nhẹ nhàng (mời bình luận/nhắn tin/đọc thêm) và 3-6 hashtag tiếng Việt liên quan.\n"
        f"6. CHÍNH SÁCH: tuyệt đối không vi phạm chính sách Facebook (không cam kết tuyệt đối, không "
        f"giật gân sai sự thật, không nội dung nhạy cảm/cấm).\n"
        f"Chỉ trả về đúng phần nội dung bài đăng, không giải thích, không tiêu đề phụ."
    )
    caption = openai_client.chat_text(system, user, model=model)
    return _strip_banned(caption, banned_words)


def _strip_banned(text: str, banned_words: list[str]) -> str:
    """Safety net: remove any banned phrases the model may have slipped in."""
    if not text or not banned_words:
        return text
    for word in banned_words:
        if not word:
            continue
        text = re.sub(re.escape(word), "", text, flags=re.IGNORECASE)
    # Tidy up double spaces / stray punctuation left behind.
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"\s+([,.!?])", r"\1", text)
    return text.strip()
