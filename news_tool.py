"""Công cụ tổng hợp & phân tích tin tức kinh tế - tài chính theo định kỳ.

Chạy:  python news_tool.py [--hours 24] [--no-ai] [--no-fetch] [--open]
"""
import argparse
import concurrent.futures
import email.utils
import html
import json
import os
import re
import sqlite3
import sys
import urllib.request
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

BASE = Path(__file__).resolve().parent
DATA_DIR = BASE / "data"
REPORT_DIR = BASE / "reports"
DB_PATH = DATA_DIR / "news.db"
VN_TZ = timezone(timedelta(hours=7))
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) NewsDigest/1.0"

STOPWORDS = set("""
và của có là các những cho trong với được người này đã sẽ một khi từ theo tại về đến năm ngày tháng
nhiều hơn vào ra đang bị lên sau trước như thế nào gì vì để mới còn cũng rất nhưng hay hoặc nếu thì mà
đó đây ai sao bao tới qua phải nên vẫn chỉ cùng đều lại hết nay hôm tuần quý triệu tỷ tỉ nghìn đồng usd
the a an of to in on for and or with at by from as is are was were be been it its this that will has have
after over into new says said up out about more than amid how why what us u.s. could would can
""".split())

WORD_RE = re.compile(r"[^\W\d_]+(?:[-'][^\W\d_]+)*", re.UNICODE)


# ---------------------------------------------------------------- tiện ích

def load_config():
    with open(BASE / "config.json", encoding="utf-8") as f:
        return json.load(f)


def clean_text(s):
    if not s:
        return ""
    s = html.unescape(s)
    s = re.sub(r"<[^>]+>", " ", s)
    s = html.unescape(s)
    return re.sub(r"\s+", " ", s).strip()


def parse_date(s):
    if not s:
        return None
    s = s.strip()
    try:
        d = email.utils.parsedate_to_datetime(s)
    except (TypeError, ValueError, IndexError):
        try:
            d = datetime.fromisoformat(s.replace("Z", "+00:00"))
        except ValueError:
            return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=VN_TZ)
    return d.astimezone(timezone.utc)


def fmt_vn(dt, pattern="%H:%M %d/%m"):
    return dt.astimezone(VN_TZ).strftime(pattern)


def esc(s):
    return html.escape(str(s), quote=True)


# ---------------------------------------------------------------- thu thập

def _local(tag):
    return tag.rsplit("}", 1)[-1].lower()


def parse_feed(raw):
    """Trả về list dict(title, link, summary, published) từ RSS hoặc Atom."""
    items = []
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        return parse_feed_regex(raw.decode("utf-8", "replace"))
    for node in root.iter():
        if _local(node.tag) not in ("item", "entry"):
            continue
        rec = {"title": "", "link": "", "summary": "", "published": ""}
        for child in node:
            name = _local(child.tag)
            text = (child.text or "").strip()
            if name == "title":
                rec["title"] = text
            elif name == "link":
                rec["link"] = rec["link"] or text or child.get("href", "")
            elif name in ("description", "summary") and not rec["summary"]:
                rec["summary"] = text
            elif name in ("pubdate", "published", "updated", "date") and not rec["published"]:
                rec["published"] = text
            elif name == "guid" and not rec["link"] and text.startswith("http"):
                rec["link"] = text
        items.append(rec)
    return items


def parse_feed_regex(text):
    """Dự phòng cho feed XML lỗi cú pháp."""
    def grab(block, tag):
        m = re.search(rf"<{tag}[^>]*>(.*?)</{tag}>", block, re.S | re.I)
        if not m:
            return ""
        v = m.group(1).strip()
        return re.sub(r"^<!\[CDATA\[(.*)\]\]>$", r"\1", v, flags=re.S)
    return [{"title": grab(b, "title"), "link": grab(b, "link"),
             "summary": grab(b, "description"), "published": grab(b, "pubDate")}
            for b in re.findall(r"<item[\s>].*?</item>", text, re.S | re.I)]


def fetch_source(src, cfg):
    req = urllib.request.Request(src["url"], headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=cfg.get("request_timeout", 20)) as r:
        raw = r.read()
    now = datetime.now(timezone.utc)
    out = []
    for it in parse_feed(raw)[: cfg.get("max_items_per_source", 60)]:
        title = clean_text(it["title"])
        link = it["link"].strip()
        if not title or not link:
            continue
        pub = parse_date(it["published"]) or now
        out.append({
            "url": link, "source": src["name"], "lang": src.get("lang", "vi"),
            "title": title, "summary": clean_text(it["summary"])[:800],
            "published": min(pub, now).isoformat(),
        })
    return out


