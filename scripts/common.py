"""Hàm dùng chung: đường dẫn, giờ Việt Nam, đọc/ghi JSON, chỉ mục dữ liệu."""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
DAILY = DATA / "daily"
WEEKLY = DATA / "weekly"
DOCS = ROOT / "docs"
try:
    VN_TZ = ZoneInfo("Asia/Ho_Chi_Minh")
except ZoneInfoNotFoundError:  # Windows không có sẵn dữ liệu múi giờ; Việt Nam cố định UTC+7
    VN_TZ = timezone(timedelta(hours=7))

WEEKDAYS_VI = ["thứ Hai", "thứ Ba", "thứ Tư", "thứ Năm", "thứ Sáu", "thứ Bảy", "Chủ nhật"]


def now_vn() -> datetime:
    return datetime.now(VN_TZ)


def session_for(dt: datetime) -> str:
    """Trước 12h là bản tin sáng, từ 12h là bản tin chiều."""
    return "sang" if dt.hour < 12 else "chieu"


def daily_key(d: date, session: str) -> str:
    return f"{d.isoformat()}-{session}"


def weekly_key(d: date) -> str:
    y, w, _ = d.isocalendar()
    return f"{y}-W{w:02d}"


def label_for(d: date, session: str) -> str:
    part = "Sáng" if session == "sang" else "Chiều"
    return f"{part} {d.day}/{d.month}/{d.year} ({WEEKDAYS_VI[d.weekday()]})"


def week_label(d: date) -> str:
    y, w, _ = d.isocalendar()
    mon = date.fromisocalendar(y, w, 1)
    fri = date.fromisocalendar(y, w, 5)
    return f"Tuần {mon.day}–{fri.day}/{fri.month}/{fri.year}" if mon.month == fri.month else \
        f"Tuần {mon.day}/{mon.month}–{fri.day}/{fri.month}/{fri.year}"


def read_json(p: Path):
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def write_json(p: Path, obj) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
        f.write("\n")
    tmp.replace(p)


def build_index() -> dict:
    """Ghi data/index.json liệt kê mọi bản tin để trang web biết cần tải file nào."""
    idx = {
        "daily": sorted(p.stem for p in DAILY.glob("*.json")),
        "weekly": sorted(p.stem for p in WEEKLY.glob("*.json")),
        "updatedAt": now_vn().isoformat(timespec="seconds"),
    }
    write_json(DATA / "index.json", idx)
    return idx
