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
Mở `.env`, điền `ANTHROPIC_API_KEY` (lấy tại console.anthropic.com). Khóa này chỉ dùng khi soạn tin bằng `collect.py` trên máy và cho tab Hỏi đáp. Chỉ xem bản tin, hoặc dùng bản tự động RSS, thì không cần khóa. Chạy trên GitHub không dùng khóa này (xem mục 7).

## 2. Xem trang
```bash
python server.py
```
Mở http://localhost:8000. (Mở trực tiếp file `web/index.html` sẽ không tải được dữ liệu, phải chạy qua server.)

- Tab **Phân tích** tổng hợp diễn biến qua tối đa 40 bản tin gần nhất (VN-Index, khối ngoại, thanh khoản, tỷ giá, lãi suất qua đêm, ngành và mã được nhắc nhiều, sắc thái tin, nguồn báo). Trang tự tính trong trình duyệt, không gọi AI.
- Trang **tự cập nhật**: kiểm tra dữ liệu mỗi 5 phút và khi bạn quay lại tab, tự chuyển sang bản mới và báo "Có bản tin mới".
- Tab **Hỏi đáp** chỉ hiện khi chạy `python server.py` (bản GitHub Pages không có máy chủ AI).
- Bản tin **tự động (RSS)** có nhãn "Bản tự động – chưa có nhận định AI" và hậu tố "· tự động" trong ô chọn bản tin. Bản này chỉ có danh sách bài (tiêu đề gốc, nguồn, link, giờ), không có số liệu và nhận định. Tab Phân tích bỏ qua các bản này và ghi rõ số bản đã bỏ qua.

## 3. Soạn bản tin mới
```bash
python scripts/collect.py              # tự chọn sáng/chiều theo giờ Việt Nam
python scripts/collect.py --session chieu --force
python scripts/collect.py --dry-run    # xem đề bài gửi cho AI
```
Kết quả ghi vào `data/daily/` (và `data/weekly/` với bản chiều thứ Hai–Sáu), sau đó tự cập nhật `data/index.json`. Trang web tự tải lại dữ liệu mỗi 5 phút.

Kiểm tra dữ liệu: `python scripts/validate.py`

**Bản tự động từ RSS (miễn phí, không cần AI):**
```bash
python scripts/rss_collect.py                  # ghi data/daily/<key>.json với "mode": "rss", rồi cập nhật index
python scripts/rss_collect.py --out ban-thu    # chạy thử: ghi vào ban-thu/daily/<key>.json, không đụng data/
python scripts/validate.py ban-thu/daily/<key>.json
python scripts/rss_collect.py --check-feeds    # kiểm từng feed: HTTP, số item, số item có ngày, bài mới nhất
```
Thay `<key>` bằng tên file vừa in ra (ví dụ `2026-11-07-sang`). Nguồn feed và từ khóa phân loại nằm ở `scripts/rss_config.json`. Script không bao giờ ghi đè bản tin AI (file không có `"mode": "rss"`); chiều thứ Bảy/Chủ nhật thì bỏ qua. Không có bài nào trong khung giờ thì thoát mã 3 và không ghi file.

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

Máy cần bật vào giờ chạy. Muốn chạy kể cả khi tắt máy, đưa dự án lên GitHub và dùng GitHub Actions (mục 7).

## 7. Chạy tự động trên GitHub (miễn phí, 2 tầng)
Workflow `.github/workflows/bantin.yml` chạy lúc 6h45 và 16h45 giờ Việt Nam (GitHub cron có thể trễ 5–30 phút), rồi đăng trang lên GitHub Pages.

- **Tầng 1 – bản tự động RSS (luôn chạy, không tốn phí):** `python scripts/rss_collect.py` đọc RSS của các báo Việt Nam và ghi bản tin `"mode": "rss"`.
- **Tầng 2 – Claude Code (gói Pro, khi có secret `CLAUDE_CODE_OAUTH_TOKEN`):** trong job `draft`, Claude Code CLI (`claude -p`) tự tìm tin theo `docs/QUY-TRINH-BAN-TIN.md` và ghi bản AI thay cho bản RSS. Job `publish` nhận đúng 2 file (bản ngày, bản tuần), rồi `scripts/ci_guard.py check` kiểm tra: bản AI hợp lệ thì giữ; hỏng (sai cấu trúc, JSON lỗi, file bị xóa, mất mục bất thường cũ của tuần…) thì khôi phục nguyên bản RSS.
- Không có secret thì workflow vẫn soạn bản tự động (RSS) và đăng trang, kèm cảnh báo trong log.

Các bước một lần:
1. Trên máy: `npm install -g @anthropic-ai/claude-code`, rồi `claude setup-token` (đăng nhập bằng tài khoản Claude Pro, chép token được in ra).
2. GitHub: Settings → Secrets and variables → Actions → New repository secret, tên `CLAUDE_CODE_OAUTH_TOKEN`, dán token. (Chỉ cần bản RSS thì bỏ qua bước 1–2.)
3. Settings → Pages → Source = **GitHub Actions**.
4. Actions → *Ban tin Nhip Thi Truong* → **Run workflow** để chạy thử (chọn buổi; bật *force* để Claude soạn lại dù đã có bản AI).