def open_db():
    DATA_DIR.mkdir(exist_ok=True)
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    db.executescript("""
        CREATE TABLE IF NOT EXISTS articles (
            url TEXT PRIMARY KEY, source TEXT, lang TEXT, title TEXT,
            summary TEXT, published TEXT, fetched TEXT);
        CREATE INDEX IF NOT EXISTS idx_pub ON articles(published);
        CREATE TABLE IF NOT EXISTS runs (
            ts TEXT PRIMARY KEY, hours INTEGER, total INTEGER,
            sentiment REAL, topics TEXT);
    """)
    return db


def collect(cfg, db):
    fetched_at = datetime.now(timezone.utc).isoformat()
    stats, new_count = {}, 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        futures = {pool.submit(fetch_source, s, cfg): s for s in cfg["sources"]}
        for fut in concurrent.futures.as_completed(futures):
            src = futures[fut]
            try:
                items = fut.result()
            except Exception as e:  # mạng lỗi / feed hỏng: bỏ qua nguồn đó
                stats[src["name"]] = f"LỖI: {type(e).__name__}"
                print(f"  ! {src['name']}: {e}")
                continue
            added = 0
            for a in items:
                cur = db.execute(
                    "INSERT OR IGNORE INTO articles VALUES (?,?,?,?,?,?,?)",
                    (a["url"], a["source"], a["lang"], a["title"], a["summary"],
                     a["published"], fetched_at))
                added += cur.rowcount
            new_count += added
            stats[src["name"]] = f"{len(items)} tin ({added} mới)"
            print(f"  - {src['name']}: {len(items)} tin, {added} mới")
    db.commit()
    return stats, new_count


def load_window(db, start, end):
    rows = db.execute(
        "SELECT * FROM articles WHERE published >= ? AND published < ? ORDER BY published DESC",
        (start.isoformat(), end.isoformat())).fetchall()
    arts, seen_titles = [], set()
    for r in rows:
        key = r["title"].lower()
        if key in seen_titles:  # cùng một tin xuất hiện ở hai chuyên mục
            continue
        seen_titles.add(key)
        a = dict(r)
        a["dt"] = datetime.fromisoformat(a["published"])
        arts.append(a)
    return arts


# ---------------------------------------------------------------- phân tích

def compile_terms(terms):
    terms = sorted(set(t.lower() for t in terms), key=len, reverse=True)
    return [(t, re.compile(r"(?<!\w)" + re.escape(t) + r"(?!\w)")) for t in terms]


def count_terms(text, compiled):
    """Đếm cụm từ, ưu tiên cụm dài để 'báo lỗ' không bị đếm thêm 'lỗ'."""
    hits = Counter()
    for term, rx in compiled:
        text, n = rx.subn(" ", text)
        if n:
            hits[term] += n
    return hits


def title_bigrams(title):
    """Cụm 2 âm tiết liền nhau: tiếng Việt ghép từ theo âm tiết nên so từng âm tiết dễ trùng nhầm."""
    words = [w.lower() for w in WORD_RE.findall(title)]
    return {f"{w1} {w2}" for w1, w2 in zip(words, words[1:])
            if w1 not in STOPWORDS and w2 not in STOPWORDS and len(w1) > 1 and len(w2) > 1}


def cluster_stories(arts, threshold=0.3):
    """Gom các bài cùng sự kiện (từ nhiều báo) theo độ trùng cụm từ của tiêu đề."""
    clusters = []
    for a in sorted(arts, key=lambda x: x["dt"]):
        toks = a["_tokens"]
        best, best_sim = None, 0.0
        if len(toks) >= 2:
            for c in clusters:
                for other in c["items"]:
                    inter = len(toks & other["_tokens"])
                    if inter < 2:
                        continue
                    sim = inter / min(len(toks), len(other["_tokens"]))
                    if sim > best_sim:
                        best, best_sim = c, sim
        if best and best_sim >= threshold:
            best["items"].append(a)
        else:
            clusters.append({"items": [a]})
    multi = [c for c in clusters if len({i["source"].split()[0] for i in c["items"]}) >= 2]
    multi.sort(key=lambda c: (len({i["source"] for i in c["items"]}), max(i["dt"] for i in c["items"])),
               reverse=True)
    return multi


def bigram_df(arts):
    """Số bài chứa mỗi cụm 2 âm tiết; bỏ 'mảnh đầu' của cụm dài (vd. 'giải phóng' trong 'giải phóng mặt bằng')."""
    df, next_word = Counter(), defaultdict(Counter)
    for a in arts:
        words = [w.lower() for w in WORD_RE.findall(a["title"])]
        grams, follows = set(), set()
        for i, (w1, w2) in enumerate(zip(words, words[1:])):
            if w1 in STOPWORDS or w2 in STOPWORDS or len(w1) < 2 or len(w2) < 2:
                continue
            g = f"{w1} {w2}"
            grams.add(g)
            if i + 2 < len(words):
                follows.add((g, words[i + 2]))
        df.update(grams)
        for g, y in follows:
            next_word[g][y] += 1
    original = dict(df)
    for g, n in original.items():
        if not next_word[g]:
            continue
        y, cnt = next_word[g].most_common(1)[0]
        w2 = g.split()[1]
        if cnt == n and y not in STOPWORDS and len(y) > 1 and original.get(f"{w2} {y}", 0) >= n:
            df[g] = 0
    return +df


