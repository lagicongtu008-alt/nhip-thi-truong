"""Kiểm tra cấu trúc và số lượng bài của các file bản tin.

    python scripts/validate.py                      # kiểm tra toàn bộ data/
    python scripts/validate.py data/daily/2026-11-07-sang.json

Lỗi (ERROR) = sai cấu trúc, trang web có thể hiển thị sai. Cảnh báo (WARN) = chưa đạt
mục tiêu số bài hoặc thiếu trường khuyến nghị.
"""
from __future__ import annotations

import re
import sys
from datetime import date
from pathlib import Path

from common import DAILY, WEEKLY, read_json

IMPACT = {"tich-cuc", "tieu-cuc", "trung-tinh"}
STATUS = {"soi-noi", "bat-thuong"}
SIGNAL = {"cao", "trung-binh", "thap"}
URL_RE = re.compile(r"^https?://[^\s]+$")


class Report:
    def __init__(self, name: str):
        self.name, self.errors, self.warns = name, [], []

    def err(self, m):
        self.errors.append(m)

    def warn(self, m):
        self.warns.append(m)


def check_articles(r: Report, arts, where: str, minimum: int) -> None:
    if not isinstance(arts, list):
        r.err(f"{where}.articles phải là mảng")
        return
    seen = set()
    for i, a in enumerate(arts):
        for f in ("title", "source", "url"):
            if not a.get(f):
                r.err(f"{where}.articles[{i}] thiếu '{f}'")
        u = a.get("url", "")
        if u and not URL_RE.match(u):
            r.err(f"{where}.articles[{i}] url không hợp lệ: {u}")
        if u in seen:
            r.err(f"{where}.articles[{i}] trùng URL: {u}")
        seen.add(u)
    if len(arts) < minimum:
        r.warn(f"{where}: {len(arts)} bài (mục tiêu ≥ {minimum})")


def check_market(r: Report, m, where: str, art_min: int) -> None:
    if not isinstance(m, dict):
        r.err(f"thiếu '{where}'")
        return
    if not m.get("summary"):
        r.err(f"{where}.summary trống")
    metrics = m.get("metrics") or []
    if not 4 <= len(metrics) <= 6:
        r.warn(f"{where}.metrics có {len(metrics)} ô (nên 4–6)")
    for i, x in enumerate(metrics):
        if not x.get("label") or not x.get("value"):
            r.err(f"{where}.metrics[{i}] thiếu label/value")
        if "change" in x and not isinstance(x["change"], (int, float)):
            r.err(f"{where}.metrics[{i}].change phải là số")
    news = m.get("news") or []
    if not 4 <= len(news) <= 6:
        r.warn(f"{where}.news có {len(news)} tin (nên 4–6)")
    for i, n in enumerate(news):
        if n.get("impact") not in IMPACT:
            r.err(f"{where}.news[{i}].impact phải thuộc {sorted(IMPACT)}")
        if n.get("url") and not URL_RE.match(n["url"]):
            r.err(f"{where}.news[{i}].url không hợp lệ")
    if not m.get("analysis"):
        r.warn(f"{where}.analysis trống")
    check_articles(r, m.get("articles", []), where, art_min)


def check_daily(p: Path) -> Report:
    r = Report(str(p))
    d = read_json(p)
    for f in ("key", "label", "date", "session", "updatedAt"):
        if not d.get(f):
            r.err(f"thiếu '{f}'")
    if d.get("key") != p.stem:
        r.err(f"key '{d.get('key')}' khác tên file '{p.stem}'")
    session = d.get("session")
    if session not in ("sang", "chieu"):
        r.err("session phải là 'sang' hoặc 'chieu'")
    try:
        weekend = date.fromisoformat(d["date"]).weekday() >= 5
    except Exception:
        r.err("date phải dạng YYYY-MM-DD")
        weekend = False
    trading_pm = session == "chieu" and not weekend
    check_market(r, d.get("banking"), "banking", 20 if trading_pm else 8)
    check_market(r, d.get("securities"), "securities", 25 if trading_pm else 8)
    secs = d.get("sectors") or []
    if len(secs) != 5:
        r.err(f"sectors phải có đúng 5 phần tử (đang có {len(secs)})")
    for i, s in enumerate(secs):
        w = f"sectors[{i}]({s.get('name', '?')})"
        if s.get("status") not in STATUS:
            r.err(f"{w}.status phải thuộc {sorted(STATUS)}")
        if not isinstance(s.get("heat"), int) or not 1 <= s["heat"] <= 5:
            r.err(f"{w}.heat phải là số nguyên 1–5")
        for st in s.get("stocks") or []:
            if "change" in st and not isinstance(st["change"], (int, float)):
                r.err(f"{w}.stocks.change phải là số")
        check_articles(r, s.get("articles", []), w, 5)
    return r


def check_weekly(p: Path) -> Report:
    r = Report(str(p))
    w = read_json(p)
    for f in ("key", "label", "headline", "summary", "updatedAt"):
        if not w.get(f):
            r.err(f"thiếu '{f}'")
    an = w.get("anomalies") or []
    if len(an) > 12:
        r.err(f"anomalies tối đa 12 mục (đang có {len(an)})")
    for i, a in enumerate(an):
        if a.get("signal") not in SIGNAL:
            r.err(f"anomalies[{i}].signal phải thuộc {sorted(SIGNAL)}")
        if a.get("url") and not URL_RE.match(a["url"]):
            r.err(f"anomalies[{i}].url không hợp lệ")
    return r


def main(argv: list[str]) -> int:
    files = [Path(a) for a in argv] or sorted(DAILY.glob("*.json")) + sorted(WEEKLY.glob("*.json"))
    total_err = 0
    for p in files:
        r = check_weekly(p) if p.parent.name == "weekly" else check_daily(p)
        status = "OK " if not r.errors else "ERR"
        print(f"[{status}] {p.name}" + (f"  ({len(r.warns)} cảnh báo)" if r.warns else ""))
        for m in r.errors:
            print(f"    ERROR {m}")
        for m in r.warns:
            print(f"    WARN  {m}")
        total_err += len(r.errors)
    return 1 if total_err else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
