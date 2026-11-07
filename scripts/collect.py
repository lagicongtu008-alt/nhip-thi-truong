"""Soạn bản tin Nhịp Thị Trường bằng Claude API + công cụ web search.

    python scripts/collect.py                 # tự xác định buổi theo giờ Việt Nam
    python scripts/collect.py --session chieu # ép buổi chiều
    python scripts/collect.py --date 2026-11-06 --session chieu
    python scripts/collect.py --dry-run       # chỉ in prompt, không gọi API
    python scripts/collect.py --fetch         # cho phép Claude đọc toàn văn bài báo (web fetch, beta)

Cần ANTHROPIC_API_KEY trong file .env (xem .env.example). Quy trình chi tiết nằm ở
docs/QUY-TRINH-BAN-TIN.md: sửa file đó để đổi cách soạn tin, không cần sửa code này.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import date

from common import (DAILY, DOCS, ROOT, WEEKLY, build_index, daily_key, label_for, now_vn,
                    read_json, session_for, week_label, weekly_key, write_json)
from validate import check_daily, check_weekly

try:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
except ImportError:
    pass

MODEL = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-5-5")
MAX_SEARCHES = int(os.getenv("MAX_SEARCHES", "40"))
MAX_TOKENS = int(os.getenv("MAX_TOKENS", "32000"))
MAX_CONTINUES = 8


def process_text() -> str:
    text = (DOCS / "QUY-TRINH-BAN-TIN.md").read_text(encoding="utf-8")
    i = text.find("## Vai trò")
    return text[i:] if i >= 0 else text


def build_prompt(d: date, session: str, with_weekly: bool, weekly_existing: dict | None) -> str:
    now = now_vn()
    key = daily_key(d, session)
    parts = [
        process_text(),
        "\n---\n## Thông tin lần chạy này",
        f"- Giờ hiện tại (Việt Nam): {now.strftime('%H:%M %d/%m/%Y')}",
        f"- Bản tin cần soạn: key = \"{key}\", label = \"{label_for(d, session)}\", date = \"{d.isoformat()}\", session = \"{session}\"",
        f"- updatedAt dùng: \"{now.isoformat(timespec='seconds')}\"",
    ]
    if with_weekly:
        wk = weekly_key(d)
        parts.append(f"- Tổng hợp tuần: key = \"{wk}\", label = \"{week_label(d)}\"")
        if weekly_existing:
            parts.append("- Bản tổng hợp tuần hiện có (giữ các anomalies cũ, thêm mục mới của hôm nay):")
            parts.append("```json\n" + json.dumps(weekly_existing, ensure_ascii=False) + "\n```")
        else:
            parts.append("- Tuần này chưa có bản tổng hợp: tạo mới.")
    parts.append(
        "\n## Định dạng trả lời\n"
        "Sau khi tìm kiếm xong, trả lời DUY NHẤT bằng JSON hợp lệ, không markdown, không giải thích:\n"
        "<daily>{ ...bản tin ngày... }</daily>"
        + ("\n<weekly>{ ...tổng hợp tuần đầy đủ... }</weekly>" if with_weekly else "")
    )
    return "\n".join(parts)


def extract(tag: str, text: str) -> dict | None:
    m = re.search(rf"<{tag}>\s*(\{{.*\}})\s*</{tag}>", text, re.S)
    if not m:
        return None
    raw = m.group(1).strip()
    raw = re.sub(r"^```(?:json)?|```$", "", raw).strip()
    return json.loads(raw)


def run_claude(prompt: str, use_fetch: bool) -> str:
    import anthropic

    client = anthropic.Anthropic()
    tools: list[dict] = [{"type": "web_search_20250305", "name": "web_search", "max_uses": MAX_SEARCHES,
                          "user_location": {"type": "approximate", "country": "VN", "timezone": "Asia/Ho_Chi_Minh"}}]
    extra = {}
    if use_fetch:
        tools.append({"type": "web_fetch_20250910", "name": "web_fetch", "max_uses": 10})
        extra["betas"] = ["web-fetch-2025-09-10"]
    api = client.beta.messages if use_fetch else client.messages

    messages: list[dict] = [{"role": "user", "content": prompt}]
    texts: list[str] = []
    for turn in range(MAX_CONTINUES):
        with api.stream(model=MODEL, max_tokens=MAX_TOKENS, tools=tools, messages=messages, **extra) as stream:
            msg = stream.get_final_message()
        searches = sum(1 for b in msg.content if getattr(b, "type", "") == "server_tool_use")
        print(f"  lượt {turn + 1}: stop_reason={msg.stop_reason}, {searches} lượt tìm/đọc web", flush=True)
        texts.extend(b.text for b in msg.content if getattr(b, "type", "") == "text")
        if msg.stop_reason in ("pause_turn", "max_tokens"):
            # Server tạm dừng vòng tìm kiếm dài hoặc hết token: gửi lại để Claude làm tiếp
            messages.append({"role": "assistant",
                             "content": [b.model_dump(exclude_none=True) for b in msg.content]})
            if msg.stop_reason == "max_tokens":
                messages.append({"role": "user", "content": "Tiếp tục đúng chỗ đang dừng, không lặp lại phần đã viết."})
            continue
        break
    return "".join(texts)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--date", help="YYYY-MM-DD (mặc định: hôm nay giờ VN)")
    ap.add_argument("--session", choices=["sang", "chieu"])
    ap.add_argument("--force", action="store_true", help="ghi đè bản tin đã có")
    ap.add_argument("--fetch", action="store_true", help="bật web fetch (beta) để đọc toàn văn bài")
    ap.add_argument("--dry-run", action="store_true", help="chỉ in prompt")
    args = ap.parse_args()

    now = now_vn()
    d = date.fromisoformat(args.date) if args.date else now.date()
    session = args.session or session_for(now)
    weekend = d.weekday() >= 5
    if session == "chieu" and weekend:
        print("Chiều thứ Bảy/Chủ nhật không có bản tin. Kết thúc.")
        return 0

    key = daily_key(d, session)
    out = DAILY / f"{key}.json"
    if out.exists() and not args.force and not args.dry_run:
        print(f"{out.relative_to(ROOT)} đã tồn tại. Dùng --force để soạn lại.")
        return 0

    with_weekly = session == "chieu" and not weekend
    wk_path = WEEKLY / f"{weekly_key(d)}.json"
    wk_existing = read_json(wk_path) if with_weekly and wk_path.exists() else None

    prompt = build_prompt(d, session, with_weekly, wk_existing)
    if args.dry_run:
        print(prompt)
        return 0
    if not os.getenv("ANTHROPIC_API_KEY"):
        print("Thiếu ANTHROPIC_API_KEY. Sao chép .env.example thành .env và điền khóa.", file=sys.stderr)
        return 2

    print(f"Đang soạn {key} bằng {MODEL} (tối đa {MAX_SEARCHES} lượt tìm kiếm)…", flush=True)
    text = run_claude(prompt, args.fetch)
    raw_path = ROOT / "logs" / f"{key}.raw.txt"
    raw_path.parent.mkdir(exist_ok=True)
    raw_path.write_text(text, encoding="utf-8")

    try:
        daily = extract("daily", text)
        weekly = extract("weekly", text) if with_weekly else None
    except json.JSONDecodeError as e:
        print(f"JSON trả về không hợp lệ: {e}. Xem {raw_path.relative_to(ROOT)}", file=sys.stderr)
        return 1
    if not daily:
        print(f"Không tìm thấy <daily>…</daily> trong câu trả lời. Xem {raw_path.relative_to(ROOT)}", file=sys.stderr)
        return 1

    daily.update(key=key, date=d.isoformat(), session=session)
    daily.setdefault("label", label_for(d, session))
    daily.setdefault("updatedAt", now.isoformat(timespec="seconds"))
    write_json(out, daily)
    rep = check_daily(out)
    counts = f"ngân hàng {len(daily['banking'].get('articles', []))}, chứng khoán {len(daily['securities'].get('articles', []))}, " + \
        ", ".join(f"{s.get('name')} {len(s.get('articles', []))}" for s in daily.get("sectors", []))
    print(f"Đã ghi {out.relative_to(ROOT)}: {counts}")

    if weekly:
        weekly["key"] = weekly_key(d)
        weekly.setdefault("label", week_label(d))
        write_json(wk_path, weekly)
        rep_w = check_weekly(wk_path)
        print(f"Đã ghi {wk_path.relative_to(ROOT)}: {len(weekly.get('anomalies', []))} diễn biến bất thường")
        rep.errors += rep_w.errors
        rep.warns += rep_w.warns

    build_index()
    for m in rep.errors:
        print("  ERROR", m)
    for m in rep.warns:
        print("  WARN ", m)
    return 1 if rep.errors else 0


if __name__ == "__main__":
    sys.exit(main())