def analyze(arts, prev_arts, cfg):
    topic_rx = {t: compile_terms(ws) for t, ws in cfg["topics"].items()}
    pos_rx = compile_terms(cfg["sentiment"]["positive"])
    neg_rx = compile_terms(cfg["sentiment"]["negative"])
    watch = set(cfg.get("watch_tickers", []))
    ticker_ctx = re.compile(r"\((?:HOSE|HoSE|HNX|UPCoM|UPCOM)\s*:\s*([A-Z]{3})\)")
    ticker_any = re.compile(r"(?<![A-Za-z0-9])([A-Z]{3})(?![A-Za-z0-9])")
    city_rx = re.compile(r"TP\.?\s*HCM", re.I)  # "TP.HCM" không phải mã HCM
    kw_stop = {k.lower() for k in cfg.get("keyword_stoplist", [])}

    def classify(a):
        text = f"{a['title']} {a['summary']}".lower()
        scores = {t: sum(count_terms(text, rx).values()) for t, rx in topic_rx.items()}
        # tiêu đề quan trọng hơn: cộng thêm điểm nếu khớp trong tiêu đề
        title = a["title"].lower()
        for t, rx in topic_rx.items():
            scores[t] += 2 * sum(count_terms(title, rx).values())
        topics = [t for t, s in sorted(scores.items(), key=lambda x: -x[1]) if s > 0]
        a["topics"] = topics or ["Khác"]
        a["topic"] = a["topics"][0]
        p = sum(count_terms(text, pos_rx).values())
        n = sum(count_terms(text, neg_rx).values())
        a["pos_terms"], a["neg_terms"] = p, n
        a["sent"] = 1 if p > n else (-1 if n > p else 0)
        a["_tokens"] = title_bigrams(a["title"])
        raw = city_rx.sub(" ", a["title"] + " " + a["summary"])
        tickers = set(ticker_ctx.findall(raw))
        if a["lang"] == "vi":
            tickers |= {t for t in ticker_any.findall(raw) if t in watch}
        a["tickers"] = sorted(tickers)

    for a in arts:
        classify(a)
    for a in prev_arts:
        classify(a)

    total = len(arts)
    sent_counts = Counter(a["sent"] for a in arts)
    sentiment_index = round(100 * (sent_counts[1] - sent_counts[-1]) / total, 1) if total else 0.0

    def topic_table(items):
        tab = defaultdict(lambda: {"n": 0, "pos": 0, "neg": 0})
        for a in items:
            for t in a["topics"][:2]:  # tối đa 2 chủ đề / bài
                tab[t]["n"] += 1
                tab[t]["pos"] += a["sent"] == 1
                tab[t]["neg"] += a["sent"] == -1
        return tab

    topics_now, topics_prev = topic_table(arts), topic_table(prev_arts)
    topics = []
    for t, v in sorted(topics_now.items(), key=lambda x: -x[1]["n"]):
        prev_n = topics_prev.get(t, {}).get("n", 0)
        idx = round(100 * (v["pos"] - v["neg"]) / v["n"], 0) if v["n"] else 0
        topics.append({"name": t, **v, "prev": prev_n, "delta": v["n"] - prev_n, "sent_index": idx})

    df_now, df_prev = bigram_df(arts), bigram_df(prev_arts)
    keywords = []
    for g, n in df_now.most_common(120):
        if n < 3:
            break
        if g in kw_stop:
            continue
        p = df_prev.get(g, 0)
        keywords.append({"term": g, "n": n, "prev": p, "rising": n >= 2 * max(p, 1)})
    keywords = keywords[:25]

    tick = defaultdict(lambda: {"n": 0, "pos": 0, "neg": 0, "titles": []})
    for a in arts:
        for t in a["tickers"]:
            tick[t]["n"] += 1
            tick[t]["pos"] += a["sent"] == 1
            tick[t]["neg"] += a["sent"] == -1
            if len(tick[t]["titles"]) < 3:
                tick[t]["titles"].append(a)
    tickers = sorted(({"code": k, **v} for k, v in tick.items()), key=lambda x: -x["n"])[:20]

    by_source = Counter(a["source"] for a in arts)
    by_topic_articles = defaultdict(list)
    for a in arts:
        by_topic_articles[a["topic"]].append(a)

    return {
        "total": total,
        "prev_total": len(prev_arts),
        "sent_counts": {"pos": sent_counts[1], "neu": sent_counts[0], "neg": sent_counts[-1]},
        "sentiment_index": sentiment_index,
        "topics": topics,
        "keywords": keywords,
        "tickers": tickers,
        "stories": cluster_stories(arts)[:12],
        "by_source": by_source,
        "by_topic_articles": by_topic_articles,
    }


