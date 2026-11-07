"""Tầng tự động RSS: soạn bản tin "mode": "rss" từ feed báo Việt Nam, không cần AI, không tốn phí.

    python scripts/rss_collect.py                       # tự chọn ngày/buổi theo giờ Việt Nam
    python scripts/rss_collect.py --date 2026-11-06 --session chieu
    python scripts/rss_collect.py --out /tmp/ntt        # ghi vào /tmp/ntt/daily/<key>.json
    python scripts/rss_collect.py --check-feeds         # chỉ kiểm từng feed, không ghi file

Bản RSS chỉ có danh sách bài (tiêu đề gốc, nguồn, link, giờ), không có số liệu thị trường,
không có nhận định, không chép sapo của báo. Không bao giờ ghi đè bản tin AI (file không có
"mode": "rss"). Mã thoát: 0 = đã ghi hoặc bỏ qua hợp lệ; 3 = không có bài nào trong khung giờ.
Cấu hình: --config, hoặc biến môi trường NTT_RSS_CONFIG, mặc định scripts/rss_config.json.
"""
from __future__ import annotations

import argparse
import html
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from common import (DATA, ROOT, VN_TZ, build_index, daily_key, label_for, now_vn, read_json,
                    session_for, setup_stdio, write_json)

DEFAULT_CONFIG = Path(__file__).resolve().parent / "rss_config.json"
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
ACCEPT = "application/rss+xml, application/atom+xml, application/xml;q=0.9, text/xml;q=0.9, */*;q=0.8"
FUTURE_SLACK = timedelta(minutes=10)
HINT_BONUS = 2  # mặc định khi rss_config.json không có "hint_bonus"
LIMITS = {"banking": 60, "securities": 60, "sector": 20}
GROUP_TEXT = {"banking": "ngân hàng – tiền tệ", "securities": "chứng khoán"}
URL_RE = re.compile(r"^https?://\S+$")

# ---------------------------------------------------------------- chuẩn hóa văn bản
_TAG = re.compile(r"<[^>]*>")
_WS = re.compile(r"[\s​‌‍⁠﻿]+")


def squash(s: str) -> str:
    """Gộp mọi khoảng trắng Unicode (U+00A0, U+202F, xuống dòng…) thành một dấu cách."""
    return _WS.sub(" ", s or "").strip()


def clean(s: str | None, title: bool = False) -> str:
    """Giải mã thực thể → bỏ thẻ → giải mã lần 2 (nội dung bị escape hai lần) → gộp khoảng trắng.

    title=True: chỉ bỏ thẻ HTML có thật trong chuỗi thô (ví dụ trong CDATA), không bỏ ký tự
    `<`/`>` xuất hiện sau khi giải mã (tiêu đề như "Lãi suất <5%" giữ nguyên)."""
    if not s:
        return ""
    if title:
        s = _TAG.sub(" ", s)
        s = html.unescape(html.unescape(s))
    else:
        s = _TAG.sub(" ", html.unescape(s))
        s = _TAG.sub(" ", html.unescape(s))
    return squash(s)


# ---------------------------------------------------------------- ngày đăng
_ISO = re.compile(r"^\d{4}-\d{2}-\d{2}")
# Tuổi Trẻ: "11/4/2026 7:01:00 AM" = tháng/ngày/năm, 12 giờ. Bắt buộc có AM/PM.
_MDY_AMPM = re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{4}) (\d{1,2}):(\d{2})(?::(\d{2}))? ?([AaPp])\.?[Mm]\.?$")
# "GMT+7", "GMT+07:00" (một số báo Việt) → "+0700" để email.utils đọc được.
_GMT_OFF = re.compile(r"(?:GMT|UTC)\s*([+-])(\d{1,2})(?::?(\d{2}))?$", re.I)


def to_vn(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=VN_TZ)
    return dt.astimezone(VN_TZ)