Rủi ro cần biết:
- **Hạn mức gói Pro:** mỗi lần chạy có thể dùng 25–40 lượt tìm kiếm. Hai lần mỗi ngày có thể chạm giới hạn sử dụng của gói, nhất là khi bạn đang dùng Claude cùng lúc. Chạm giới hạn thì bước Claude lỗi và trang giữ bản RSS.
- **Chất lượng:** model mặc định của gói và số lượt tìm kiếm có thể cho ít bài hơn mục tiêu. Bản AI chỉ cần 0 ERROR là được giữ; cảnh báo (WARN) về số bài vẫn chấp nhận.
- **Điều khoản:** hãy tự đọc điều khoản của Anthropic về việc dùng token OAuth của gói Pro/Claude Code trong CI tự động và tự cân nhắc. Tài liệu này không khẳng định việc đó được phép hay không.
- **Token:** token có thể hết hạn hoặc bị thu hồi. Khi đó chạy lại `claude setup-token` và cập nhật secret. Trong lúc chưa cập nhật, trang vẫn có bản RSS.
- **Bảo mật:** nội dung web mà Claude đọc có thể chứa chỉ dẫn độc hại (prompt injection). Workflow chia hai job:
  - Job `draft` chạy Claude, chỉ có quyền ĐỌC repo (`contents: read`), không có `GH_TOKEN`; checkout không lưu token git. Token gói Pro chỉ nằm trong env của đúng bước Claude.
  - Claude chỉ có `WebSearch`, `WebFetch` (chỉ các báo trong danh sách nguồn), `Read` (trong repo, chặn `/proc`, `/etc`, `~`, `.git`, thư mục tạm của runner) và ghi/sửa CHỈ trong `data/daily/` và `data/weekly/` (`--permission-mode dontAsk`: không có trong danh sách là bị từ chối; `--restricted`). Không có `Bash`: Claude không chạy được lệnh hay script nào, nên không thể "sửa script rồi chạy". Claude cũng không tự kiểm tra; job publish kiểm tra thay.
  - Bản RSS được gửi đi (artifact) TRƯỚC khi Claude chạy; sau Claude chỉ gửi đúng 2 file đích.
  - Job `publish` chạy trên runner mới, checkout sạch, chỉ nhận đúng 2 tên file JSON đó, không chạy `claude` và không chạy file nào do Claude ghi. Job này mới có quyền ghi repo, rồi `validate.py` trước khi commit.
  - Rủi ro còn lại: token Pro vẫn tồn tại trong môi trường của bước Claude (nếu CLI có lỗ thì có thể lộ); dữ liệu sai nhưng đúng cấu trúc (số liệu bịa, link sai) vẫn lọt qua lớp kiểm tra.
- **Chất lượng khi giới hạn WebFetch:** Claude chỉ đọc toàn văn được bài trên các báo trong danh sách (`FETCH_DOMAINS` của workflow); tin từ trang khác chỉ dựa trên kết quả tìm kiếm, nên bản AI có thể ít chi tiết hơn một chút. Muốn thêm báo thì thêm tên miền vào `FETCH_DOMAINS`.
- **Cú pháp CLI:** workflow cài đúng bản `@anthropic-ai/claude-code@2.1.319` (bản đã đối chiếu `claude --help`) và gọi `claude -p "<đề bài>" --restricted --strict-mcp-config --tools … --permission-mode dontAsk --allowedTools … --disallowedTools … --max-turns 150`, giới hạn 35 phút bằng `timeout`. Khi nâng phiên bản CLI, xem lại `claude --help` vì cú pháp cờ và luật quyền có thể đổi; nếu bước Claude báo lỗi cờ thì sửa bước "Tầng 2" trong workflow.
- **Hàng đợi:** GitHub chỉ giữ một lần chạy đang chờ cho mỗi nhóm `concurrency`. Nếu một lần chạy do push tới đúng lúc đang có lần soạn tin chờ, lần chờ có thể bị thay thế. Hiếm gặp, chấp nhận được.
- **Xung đột khi đẩy dữ liệu:** nếu trong lúc workflow chạy có người push thay đổi `data/`, bước commit (`git pull --rebase`) có thể đỏ; lần chạy đó mất bản tin, trang giữ bản cũ, và lần chạy sau tự bù.

## Cấu trúc
```
web/index.html        trang web
server.py             máy chủ cục bộ + API hỏi đáp
data/                 dữ liệu bản tin (JSON)
docs/QUY-TRINH-BAN-TIN.md  quy trình soạn tin (đề bài cho AI)
scripts/              collect.py (AI), rss_collect.py + rss_config.json (bản tự động RSS),
                      ci_guard.py (gác workflow GitHub), validate.py, build_index.py
tests/                python -m unittest discover -s tests ; node tests/web_core.test.js
AGENTS.md             hướng dẫn cho Codex
.vscode/              tasks, launch, settings
```

*Bản tin chỉ mang tính thông tin, không phải khuyến nghị đầu tư.*