# ---------------------------------------------------------------- AI (Claude)

AI_SYSTEM = (
    "Bạn là chuyên gia phân tích kinh tế - tài chính, viết bản tin cho nhà đầu tư cá nhân tại Việt Nam. "
    "Chỉ dựa vào các tin được cung cấp; không bịa số liệu. Khi nêu một ý, dẫn số thứ tự tin trong ngoặc vuông, "
    "ví dụ [3] hoặc [3][17]. Viết tiếng Việt, súc tích, khách quan, không đưa khuyến nghị mua/bán cụ thể."
)

AI_INSTRUCTIONS = """Dưới đây là {n} tin kinh tế - tài chính trong {hours} giờ qua (đến {now}), kèm số liệu thống kê tự động.

THỐNG KÊ:
{stats}

DANH SÁCH TIN:
{items}

Hãy viết bản phân tích bằng Markdown với đúng các mục sau (dùng tiêu đề ##):
## Tổng quan
(3-4 câu: bức tranh chung, tâm lý thị trường)
## Điểm tin quan trọng
(5-8 gạch đầu dòng, mỗi dòng **in đậm** sự kiện chính rồi giải thích ngắn ý nghĩa)
## Tác động theo thị trường
(gạch đầu dòng cho: Chứng khoán; Lãi suất & tỷ giá; Vàng; Hàng hóa & năng lượng; Bất động sản — bỏ mục nào không có tin)
## Rủi ro và cơ hội
## Cần theo dõi tiếp
(3-5 sự kiện/chỉ số nên theo dõi trong kỳ tới)"""


def ai_analysis(arts, result, cfg, hours):
    ai_cfg = cfg.get("ai", {})
    if not ai_cfg.get("enabled", True):
        return None, "AI đang tắt trong config.json"
    try:
        import anthropic
    except ImportError:
        return None, "chưa cài thư viện anthropic (pip install anthropic)"

    # ưu tiên tin được nhiều báo đưa, sau đó tin mới nhất
    hot = []
    for c in result["stories"]:
        hot.extend(c["items"][:1])
    picked, seen = [], set()
    for a in hot + arts:
        if a["url"] not in seen:
            picked.append(a)
            seen.add(a["url"])
        if len(picked) >= ai_cfg.get("max_articles", 120):
            break

    lines = [f"[{i}] ({a['source']}, {fmt_vn(a['dt'])}) {a['title']} — {a['summary'][:300]}"
             for i, a in enumerate(picked, 1)]
    stats = [f"- Tổng số tin: {result['total']} (kỳ trước: {result['prev_total']})",
             f"- Chỉ số tâm lý (từ khóa): {result['sentiment_index']:+} "
             f"(tích cực {result['sent_counts']['pos']}, tiêu cực {result['sent_counts']['neg']})",
             "- Chủ đề: " + ", ".join(f"{t['name']} {t['n']} ({t['delta']:+})" for t in result["topics"]),
             "- Từ khóa nổi bật: " + ", ".join(k["term"] for k in result["keywords"][:15]),
             "- Mã cổ phiếu được nhắc: " + ", ".join(f"{t['code']}({t['n']})" for t in result["tickers"][:12])]
    prompt = AI_INSTRUCTIONS.format(n=len(picked), hours=hours,
                                    now=fmt_vn(datetime.now(timezone.utc), "%H:%M %d/%m/%Y"),
                                    stats="\n".join(stats), items="\n".join(lines))

    client = anthropic.Anthropic()
    try:
        with client.beta.messages.stream(
            model=ai_cfg.get("model", "claude-opus-5-5"),
            max_tokens=16000,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            output_config={"effort": ai_cfg.get("effort", "medium")},
            system=AI_SYSTEM,
            messages=[{"role": "user", "content": prompt}],
        ) as stream:
            msg = stream.get_final_message()
    except anthropic.AuthenticationError:
        return None, "chưa có/không hợp lệ ANTHROPIC_API_KEY"
    except anthropic.RateLimitError:
        return None, "API đang giới hạn tần suất, thử lại sau"
    except anthropic.APIStatusError as e:
        return None, f"lỗi API {e.status_code}: {e.message}"
    except anthropic.APIConnectionError:
        return None, "không kết nối được tới API"
    except TypeError:  # SDK không tìm thấy thông tin xác thực
        return None, "chưa đặt biến môi trường ANTHROPIC_API_KEY"

    if msg.stop_reason == "refusal":
        return None, "mô hình từ chối yêu cầu"
    text = "".join(b.text for b in msg.content if b.type == "text").strip()
    return {"markdown": text, "refs": picked}, None


