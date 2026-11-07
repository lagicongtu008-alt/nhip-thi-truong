# AGENTS.md – hướng dẫn cho Codex (và các AI agent khác)

Dự án **Nhịp Thị Trường**: bản tin kinh tế – tài chính Việt Nam, mỗi ngày 2 bản (sáng 6h45, chiều 16h45 giờ VN), hiển thị trên một trang web tĩnh đọc dữ liệu JSON.

## Cấu trúc
```
web/index.html            Trang web (một file, không build). Đọc /data/index.json rồi tải từng bản tin.
server.py                 Máy chủ cục bộ: phục vụ web/ và data/, endpoint POST /api/ask cho tab Hỏi đáp.
data/index.json           Danh sách key bản tin; tạo lại bằng scripts/build_index.py.
data/daily/<key>.json     Bản tin ngày, key = YYYY-MM-DD-sang | YYYY-MM-DD-chieu.
data/weekly/<YYYY-Www>.json  Tổng hợp tuần theo tuần ISO.
docs/QUY-TRINH-BAN-TIN.md Quy trình + cấu trúc JSON. ĐÂY LÀ NGUỒN CHUẨN – đọc trước khi soạn tin.
scripts/collect.py        Soạn bản tin tự động qua Claude API (web search). --dry-run --agent: đề bài cho agent tự ghi file.
scripts/rss_collect.py    Bản tin tự động từ RSS ("mode": "rss"), không cần AI. Cấu hình: scripts/rss_config.json.
scripts/ci_guard.py       Gác workflow GitHub: plan / snapshot / import / check (khôi phục bản RSS nếu bản AI hỏng).
scripts/validate.py       Kiểm tra cấu trúc và số bài.
scripts/build_index.py    Tạo lại data/index.json.
```

## Khi được yêu cầu "soạn bản tin" / "chạy bản tin"
Có hai cách. Hỏi người dùng nếu không rõ; mặc định dùng cách A khi có web search.

**A. Agent tự soạn (Codex có web search):**
1. Chạy `python scripts/collect.py --dry-run` để biết giờ Việt Nam, key, label (và key tuần nếu là chiều ngày giao dịch) cùng đề bài đầy đủ. Thêm `--session sang|chieu` hoặc `--date YYYY-MM-DD` nếu người dùng chỉ định.
2. Làm đúng `docs/QUY-TRINH-BAN-TIN.md`: tìm kiếm nhiều lượt, kiểm tra ngày đăng từng bài, chỉ dùng URL thật từ kết quả tìm kiếm.
3. Ghi `data/daily/<key>.json` (UTF-8, giữ đúng tên trường). Nếu file đó đang là bản tự động (`"mode": "rss"`) thì được ghi đè toàn bộ và phải BỎ trường `mode`; danh sách bài trong bản RSS chỉ là gợi ý, phải tự kiểm ngày đăng. Chiều thứ Hai–Sáu: cập nhật `data/weekly/<YYYY-Www>.json`, giữ các `anomalies` cũ.
   (Đề bài kèm hướng dẫn ghi file cho agent: `python scripts/collect.py --dry-run --agent`.)
4. Chạy `python scripts/validate.py data/daily/<key>.json` – sửa hết ERROR, cố gắng giảm WARN về số bài.
5. Chạy `python scripts/build_index.py`.
6. Báo lại: key đã ghi, số bài từng mảng, 1–2 ý nổi bật.

**B. Chạy script:** `python scripts/collect.py` (cần `ANTHROPIC_API_KEY` trong `.env`).

**C. Bản tự động không cần AI:** `python scripts/rss_collect.py` (không bao giờ ghi đè bản AI). Trên GitHub Actions, workflow luôn chạy tầng RSS rồi, nếu có secret `CLAUDE_CODE_OAUTH_TOKEN`, chạy Claude Code với đề bài `collect.py --dry-run --agent`.

## Quy tắc nội dung (bắt buộc)
- Không bịa số liệu; không chắc thì bỏ trường. Không tự tạo URL.
- Tiêu đề bài giữ nguyên; tóm tắt viết bằng lời mình, tiếng Việt.
- Không khuyến nghị mua/bán mã cụ thể.
- Chiều thứ Bảy/Chủ nhật: không soạn.

## Quy tắc code
- `web/index.html` là một file tự chứa, không framework, không bước build. Giữ nguyên tên trường JSON vì trang hiển thị dựa vào chúng.
- Python ≥ 3.10, chỉ phụ thuộc `anthropic` và `python-dotenv` (requirements.txt).
- Không commit `.env`, `logs/`.
- Sau khi sửa script: `python scripts/validate.py` phải trả về 0 trên dữ liệu hiện có.
