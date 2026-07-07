# AutoContentPipeline

Tự động hóa quy trình: chọn từ khóa `Pending` từ file Excel → sinh nội dung SEO + caption
Fanpage (OpenAI GPT-4o) → tạo ảnh vuông 1:1 ẩn danh có gắn thương hiệu (Replicate Flux 1.1 Pro + Pillow)
→ chèn ảnh dưới các thẻ H2 (BeautifulSoup) → đăng **bản nháp** WordPress → đăng Album Facebook
→ ghi kết quả (trạng thái, URL, thời gian, lỗi) ngược lại file Excel. Mỗi lần chạy xử lý **1 dòng**.

## 1. Cài đặt

```powershell
python -m pip install -r requirements.txt
copy .env.example .env      # rồi điền giá trị thật (hoặc để DRY_RUN=1 để chạy thử)
```

## 2. Tạo file dữ liệu mẫu

```powershell
python main.py init-data
```

Sinh `data/keywords.xlsx` gồm:
- Sheet **Data**: 21 cột (8 cột input từ nghiên cứu từ khóa + các cột AI ghi lại + trạng thái), kèm vài dòng `Pending` mẫu.
- Sheet **Cấu Hình**: prompts, banned words, style ảnh.

## 3. Chạy thử (DRY-RUN — không tốn credit, không đăng gì)

```powershell
python main.py run-once --dry-run --keep-images
```

- Sinh nội dung giả lập, nhưng **ảnh WebP được tạo thật** vào `output/`.
- Giả lập upload WordPress / đăng bài nháp / Album Facebook, in URL giả.
- Ghi `Success` + URL + thời gian vào `keywords.xlsx`.
- `--keep-images` giữ lại ảnh trong `output/` để kiểm tra (mặc định sẽ dọn sạch).

## 4. Chạy thật

Đặt `DRY_RUN=0` trong `.env`, điền đủ khóa API, thay `assets/logo.png` bằng logo thật của bạn
(font mặc định `assets/fonts/font.ttf` = Arial Bold, hỗ trợ tiếng Việt — có thể thay bằng font riêng),
rồi chạy `python main.py run-once`. **Mỗi lần chạy chỉ xử lý đúng 1 dòng `Pending`.**

## 5. Lên lịch (bên ngoài)

Không còn scheduler nội bộ. Muốn chạy định kỳ, hãy để **Windows Task Scheduler** (hoặc cron) gọi
`python main.py run-once` theo tần suất bạn muốn — mỗi lần gọi xử lý 1 dòng tiếp theo. Ví dụ tạo
Basic Task chạy mỗi vài giờ với hành động `python C:\Users\Admin\Desktop\auto-post\main.py run-once`.

## Biến môi trường

Xem `.env.example`. Bí mật (API key/token) **chỉ** đọc từ biến môi trường, không bao giờ lưu trong
code hay Excel.

## Ghi chú

- Bài WordPress đăng ở trạng thái `draft` để bạn kiểm duyệt thủ công.
- Meta description ghi qua `excerpt` + (tùy chọn) trường của plugin SEO (Yoast/RankMath) — cấu hình
  `wordpress.seo_plugin` trong `settings.json`.
- Facebook Album cần **Page** (không phải profile cá nhân) và quyền `pages_manage_posts`.