def md_to_html(md, refs):
    """Chuyển Markdown đơn giản (##, -, **) sang HTML; [n] thành liên kết tới bài gốc."""
    def inline(s):
        s = esc(s)
        s = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", s)

        def ref(m):
            i = int(m.group(1))
            if 1 <= i <= len(refs):
                a = refs[i - 1]
                return f'<a class="ref" href="{esc(a["url"])}" title="{esc(a["title"])}" target="_blank">{i}</a>'
            return m.group(0)
        return re.sub(r"\[(\d{1,3})\]", ref, s)

    out, in_list = [], False
    for line in md.splitlines():
        line = line.rstrip()
        if re.match(r"^\s*[-*]\s+", line):
            if not in_list:
                out.append("<ul>")
                in_list = True
            out.append(f"<li>{inline(re.sub(r'^\s*[-*]\s+', '', line))}</li>")
            continue
        if in_list:
            out.append("</ul>")
            in_list = False
        if line.startswith("#"):
            level = min(len(line) - len(line.lstrip("#")) + 1, 4)
            out.append(f"<h{level}>{inline(line.lstrip('#').strip())}</h{level}>")
        elif line.strip():
            out.append(f"<p>{inline(line)}</p>")
    if in_list:
        out.append("</ul>")
    return "\n".join(out)


# ---------------------------------------------------------------- báo cáo

CSS = """
:root{--bg:#f6f7f9;--card:#fff;--ink:#1b1f24;--muted:#5d6670;--line:#e3e6ea;--accent:#1f5fbf;
--pos:#1a7f4b;--neg:#c0392b;--neu:#8a939c;--chip:#eef2f7}
@media (prefers-color-scheme:dark){:root{--bg:#111418;--card:#1a1e23;--ink:#e6e9ec;--muted:#9aa4ae;
--line:#2b3138;--accent:#6ea8ff;--pos:#4cc38a;--neg:#ff7b6b;--neu:#7b858f;--chip:#242a31}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);
font:15px/1.55 "Segoe UI",system-ui,-apple-system,Roboto,sans-serif}
.wrap{max-width:1080px;margin:0 auto;padding:24px 16px 60px}
header h1{margin:0 0 4px;font-size:26px}header p{margin:0;color:var(--muted)}
.grid{display:grid;gap:14px}.kpis{grid-template-columns:repeat(auto-fit,minmax(170px,1fr));margin:20px 0}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:16px 18px}
.kpi .v{font-size:28px;font-weight:650}.kpi .l{color:var(--muted);font-size:13px}
.kpi .s{font-size:13px;color:var(--muted)}
h2{font-size:19px;margin:28px 0 12px}.card h2:first-child,.card h3:first-child{margin-top:0}
.ai h3{font-size:16px;margin:18px 0 6px}.ai ul{padding-left:20px;margin:6px 0}
.ai li{margin:4px 0}a{color:var(--accent);text-decoration:none}a:hover{text-decoration:underline}
a.ref{font-size:11px;vertical-align:super;padding:0 3px;margin-left:1px;border-radius:4px;background:var(--chip)}
table{width:100%;border-collapse:collapse}td,th{padding:7px 6px;border-bottom:1px solid var(--line);
text-align:left;vertical-align:middle}th{font-size:12px;color:var(--muted);font-weight:600}
td.num,th.num{text-align:right;font-variant-numeric:tabular-nums}
.bar{display:flex;height:10px;border-radius:5px;overflow:hidden;background:var(--chip);min-width:120px}
.bar span{display:block;height:100%}.b-pos{background:var(--pos)}.b-neg{background:var(--neg)}.b-neu{background:var(--neu)}
.up{color:var(--pos)}.down{color:var(--neg)}.muted{color:var(--muted)}
.chips{display:flex;flex-wrap:wrap;gap:8px}.chip{background:var(--chip);border-radius:999px;padding:4px 11px;font-size:13px}
.chip b{font-weight:650;margin-left:4px}.chip.rise{outline:1.5px solid var(--accent)}
.story{padding:10px 0;border-bottom:1px solid var(--line)}.story:last-child{border:0}
.story .t{font-weight:600}.story .m{font-size:13px;color:var(--muted)}
.list{margin:0;padding:0;list-style:none}.list li{padding:7px 0;border-bottom:1px solid var(--line)}
.list li:last-child{border:0}.list .m{font-size:12px;color:var(--muted)}
.dot{display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:6px;vertical-align:middle}
.cols{grid-template-columns:repeat(auto-fit,minmax(320px,1fr))}
.note{font-size:12px;color:var(--muted);margin-top:8px}
svg.spark{width:100%;height:60px}
footer{margin-top:40px;color:var(--muted);font-size:12px}
"""


