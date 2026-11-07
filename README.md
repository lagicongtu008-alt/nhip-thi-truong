# Nhịp Thị Trường

Bản tin kinh tế – tài chính Việt Nam (ngân hàng, chứng khoán, 5 ngành nổi bật, tổng hợp tuần), chuyển từ Claude sang chạy trên máy của bạn với **VS Code** và/hoặc **OpenAI Codex**.

Đã kèm sẵn toàn bộ dữ liệu cũ: 18 bản tin ngày (23/10 – 7/11/2026) và 3 bản tổng hợp tuần (W43–W45).

## 1. Cài đặt (một lần)
Cần Python 3.10 trở lên.

```bash
cd nhip-thi-truong
python -m venv .venv
# Windows:  .venv\Scripts\activate
# macOS/Linux:  source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # Windows: copy .env.example .env
```
Mở `.env`, điền `ANTHROPIC_API_KEY` (lấy tại console.anthropic.com). Khóa này dùng cho việc soạn tin tự động và tab Hỏi đáp. Chỉ xem bản tin cũ thì không cần khóa.

## 2. Xem trang
```bash
python server.py
```
Mở http://localhost:8000. (Mở trực tiếp file `web/index.html` sẽ không tải được dữ liệu, phải chạy qua server.)

## 3. Soạn bản tin mới
```bash
python scripts/collect.py              # tự chọn sáng/chiều theo giờ Việt Nam
python scripts/collect.py --session chieu --force
python scripts/collect.py --dry-run    # xem đề bài gửi cho AI
```
Kết quả ghi vào `data/daily/` (và `data/weekly/` với bản chiều thứ Hai–Sáu), sau đó tự cập nhật `data/index.json`. Trang web tự tải lại dữ liệu mỗi 5 phút.

Kiểm tra dữ liệu: `python scripts/validate.py`

Biến trong `.env`: `ANTHROPIC_MODEL` (model dùng), `MAX_SEARCHES` (số lượt tìm kiếm tối đa, mặc định 40). Mỗi lần soạn tốn phí API theo số token và số lượt web search.

## 4. Dùng trong VS Code
Mở thư mục bằng **File → Open Folder**. VS Code sẽ gợi ý cài extension Python. Vào **Terminal → Run Task…** để chạy:
- *Xem trang (server)*
- *Soạn bản tin (tự chọn buổi)*, *Soạn bản tin sáng*, *Soạn bản tin chiều*
- *Kiểm tra dữ liệu*

Hoặc nhấn F5 để chạy server/collect với debugger (cấu hình trong `.vscode/launch.json`).

## 5. Dùng với OpenAI Codex
Codex đọc `AGENTS.md` ở gốc dự án. Mở thư mục trong Codex (CLI, extension VS Code hoặc app) rồi yêu cầu, ví dụ:
- "Soạn bản tin chiều hôm nay theo quy trình" – Codex tự tìm tin, ghi JSON, chạy validate.
- "Chạy python scripts/collect.py" – dùng Claude API như trên.
- "Thêm biểu đồ VN-Index vào tab Chứng khoán" – sửa `web/index.html`.

Muốn đổi cách soạn tin (nguồn báo, số bài, ngành…), sửa `docs/QUY-TRINH-BAN-TIN.md`.

## 6. Chạy tự động 6h45 và 16h45
**macOS/Linux (cron)** – `crontab -e`, thêm (đổi đường dẫn):
```
45 6,16 * * * cd /duong/dan/nhip-thi-truong && .venv/bin/python scripts/collect.py >> logs/cron.log 2>&1
```
Máy phải đặt múi giờ Việt Nam, hoặc thêm dòng `CRON_TZ=Asia/Ho_Chi_Minh` phía trên.

**Windows** – chạy `scripts\schedule_windows.ps1` trong PowerShell (tạo 2 tác vụ trong Task Scheduler).

Máy cần bật vào giờ chạy. Muốn chạy kể cả khi tắt máy, có thể đưa dự án lên GitHub và dùng GitHub Actions (xem `.github/workflows/bantin.yml`, cần thêm secret `ANTHROPIC_API_KEY`).

## Cấu trúc
```
web/index.html        trang web
server.py             máy chủ cục bộ + API hỏi đáp
data/                 dữ liệu bản tin (JSON)
docs/QUY-TRINH-BAN-TIN.md  quy trình soạn tin (đề bài cho AI)
scripts/              collect.py, validate.py, build_index.py
AGENTS.md             hướng dẫn cho Codex
.vscode/              tasks, launch, settings
```

*Bản tin chỉ mang tính thông tin, không phải khuyến nghị đầu tư.*