def parse_date(s: str | None) -> datetime | None:
    """RFC 822, ISO 8601 (cả 'Z'), 'M/D/YYYY h:mm[:ss] AM/PM'. Trả giờ VN hoặc None."""
    s = squash(s or "")
    if not s:
        return None
    dt = None
    m = _MDY_AMPM.match(s)
    if m:
        mo, d, y, h, mi = (int(m.group(i)) for i in range(1, 6))
        sec = int(m.group(6) or 0)
        if not 1 <= h <= 12:
            return None
        h = h % 12 + (12 if m.group(7) in "Pp" else 0)
        try:
            dt = datetime(y, mo, d, h, mi, sec)
        except ValueError:
            return None
    elif _ISO.match(s):
        iso = s[:-1] + "+00:00" if s[-1] in "Zz" else s
        try:
            dt = datetime.fromisoformat(iso)
        except ValueError:
            return None
    else:
        g = _GMT_OFF.search(s)
        if g:
            s = s[:g.start()] + f"{g.group(1)}{int(g.group(2)):02d}{g.group(3) or '00'}"
        try:
            dt = parsedate_to_datetime(s)
        except (TypeError, ValueError, IndexError, OverflowError):
            return None
    if dt is None:
        return None
    try:
        return to_vn(dt)
    except (ValueError, OverflowError):
        return None


# ---------------------------------------------------------------- link
def canon_url(u: str) -> str:
    """Khóa chống trùng: hạ chữ thường scheme+host, bỏ fragment, bỏ utm_*, bỏ '/' cuối."""
    u = (u or "").strip()
    try:
        p = urlsplit(u)
    except ValueError:
        return u
    q = urlencode([(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True)
                   if not k.lower().startswith("utm_")], doseq=True)
    return urlunsplit((p.scheme.lower(), p.netloc.lower(), p.path.rstrip("/"), q, ""))


# ---------------------------------------------------------------- đọc feed
def _name(tag) -> str:
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def _child(el, *names):
    for n in names:
        for c in el:
            if _name(c.tag) == n:
                return c
    return None


def _text(el) -> str:
    return "" if el is None else "".join(el.itertext())


def _atom_link(entry) -> str:
    links = [c for c in entry if _name(c.tag) == "link"]
    for c in links:
        if c.get("rel", "alternate") == "alternate" and c.get("href"):
            return c.get("href")
    for c in links:
        if c.get("href"):
            return c.get("href")
        if (c.text or "").strip():
            return c.text.strip()
    return ""


def _rss_link(item) -> str:
    link = _text(_child(item, "link")).strip()
    if link:
        return link
    guid = _child(item, "guid")
    if guid is not None:
        g = _text(guid).strip()
        if g.startswith(("http://", "https://")) and guid.get("isPermaLink", "true").lower() != "false":
            return g
    return ""


def _item(title: str, link: str, desc: str, pub: str, source: str) -> dict:
    return {"title": clean(title, title=True), "link": squash(html.unescape(link or "")),
            "desc": clean(desc), "published": parse_date(pub), "source": source}


def parse_feed_regex(text: str, source: str) -> list[dict]:
    """Dự phòng cho feed XML lỗi cú pháp (giống news_tool.parse_feed_regex)."""
    def grab(block, tag):
        m = re.search(rf"<{tag}(?:\s[^>]*)?>(.*?)</{tag}>", block, re.S | re.I)
        if not m:
            return ""
        v = m.group(1).strip()
        return re.sub(r"^<!\[CDATA\[(.*)\]\]>$", r"\1", v, flags=re.S)
    out = []
    for b in re.findall(r"<item[\s>].*?</item>", text, re.S | re.I):
        link = grab(b, "link")
        if not link.strip():
            g = grab(b, "guid").strip()
            link = g if g.startswith(("http://", "https://")) else ""
        out.append(_item(grab(b, "title"), link, grab(b, "description"), grab(b, "pubDate"), source))
    return out


def parse_feed(data: bytes, source: str) -> list[dict]:
    """RSS 2.0 / Atom → [{title, link, desc, published: datetime giờ VN | None, source}]."""
    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        return parse_feed_regex(data.decode("utf-8", "replace"), source)
    out = []
    for el in root.iter():
        n = _name(el.tag)
        if n == "item":
            out.append(_item(_text(_child(el, "title")), _rss_link(el),
                             _text(_child(el, "description", "summary", "encoded")),
                             _text(_child(el, "pubDate", "date", "published", "updated")), source))
        elif n == "entry":
            out.append(_item(_text(_child(el, "title")), _atom_link(el),
                             _text(_child(el, "summary", "content")),
                             _text(_child(el, "published", "updated")), source))
    return [x for x in out if x["title"] and x["link"]]