def sent_dot(s):
    cls = {1: "b-pos", -1: "b-neg", 0: "b-neu"}[s]
    label = {1: "tích cực", -1: "tiêu cực", 0: "trung tính"}[s]
    return f'<span class="dot {cls}" title="{label}"></span>'


def delta_html(d):
    if d > 0:
        return f'<span class="up">▲ {d}</span>'
    if d < 0:
        return f'<span class="down">▼ {-d}</span>'
    return '<span class="muted">–</span>'


def sparkline(history):
    if len(history) < 2:
        return '<p class="muted">Cần ít nhất 2 lần chạy để vẽ xu hướng.</p>'
    vals = [h["sentiment"] for h in history]
    lo, hi = min(vals + [-10]), max(vals + [10])
    w, h = 600, 60
    pts = []
    for i, v in enumerate(vals):
        x = i * w / (len(vals) - 1)
        y = h - 6 - (v - lo) / (hi - lo) * (h - 12)
        pts.append(f"{x:.1f},{y:.1f}")
    zero_y = h - 6 - (0 - lo) / (hi - lo) * (h - 12)
    first = fmt_vn(datetime.fromisoformat(history[0]["ts"]))
    last = fmt_vn(datetime.fromisoformat(history[-1]["ts"]))
    return (f'<svg class="spark" viewBox="0 0 {w} {h}" preserveAspectRatio="none">'
            f'<line x1="0" x2="{w}" y1="{zero_y:.1f}" y2="{zero_y:.1f}" stroke="var(--line)" stroke-dasharray="4 4"/>'
            f'<polyline fill="none" stroke="var(--accent)" stroke-width="2" points="{" ".join(pts)}"/></svg>'
            f'<div class="note">{len(vals)} lần chạy gần nhất: {first} → {last}. '
            f'Đường đứt nét là mức 0 (cân bằng tích cực/tiêu cực).</div>')


def build_html(result, ai, ai_err, start, end, hours, fetch_stats, history):
    sc = result["sent_counts"]
    total = max(result["total"], 1)
    si = result["sentiment_index"]
    mood = "Tích cực" if si > 10 else ("Tiêu cực" if si < -10 else "Trung tính")
    hot_topic = result["topics"][0]["name"] if result["topics"] else "–"
    p = []
    p.append(f"""<!doctype html><html lang="vi"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Bản tin Kinh tế - Tài chính {fmt_vn(end, '%d/%m/%Y %H:%M')}</title><style>{CSS}</style></head>
<body><div class="wrap"><header><h1>Bản tin Kinh tế – Tài chính</h1>
<p>Từ {fmt_vn(start, '%H:%M %d/%m/%Y')} đến {fmt_vn(end, '%H:%M %d/%m/%Y')} ({hours} giờ) ·
{len(result['by_source'])} nguồn</p></header>
<div class="grid kpis">
<div class="card kpi"><div class="l">Số tin</div><div class="v">{result['total']}</div>
<div class="s">kỳ trước {result['prev_total']} {delta_html(result['total'] - result['prev_total'])}</div></div>
<div class="card kpi"><div class="l">Chỉ số tâm lý</div><div class="v">{si:+.0f}</div><div class="s">{mood} (thang −100…+100)</div></div>
<div class="card kpi"><div class="l">Tích cực / Tiêu cực</div><div class="v"><span class="up">{sc['pos']}</span> / <span class="down">{sc['neg']}</span></div>
<div class="s">{sc['neu']} tin trung tính</div></div>
<div class="card kpi"><div class="l">Chủ đề nóng nhất</div><div class="v" style="font-size:19px">{esc(hot_topic)}</div>
<div class="s">{result['topics'][0]['n'] if result['topics'] else 0} tin</div></div></div>""")

    p.append('<div class="card ai"><h2>Nhận định tổng hợp (AI)</h2>')
    if ai:
        p.append(md_to_html(ai["markdown"], ai["refs"]))
        p.append('<p class="note">Do Claude tổng hợp từ tiêu đề và tóm tắt tin; số nhỏ dẫn tới bài gốc. '
                 'Chỉ mang tính tham khảo, không phải khuyến nghị đầu tư.</p>')
    else:
        p.append(f'<p class="muted">Không có phần nhận định AI: {esc(ai_err)}. '
                 'Các phân tích thống kê bên dưới vẫn đầy đủ.</p>')
    p.append("</div>")

    if result["stories"]:
        p.append('<h2>Tin được nhiều báo cùng đưa</h2><div class="card">')
        for c in result["stories"]:
            lead = max(c["items"], key=lambda a: len(a["summary"]))
            srcs = sorted({a["source"] for a in c["items"]})
            links = " · ".join(f'<a href="{esc(a["url"])}" target="_blank">{esc(a["source"])}</a>' for a in c["items"][:6])
            p.append(f'<div class="story"><div class="t">{sent_dot(lead["sent"])}{esc(lead["title"])}</div>'
                     f'<div class="m">{len(srcs)} nguồn: {links}</div></div>')
        p.append("</div>")

    p.append('<h2>Chủ đề</h2><div class="card"><table><tr><th>Chủ đề</th><th class="num">Số tin</th>'
             '<th class="num">So kỳ trước</th><th>Tâm lý (xanh tích cực · đỏ tiêu cực)</th><th class="num">Chỉ số</th></tr>')
    for t in result["topics"]:
        n = max(t["n"], 1)
        neu = t["n"] - t["pos"] - t["neg"]
        p.append(f'<tr><td>{esc(t["name"])}</td><td class="num">{t["n"]}</td><td class="num">{delta_html(t["delta"])}</td>'
                 f'<td><div class="bar"><span class="b-pos" style="width:{100*t["pos"]/n:.1f}%"></span>'
                 f'<span class="b-neu" style="width:{100*neu/n:.1f}%"></span>'
                 f'<span class="b-neg" style="width:{100*t["neg"]/n:.1f}%"></span></div></td>'
                 f'<td class="num">{t["sent_index"]:+.0f}</td></tr>')
    p.append('</table><p class="note">Một bài có thể thuộc tối đa 2 chủ đề. Tâm lý được ước lượng bằng từ khóa '
             '(ví dụ “bứt phá”, “bán tháo”), chỉ là chỉ báo thô.</p></div>')

    p.append('<div class="grid cols" style="margin-top:14px">')
    p.append('<div class="card"><h3>Từ khóa nổi bật</h3><div class="chips">')
    for k in result["keywords"]:
        cls = " rise" if k["rising"] else ""
        p.append(f'<span class="chip{cls}" title="kỳ trước: {k["prev"]}">{esc(k["term"])}<b>{k["n"]}</b></span>')
    p.append('</div><p class="note">Viền xanh: từ khóa mới nổi (gấp đôi trở lên so với kỳ trước).</p></div>')

    p.append('<div class="card"><h3>Mã cổ phiếu được nhắc đến</h3>')
    if result["tickers"]:
        p.append('<table><tr><th>Mã</th><th class="num">Số tin</th><th class="num">+ / −</th><th>Tin tiêu biểu</th></tr>')
        for t in result["tickers"]:
            a = t["titles"][0]
            p.append(f'<tr><td><b>{t["code"]}</b></td><td class="num">{t["n"]}</td>'
                     f'<td class="num"><span class="up">{t["pos"]}</span>/<span class="down">{t["neg"]}</span></td>'
                     f'<td style="font-size:13px"><a href="{esc(a["url"])}" target="_blank">{esc(a["title"][:90])}</a></td></tr>')
        p.append("</table>")
    else:
        p.append('<p class="muted">Không có mã nào trong danh sách theo dõi.</p>')
    p.append("</div></div>")

    p.append('<h2>Xu hướng tâm lý qua các kỳ</h2><div class="card">' + sparkline(history) + "</div>")

    p.append('<h2>Tin theo chủ đề</h2><div class="grid cols">')
    order = [t["name"] for t in result["topics"] if t["name"] in result["by_topic_articles"]]
    for name in order:
        items = result["by_topic_articles"][name][:8]
        p.append(f'<div class="card"><h3>{esc(name)} <span class="muted" style="font-weight:400">'
                 f'({len(result["by_topic_articles"][name])})</span></h3><ul class="list">')
        for a in items:
            p.append(f'<li>{sent_dot(a["sent"])}<a href="{esc(a["url"])}" target="_blank">{esc(a["title"])}</a>'
                     f'<div class="m">{esc(a["source"])} · {fmt_vn(a["dt"])}</div></li>')
        p.append("</ul></div>")
    p.append("</div>")

    p.append('<h2>Nguồn tin</h2><div class="card"><table><tr><th>Nguồn</th><th class="num">Tin trong kỳ</th><th>Lần thu thập này</th></tr>')
    names = sorted(set(result["by_source"]) | set(fetch_stats), key=lambda s: -result["by_source"].get(s, 0))
    for s in names:
        p.append(f'<tr><td>{esc(s)}</td><td class="num">{result["by_source"].get(s, 0)}</td>'
                 f'<td class="muted">{esc(fetch_stats.get(s, "không thu thập"))}</td></tr>')
    p.append("</table></div>")
    p.append(f'<footer>Tạo lúc {fmt_vn(datetime.now(timezone.utc), "%H:%M:%S %d/%m/%Y")} bởi news_tool.py. '
             'Thông tin chỉ mang tính tham khảo.</footer></div></body></html>')
    return "\n".join(p)