def fetch(url: str, timeout: float = 20) -> tuple[bytes, str]:
    """Tải feed (thử lại 1 lần). Trả (bytes, mô tả trạng thái). Ném lỗi nếu cả hai lần hỏng."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": ACCEPT})
    last: Exception | None = None
    for attempt in range(2):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                st = getattr(r, "status", None)
                return r.read(), (f"HTTP {st}" if st else "OK")
        except (urllib.error.URLError, OSError, ValueError) as e:
            last = e
            if attempt == 0:
                time.sleep(1)
    raise last  # type: ignore[misc]


def fetch_all(sources: list[dict], timeout: float, max_items: int):
    """Tải song song. Trả (danh sách (src, items, trạng thái)), danh sách feed lỗi)."""
    def one(src):
        try:
            data, st = fetch(src["url"], timeout)
            items = parse_feed(data, src["name"])
            # Một số feed (VietnamNet ~1000 item) không sắp theo ngày: sắp mới nhất trước rồi mới cắt.
            items.sort(key=lambda x: x["published"].timestamp() if x["published"] else float("-inf"), reverse=True)
            return src, items[:max_items], st
        except Exception as e:  # feed lỗi không làm dừng script
            return src, None, f"lỗi: {type(e).__name__}: {e}"
    with ThreadPoolExecutor(8) as ex:
        res = list(ex.map(one, sources))
    failed = [f"{s['name']} {s['url']}" for s, items, _ in res if items is None]
    return res, failed


# ---------------------------------------------------------------- cửa sổ thời gian
def window_for(d: date, session: str, now: datetime) -> tuple[datetime, datetime]:
    if session == "sang":
        start = datetime(d.year, d.month, d.day, tzinfo=VN_TZ) - timedelta(hours=12)
        end = datetime(d.year, d.month, d.day, 12, tzinfo=VN_TZ)
    else:
        start = datetime(d.year, d.month, d.day, tzinfo=VN_TZ)
        end = datetime(d.year, d.month, d.day, 23, 59, 59, tzinfo=VN_TZ)
    return start, min(to_vn(now), end)


def select(items: list[dict], start: datetime, end: datetime) -> list[dict]:
    """Bỏ bài không có ngày, trước start hoặc sau end + 10 phút; bỏ trùng link; mới nhất trước."""
    seen, out = set(), []
    for it in items:
        p = it.get("published")
        if p is None or p < start or p > end + FUTURE_SLACK:
            continue
        if not URL_RE.match(it.get("link") or ""):
            continue
        k = canon_url(it["link"])
        if k in seen:
            continue
        seen.add(k)
        out.append(it)
    out.sort(key=lambda x: x["published"], reverse=True)
    return out


# ---------------------------------------------------------------- phân loại
def compile_terms(terms):
    terms = sorted({t.lower() for t in terms if t}, key=len, reverse=True)
    return [(t, re.compile(r"(?<!\w)" + re.escape(t) + r"(?!\w)")) for t in terms]


def count_terms(text: str, compiled) -> int:
    """Đếm lần khớp, cụm dài trước; khớp xong xóa đoạn đó để không đếm hai lần."""
    text, n = (text or "").lower(), 0
    for _, rx in compiled:
        text, k = rx.subn(" ", text)
        n += k
    return n


class Classifier:
    def __init__(self, cfg: dict):
        self.groups = {g: compile_terms(cfg[g]["keywords"]) for g in ("banking", "securities")}
        self.sectors = [(s["name"], compile_terms(s["keywords"])) for s in cfg["sectors"]]
        sen = cfg.get("sentiment") or {}
        self.pos = compile_terms(sen.get("positive") or [])
        self.neg = compile_terms(sen.get("negative") or [])
        # Điểm cộng cho bài từ feed chuyên mục (hint). Nhỏ hơn ngưỡng 3: bài vẫn phải có ít nhất 1 từ khóa.
        self.hint_bonus = int(cfg.get("hint_bonus", HINT_BONUS))

    def _score(self, it: dict, terms, hint_match: bool) -> int:
        return 3 * count_terms(it["title"], terms) + count_terms(it["desc"], terms) + \
            (self.hint_bonus if hint_match else 0)

    def group_score(self, it: dict, group: str) -> int:
        return self._score(it, self.groups[group], it.get("hint") == group)

    def sector_of(self, it: dict) -> tuple[str | None, int]:
        best, best_sc = None, 0
        for name, terms in self.sectors:
            sc = self._score(it, terms, False)
            if sc >= 3 and sc > best_sc:
                best, best_sc = name, sc
        return best, best_sc

    def impact(self, text: str) -> str:
        p, n = count_terms(text, self.pos), count_terms(text, self.neg)
        return "tich-cuc" if p > n else "tieu-cuc" if n > p else "trung-tinh"


def heat_for(n: int) -> int:
    return 1 if n < 3 else 2 if n < 6 else 3 if n < 10 else 4 if n < 15 else 5


# ---------------------------------------------------------------- dựng bản tin
def _fmt(dt: datetime) -> str:
    return dt.strftime("%H:%M %d/%m")


def _article(it: dict) -> dict:
    return {"title": it["title"], "source": it["source"], "url": it["link"], "time": it["published"].strftime("%d/%m %H:%M")}


def _market(group: str, picked: list[tuple[dict, int]], start: datetime, end: datetime, clf: Classifier) -> dict:
    picked = sorted(picked, key=lambda x: x[0]["published"], reverse=True)[:LIMITS[group]]
    arts = [it for it, _ in picked]
    what = GROUP_TEXT[group]
    if arts:
        n_src = len({it["source"] for it in arts})
        summary = (f"Có {len(arts)} bài về {what} từ {n_src} nguồn ({_fmt(start)} – {_fmt(end)}). "
                   "Bản tự động từ RSS, chưa có số liệu và nhận định.")
    else:
        summary = f"Chưa có bài về {what} trong khung giờ này. Bản tự động từ RSS, chưa có số liệu và nhận định."
    top = sorted(picked, key=lambda x: (-x[1], -x[0]["published"].timestamp()))[:5]
    news = [{"title": it["title"], "impact": clf.impact(it["title"] + " " + it["desc"]),
             "source": it["source"], "url": it["link"]} for it, _ in top]
    return {"summary": summary, "metrics": [], "news": news, "articles": [_article(it) for it in arts]}


def build_edition(items: list[dict], cfg: dict, d: date, session: str, now: datetime,
                  failed: list[str] | None = None) -> dict:
    """items: bài đã đọc từ mọi feed (mỗi bài có thêm khóa 'hint' của feed). Trả bản tin dạng dict."""
    clf = Classifier(cfg)
    start, end = window_for(d, session, now)
    sel = select(items, start, end)
    picked = {"banking": [], "securities": []}
    by_sector: dict[str, list[dict]] = {s["name"]: [] for s in cfg["sectors"]}
    for it in sel:
        for g in picked:
            sc = clf.group_score(it, g)
            if sc >= 3:
                picked[g].append((it, sc))
        name, _ = clf.sector_of(it)
        if name:
            by_sector[name].append(it)

    order = [s["name"] for s in cfg["sectors"]]
    ranked = sorted(order, key=lambda nm: (-len(by_sector[nm]), order.index(nm)))[:5]
    sectors = []
    for nm in ranked:
        arts = sorted(by_sector[nm], key=lambda x: x["published"], reverse=True)[:LIMITS["sector"]]
        if arts:
            sectors.append({"name": nm, "status": "soi-noi", "heat": heat_for(len(arts)),
                            "reason": f"{len(arts)} bài trong khung giờ, từ {len({a['source'] for a in arts})} nguồn.",
                            "source": arts[0]["source"], "url": arts[0]["link"],
                            "articles": [_article(a) for a in arts]})
        else:
            sectors.append({"name": nm, "status": "soi-noi", "heat": 1,
                            "reason": "Chưa có bài trong khung giờ này.", "articles": []})

    used = {id(it) for g in picked.values() for it, _ in g} | {id(a) for v in by_sector.values() for a in v}
    src_count = Counter(it["source"] for it in sel if id(it) in used)
    return {
        "key": daily_key(d, session), "label": label_for(d, session), "date": d.isoformat(), "session": session,
        "updatedAt": to_vn(now).isoformat(timespec="seconds"),
        "mode": "rss",
        "banking": _market("banking", picked["banking"], start, end, clf),
        "securities": _market("securities", picked["securities"], start, end, clf),
        "sectorsSummary": "Tự động chọn 5 ngành có nhiều bài nhất trong nguồn RSS; chưa đánh giá biến động giá.",
        "sectors": sectors,
        "rss": {"window": [start.isoformat(timespec="seconds"), end.isoformat(timespec="seconds")],
                "sources": dict(sorted(src_count.items(), key=lambda kv: (-kv[1], kv[0]))),
                "failed": list(failed or [])},
    }


def article_total(ed: dict) -> int:
    return len(ed["banking"]["articles"]) + len(ed["securities"]["articles"]) + \
        sum(len(s["articles"]) for s in ed["sectors"])


# ---------------------------------------------------------------- dòng lệnh
def load_config(path: str | None) -> dict:
    p = Path(path or os.environ.get("NTT_RSS_CONFIG") or DEFAULT_CONFIG)
    return read_json(p)


def collect_items(cfg: dict):
    res, failed = fetch_all(cfg["sources"], cfg.get("timeout", 20), cfg.get("max_items_per_source", 60))
    items = []
    for src, its, _ in res:
        for it in its or []:
            it["hint"] = src.get("hint")
            items.append(it)
    return res, items, failed


def check_feeds(cfg: dict) -> int:
    res, _ = fetch_all(cfg["sources"], cfg.get("timeout", 20), 10 ** 6)
    for src, items, st in res:
        if items is None:
            print(f"{src['name']} {src['url']} | {st} | - | - | -")
            continue
        dated = [it["published"] for it in items if it["published"]]
        newest = max(dated).strftime("%H:%M %d/%m/%Y") if dated else "-"
        print(f"{src['name']} {src['url']} | {st} | {len(items)} item | {len(dated)} có ngày | mới nhất {newest}")
    return 0


def main(argv: list[str] | None = None) -> int:
    setup_stdio()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--date", help="YYYY-MM-DD (mặc định: hôm nay giờ VN)")
    ap.add_argument("--session", choices=["sang", "chieu"])
    ap.add_argument("--out", help="thư mục gốc dữ liệu (mặc định data/ của repo); ghi vào DIR/daily/<key>.json")
    ap.add_argument("--config", help="file cấu hình (mặc định $NTT_RSS_CONFIG hoặc scripts/rss_config.json)")
    ap.add_argument("--check-feeds", action="store_true", help="kiểm từng feed rồi thoát, không ghi file")
    args = ap.parse_args(argv)

    cfg = load_config(args.config)
    if args.check_feeds:
        return check_feeds(cfg)

    now = now_vn()
    d = date.fromisoformat(args.date) if args.date else now.date()
    session = args.session or session_for(now)
    if session == "chieu" and d.weekday() >= 5:
        print("Chiều cuối tuần không có bản tin. Kết thúc.")
        return 0

    root = Path(args.out) if args.out else DATA
    key = daily_key(d, session)
    out = root / "daily" / f"{key}.json"
    if out.exists():
        try:
            old = read_json(out)
            is_rss = isinstance(old, dict) and old.get("mode") == "rss"
        except Exception:
            is_rss = False
        if not is_rss:
            print(f"Đã có bản tin AI (hoặc file không đọc được) tại {out}, bỏ qua.")
            return 0

    _, items, failed = collect_items(cfg)
    ed = build_edition(items, cfg, d, session, now, failed)
    if article_total(ed) == 0:
        print(f"Không thu được bài nào trong khung giờ {ed['rss']['window'][0]} – {ed['rss']['window'][1]}; "
              f"không ghi file. Feed lỗi: {', '.join(failed) or 'không'}")
        return 3
    write_json(out, ed)
    if not args.out:
        build_index()
    counts = f"ngân hàng {len(ed['banking']['articles'])}, chứng khoán {len(ed['securities']['articles'])}, " + \
        ", ".join(f"{s['name']} {len(s['articles'])}" for s in ed["sectors"])
    shown = out.relative_to(ROOT) if out.is_relative_to(ROOT) else out
    print(f"Đã ghi {shown}: {counts}; feed lỗi: {', '.join(failed) or 'không'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