def build_markdown(result, ai, ai_err, start, end, hours):
    L = [f"# Bản tin Kinh tế – Tài chính",
         f"Từ {fmt_vn(start, '%H:%M %d/%m/%Y')} đến {fmt_vn(end, '%H:%M %d/%m/%Y')} ({hours} giờ)", "",
         f"- Số tin: **{result['total']}** (kỳ trước {result['prev_total']})",
         f"- Chỉ số tâm lý: **{result['sentiment_index']:+.0f}** "
         f"(tích cực {result['sent_counts']['pos']}, tiêu cực {result['sent_counts']['neg']})", ""]
    L += [ai["markdown"], ""] if ai else [f"_Không có nhận định AI: {ai_err}_", ""]
    L.append("## Chủ đề")
    L += [f"- {t['name']}: {t['n']} tin ({t['delta']:+} so kỳ trước), tâm lý {t['sent_index']:+.0f}" for t in result["topics"]]
    L += ["", "## Tin được nhiều báo cùng đưa"]
    for c in result["stories"]:
        a = c["items"][0]
        L.append(f"- [{a['title']}]({a['url']}) — {len({i['source'] for i in c['items']})} nguồn")
    L += ["", "## Từ khóa nổi bật", ", ".join(f"{k['term']} ({k['n']})" for k in result["keywords"])]
    if result["tickers"]:
        L += ["", "## Mã cổ phiếu được nhắc", ", ".join(f"{t['code']} ({t['n']})" for t in result["tickers"])]
    return "\n".join(L) + "\n"


def prune_reports(keep):
    files = sorted(REPORT_DIR.glob("ban-tin_*.html"))
    for f in files[:-keep] if keep > 0 else []:
        f.unlink(missing_ok=True)
        f.with_suffix(".md").unlink(missing_ok=True)


# ---------------------------------------------------------------- main

def main():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except AttributeError:
            pass
    cfg = load_config()
    ap = argparse.ArgumentParser(description="Tổng hợp & phân tích tin tức kinh tế - tài chính")
    ap.add_argument("--hours", type=int, default=cfg.get("window_hours", 24), help="khung thời gian phân tích (giờ)")
    ap.add_argument("--no-ai", action="store_true", help="bỏ qua phần nhận định bằng Claude")
    ap.add_argument("--no-fetch", action="store_true", help="không tải tin mới, chỉ phân tích dữ liệu đã có")
    ap.add_argument("--open", action="store_true", help="mở báo cáo sau khi tạo")
    args = ap.parse_args()

    db = open_db()
    fetch_stats = {}
    if not args.no_fetch:
        print("Đang thu thập tin...")
        fetch_stats, new = collect(cfg, db)
        print(f"=> {new} tin mới được lưu.")

    end = datetime.now(timezone.utc)
    start = end - timedelta(hours=args.hours)
    arts = load_window(db, start, end)
    prev = load_window(db, start - timedelta(hours=args.hours), start)
    if not arts:
        print("Không có tin nào trong khung thời gian này.")
        return 1
    print(f"Phân tích {len(arts)} tin (kỳ trước: {len(prev)})...")
    result = analyze(arts, prev, cfg)

    ai, ai_err = None, "đã tắt bằng --no-ai"
    if not args.no_ai:
        print("Đang tạo nhận định bằng Claude...")
        ai, ai_err = ai_analysis(arts, result, cfg, args.hours)
        if ai_err:
            print(f"  ! Bỏ qua AI: {ai_err}")

    db.execute("INSERT OR REPLACE INTO runs VALUES (?,?,?,?,?)",
               (end.isoformat(), args.hours, result["total"], result["sentiment_index"],
                json.dumps({t["name"]: t["n"] for t in result["topics"]}, ensure_ascii=False)))
    db.commit()
    history = [dict(r) for r in db.execute(
        "SELECT ts, sentiment FROM (SELECT * FROM runs ORDER BY ts DESC LIMIT 30) ORDER BY ts")]

    REPORT_DIR.mkdir(exist_ok=True)
    stamp = end.astimezone(VN_TZ).strftime("%Y-%m-%d_%H%M")
    page = build_html(result, ai, ai_err, start, end, args.hours, fetch_stats, history)
    html_path = REPORT_DIR / f"ban-tin_{stamp}.html"
    html_path.write_text(page, encoding="utf-8")
    (REPORT_DIR / "moi-nhat.html").write_text(page, encoding="utf-8")
    html_path.with_suffix(".md").write_text(build_markdown(result, ai, ai_err, start, end, args.hours), encoding="utf-8")
    prune_reports(cfg.get("keep_reports", 60))
    print(f"Đã tạo báo cáo: {html_path}")
    if args.open and os.name == "nt":
        os.startfile(html_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
