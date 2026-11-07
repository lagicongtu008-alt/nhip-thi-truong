"""Test cho scripts/rss_collect.py (tầng tự động RSS). Không gọi mạng: dùng fixture và `now` cố định.

Chạy:  python -m unittest discover -s tests -v
Fixture thật (trích 4 item từ feed tải ngày 07/11/2026): vnexpress, cafef, vietstock, vneconomy,
tuoitre, thanhnien, dantri, vietnamnet. Ca biên tự viết: atom.xml, edge.xml, broken.xml.
"""
from __future__ import annotations

import copy
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta
from email.utils import format_datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import rss_collect as R  # noqa: E402
from common import VN_TZ, read_json, weekly_key  # noqa: E402
from validate import check_daily  # noqa: E402

FIX = ROOT / "tests" / "fixtures" / "rss"
REAL = ["vnexpress", "cafef", "vietstock", "vneconomy", "tuoitre", "thanhnien", "dantri", "vietnamnet"]
CFG = read_json(ROOT / "scripts" / "rss_config.json")


def vn(*a):
    return datetime(*a, tzinfo=VN_TZ)


def item(title, link="https://example.vn/a", desc="", published=None, source="Báo", hint=None):
    return {"title": title, "link": link, "desc": desc, "published": published, "source": source, "hint": hint}


def run_script(*args, env_extra=None):
    import os
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    env.pop("NTT_RSS_CONFIG", None)
    env.update(env_extra or {})
    return subprocess.run([sys.executable, str(ROOT / "scripts" / "rss_collect.py"), *args],
                          cwd=ROOT, capture_output=True, env=env)


class ParseRealFixtures(unittest.TestCase):
    def test_every_real_source_parses_with_dates(self):
        for name in REAL:
            with self.subTest(feed=name):
                items = R.parse_feed((FIX / f"{name}.xml").read_bytes(), name)
                self.assertEqual(len(items), 4)
                for it in items:
                    self.assertTrue(it["title"])
                    self.assertRegex(it["link"], r"^https?://\S+$")
                    self.assertIsNotNone(it["published"], it["title"])
                    self.assertEqual(it["published"].utcoffset(), timedelta(hours=7))
                    for f in ("title", "desc"):
                        self.assertNotRegex(it[f], r"[  ﻿]|\s{2,}|&[a-z]+;", f)
                    self.assertNotIn("<img", it["desc"])

    def test_thanhnien_cdata_entities_in_title(self):
        # Feed thật: <title><![CDATA[Ph&oacute; thủ tướng ... &aacute;n ...]]>
        it = R.parse_feed((FIX / "thanhnien.xml").read_bytes(), "Thanh Niên")[0]
        self.assertTrue(it["title"].startswith("Phó thủ tướng Phạm Gia Túc dự lễ khởi công dự án"), it["title"])

    def test_tuoitre_mdy_ampm_with_u202f(self):
        raw = (FIX / "tuoitre.xml").read_text(encoding="utf-8")
        self.assertIn(" AM", raw)
        it = R.parse_feed(raw.encode("utf-8"), "Tuổi Trẻ")[0]
        self.assertEqual(it["published"], vn(2026, 11, 7, 8, 6))

    def test_cafef_two_digit_year_and_vneconomy_gmt(self):
        self.assertEqual(R.parse_feed((FIX / "cafef.xml").read_bytes(), "CafeF")[0]["published"], vn(2026, 11, 7, 8, 59))
        # "Sat, 07 Nov 2026 02:23:49 GMT" → 09:23:49 giờ VN
        self.assertEqual(R.parse_feed((FIX / "vneconomy.xml").read_bytes(), "VnEconomy")[0]["published"],
                         vn(2026, 11, 7, 9, 23, 49))

    def test_vietstock_html_escaped_description(self):
        it = R.parse_feed((FIX / "vietstock.xml").read_bytes(), "Vietstock")[0]
        self.assertTrue(it["desc"].startswith("Chiến thắng áp đảo"), it["desc"][:80])

    def test_dantri_bom(self):
        self.assertTrue((FIX / "dantri.xml").read_bytes().startswith(b"\xef\xbb\xbf"))
        self.assertEqual(len(R.parse_feed((FIX / "dantri.xml").read_bytes(), "Dân trí")), 4)


class ParseEdgeCases(unittest.TestCase):
    def test_atom(self):
        items = R.parse_feed((FIX / "atom.xml").read_bytes(), "Atom")
        self.assertEqual([i["link"] for i in items],
                         ["https://example.vn/atom/lai-suat-lien-ngan-hang", "https://example.vn/atom/co-phieu-ngan-hang"])
        self.assertEqual(items[0]["published"], vn(2026, 11, 7, 6, 30))  # 23:30Z
        self.assertEqual(items[0]["desc"], "Ngân hàng Nhà nước bơm tiền qua OMO.")
        self.assertEqual(items[1]["published"], vn(2026, 11, 7, 8, 15))

    def test_rss_edge_fields(self):
        items = R.parse_feed((FIX / "edge.xml").read_bytes(), "Mẫu")
        by = {i["title"]: i for i in items}
        self.assertIn("Giá vàng SJC tăng mạnh", by)                     # &aacute; &agrave; trong CDATA
        self.assertIn("Lãi suất huy động & tín dụng", by)               # &amp; trong title
        self.assertEqual(by["Giá vàng SJC tăng mạnh"]["desc"], "Giá vàng miếng")  # &nbsp; → dấu cách
        self.assertEqual(by["Giá vàng SJC tăng mạnh"]["published"], vn(2026, 11, 7, 7, 1))  # U+202F trước AM
        self.assertEqual(by["Bài dùng guid làm link"]["link"], "https://example.vn/guid-link")
        self.assertIsNone(by["Bài không có ngày đăng về ngân hàng"]["published"])
        self.assertEqual(by["Giờ GMT+7 kiểu Tuổi Trẻ"]["published"], vn(2026, 11, 7, 8, 48, 49))

    def test_broken_xml_falls_back_to_regex(self):
        data = (FIX / "broken.xml").read_bytes()
        import xml.etree.ElementTree as ET
        with self.assertRaises(ET.ParseError):  # đối chứng: đúng là XML hỏng
            ET.fromstring(data)
        items = R.parse_feed(data, "Hỏng")
        self.assertGreaterEqual(len(items), 2)
        self.assertEqual(items[0]["title"], "Chứng khoán & trái phiếu")
        self.assertEqual(items[1]["title"], "Cổ phiếu ngân hàng")

    def test_parse_date_formats(self):
        self.assertEqual(R.parse_date("Sat, 07 Nov 2026 08:00:00 +0700"), vn(2026, 11, 7, 8))
        self.assertEqual(R.parse_date("2026-11-07T01:00:00Z"), vn(2026, 11, 7, 8))
        self.assertEqual(R.parse_date("2026-11-07T08:00:00"), vn(2026, 11, 7, 8))  # không múi giờ = giờ VN
        self.assertEqual(R.parse_date("11/7/2026 12:05 PM"), vn(2026, 11, 7, 12, 5))
        self.assertEqual(R.parse_date("11/7/2026 12:05:00 AM"), vn(2026, 11, 7, 0, 5))
        self.assertEqual(R.parse_date("11/7/2026 7:01:00 PM"), vn(2026, 11, 7, 19, 1))
        for bad in ("", None, "hôm qua", "7/11/2026 07:01", "13/45/2026 7:00 AM", "11/7/2026 13:00 PM"):
            self.assertIsNone(R.parse_date(bad), bad)

    def test_mdy_negative_control(self):
        # "11/4/2026 7:01:00 AM" của Tuổi Trẻ là 4/11, không phải 11/4
        d = R.parse_date("11/4/2026 7:01:00 AM")
        self.assertEqual((d.day, d.month), (4, 11))
        self.assertNotEqual((d.day, d.month), (11, 4))

    def test_clean(self):
        self.assertEqual(R.clean("<p>A&nbsp;&amp;  B</p>\n C"), "A & B C")
        self.assertEqual(R.clean("&lt;b&gt;đậm&lt;/b&gt;"), "đậm")  # escape hai lần
        self.assertEqual(R.clean("Lãi suất &lt;5%, tỷ giá &gt; 25.000", title=True), "Lãi suất <5%, tỷ giá > 25.000")
        self.assertEqual(R.clean("<b>Tiêu đề</b> &aacute;", title=True), "Tiêu đề á")


class WindowAndSelect(unittest.TestCase):
    def test_window_bounds(self):
        d = date(2026, 11, 6)
        self.assertEqual(R.window_for(d, "sang", vn(2026, 11, 6, 7)), (vn(2026, 11, 5, 12), vn(2026, 11, 6, 7)))
        self.assertEqual(R.window_for(d, "sang", vn(2026, 11, 6, 20)), (vn(2026, 11, 5, 12), vn(2026, 11, 6, 12)))
        self.assertEqual(R.window_for(d, "chieu", vn(2026, 11, 6, 17)), (vn(2026, 11, 6), vn(2026, 11, 6, 17)))
        self.assertEqual(R.window_for(d, "chieu", vn(2026, 11, 8)), (vn(2026, 11, 6), vn(2026, 11, 6, 23, 59, 59)))

    def _sel(self, session, pubs, now):
        start, end = R.window_for(date(2026, 11, 6), session, now)
        items = [item(f"b{i}", f"https://e.vn/{i}", published=p) for i, p in enumerate(pubs)]
        return [it["title"] for it in R.select(items, start, end)]

    def test_start_is_inclusive_one_minute_before_is_dropped(self):
        self.assertEqual(self._sel("sang", [vn(2026, 11, 5, 12), vn(2026, 11, 5, 11, 59)], vn(2026, 11, 6, 7)), ["b0"])
        self.assertEqual(self._sel("chieu", [vn(2026, 11, 6), vn(2026, 11, 5, 23, 59)], vn(2026, 11, 6, 17)), ["b0"])

    def test_future_slack(self):
        now = vn(2026, 11, 6, 17)
        got = self._sel("chieu", [now + timedelta(minutes=9), now + timedelta(minutes=10, seconds=1),
                                  now + timedelta(minutes=30)], now)
        self.assertEqual(got, ["b0"])

    def test_undated_dropped_and_sorted_newest_first(self):
        now = vn(2026, 11, 6, 17)
        got = self._sel("chieu", [vn(2026, 11, 6, 9), None, vn(2026, 11, 6, 15)], now)
        self.assertEqual(got, ["b2", "b0"])

    def test_dedupe_by_canonical_url(self):
        p = vn(2026, 11, 6, 9)
        links = ["https://cafef.vn/bai-a.chn", "https://cafef.vn/bai-a.chn?utm_source=rss&utm_medium=x",
                 "https://cafef.vn/bai-a.chn#binh-luan", "https://cafef.vn/bai-a.chn/", "https://CafeF.VN/bai-a.chn",
                 "HTTPS://cafef.vn/bai-a.chn"]
        items = [item(f"t{i}", u, published=p) for i, u in enumerate(links)]
        out = R.select(items, vn(2026, 11, 6), vn(2026, 11, 6, 17))
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["link"], links[0])  # giữ URL gốc của lần gặp đầu
        # đối chứng: query khác (không phải utm) là bài khác
        self.assertNotEqual(R.canon_url("https://e.vn/a?id=1"), R.canon_url("https://e.vn/a?id=2"))

    def test_non_http_links_dropped(self):
        items = [item("x", "javascript:alert(1)", published=vn(2026, 11, 6, 9)),
                 item("y", "https://e.vn/c d", published=vn(2026, 11, 6, 9))]
        self.assertEqual(R.select(items, vn(2026, 11, 6), vn(2026, 11, 6, 17)), [])


class Classify(unittest.TestCase):
    clf = R.Classifier(CFG)

    def test_banking_title(self):
        self.assertGreaterEqual(self.clf.group_score(item("Lãi suất huy động tiếp tục giảm"), "banking"), 3)

    def test_hint_alone_is_not_enough(self):
        # Vòng sửa 1 (L1): hint chỉ +2, dưới ngưỡng 3 – bài feed chuyên mục không có từ khóa thì KHÔNG vào
        self.assertEqual(CFG["hint_bonus"], 2)
        it = item("Nhà sản xuất Anh trai vượt ngàn chông gai thay chủ tịch", hint="securities")
        self.assertEqual(self.clf.group_score(it, "securities"), 2)
        self.assertLess(self.clf.group_score(it, "securities"), 3)
        it = item("Đột nhập nhà dân trộm nhiều vàng, trang sức", desc="Kẻ gian lấy đi nhiều tài sản.", hint="banking")
        self.assertLess(self.clf.group_score(it, "banking"), 3)

    def test_hint_plus_one_desc_keyword_is_enough(self):
        # chiều ngược lại: cùng feed, desc nhắc 1 từ khóa → 2 + 1 = 3, vào mảng
        it = item("Nhà sản xuất Anh trai vượt ngàn chông gai thay chủ tịch",
                  desc="Doanh nghiệp vừa công bố thay đổi nhân sự cấp cao, cổ đông lớn ủng hộ.", hint="securities")
        self.assertEqual(self.clf.group_score(it, "securities"), 3)
        # đối chứng: không có hint thì chỉ 1 điểm, không vào
        self.assertEqual(self.clf.group_score(dict(it, hint=None), "securities"), 1)

    def test_hint_bonus_from_config(self):
        clf = R.Classifier(dict(CFG, hint_bonus=3))
        it = item("Nhà sản xuất Anh trai vượt ngàn chông gai thay chủ tịch", hint="securities")
        self.assertEqual(clf.group_score(it, "securities"), 3)
        self.assertEqual(R.Classifier({k: v for k, v in CFG.items() if k != "hint_bonus"}).hint_bonus, R.HINT_BONUS)

    def test_bare_xay_dung_not_construction(self):
        self.assertNotIn("xây dựng", next(s for s in CFG["sectors"] if s["name"] == "Xây dựng – hạ tầng")["keywords"])
        name, _ = self.clf.sector_of(item("Cần Thơ, Vĩnh Long, Đồng Tháp xây dựng mô hình tăng trưởng mới"))
        self.assertNotEqual(name, "Xây dựng – hạ tầng")
        # chiều ngược lại: nhắc hạ tầng thật vẫn vào
        self.assertEqual(self.clf.sector_of(item("Khởi công cao tốc Bắc – Nam đoạn qua Nghệ An"))[0], "Xây dựng – hạ tầng")
        self.assertEqual(self.clf.sector_of(item("Nhà thầu chậm tiến độ công trình trường học"))[0], "Xây dựng – hạ tầng")

    def test_gold_sector(self):
        self.assertEqual(self.clf.sector_of(item("Giá vàng SJC lập đỉnh mới"))[0], "Vàng – trang sức")

    def test_desc_once_is_below_threshold(self):
        it = item("Doanh nghiệp xuất khẩu tăng tốc cuối năm", desc="Vốn vay từ ngân hàng giúp mở rộng sản xuất.")
        self.assertEqual(self.clf.group_score(it, "banking"), 1)
        self.assertLess(self.clf.group_score(it, "banking"), 3)
        # đối chứng: nhắc trong title thì vào
        self.assertGreaterEqual(self.clf.group_score(item("Ngân hàng mở rộng cho vay"), "banking"), 3)

    def test_sector_tie_takes_config_order(self):
        # "bất động sản" (ngành 1) và "hạ tầng" (ngành 6) mỗi bên 1 lần trong title
        self.assertEqual(self.clf.sector_of(item("Bất động sản hưởng lợi từ hạ tầng"))[0], "Bất động sản")

    def test_impact(self):
        self.assertEqual(self.clf.impact("VN-Index bứt phá, thanh khoản phục hồi"), "tich-cuc")
        self.assertEqual(self.clf.impact("Cổ phiếu lao dốc, nhà đầu tư bán tháo"), "tieu-cuc")
        self.assertEqual(self.clf.impact("Thị trường phục hồi nhưng rủi ro còn"), "trung-tinh")
        self.assertEqual(self.clf.impact("Họp đại hội cổ đông thường niên"), "trung-tinh")

    def test_bao_lo_counted_once(self):
        self.assertEqual(R.count_terms("doanh nghiệp báo lỗ quý 3", self.clf.neg), 1)
        # một từ tích cực vs "báo lỗ": nếu "lỗ" bị đếm thêm thì thành tiêu cực
        self.assertEqual(self.clf.impact("Tăng trưởng doanh thu nhưng báo lỗ"), "trung-tinh")

    def test_heat_for(self):
        for n, h in [(0, 1), (2, 1), (3, 2), (5, 2), (6, 3), (9, 3), (10, 4), (14, 4), (15, 5), (40, 5)]:
            self.assertEqual(R.heat_for(n), h, n)


def fixture_items():
    items = []
    hints = {"cafef": "banking", "vietstock": "securities", "vneconomy": "securities"}
    names = {"vnexpress": "VnExpress", "cafef": "CafeF", "vietstock": "Vietstock", "vneconomy": "VnEconomy",
             "tuoitre": "Tuổi Trẻ", "thanhnien": "Thanh Niên", "dantri": "Dân trí", "vietnamnet": "VietnamNet"}
    for n in REAL:
        for it in R.parse_feed((FIX / f"{n}.xml").read_bytes(), names[n]):
            it["hint"] = hints.get(n)
            items.append(it)
    return items


class BuildEdition(unittest.TestCase):
    NOW = vn(2026, 11, 7, 9, 45)

    def build(self, items=None, d=date(2026, 11, 7), session="sang"):
        return R.build_edition(fixture_items() if items is None else items, CFG, d, session, self.NOW, ["X http://x"])

    def write(self, ed):
        tmp = Path(tempfile.mkdtemp(prefix="ntt-rss-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        p = tmp / "daily" / f"{ed['key']}.json"
        R.write_json(p, ed)
        return p

    def test_structure_and_validate(self):
        ed = self.build()
        rep = check_daily(self.write(ed))
        self.assertEqual(rep.errors, [])
        self.assertEqual(ed["mode"], "rss")
        self.assertEqual((ed["key"], ed["date"], ed["session"]), ("2026-11-07-sang", "2026-11-07", "sang"))
        self.assertTrue(ed["updatedAt"].endswith("+07:00"))
        self.assertEqual(len(ed["sectors"]), 5)
        for g in ("banking", "securities"):
            m = ed[g]
            self.assertEqual(m["metrics"], [])
            self.assertGreaterEqual(len(m["articles"]), 1)
            self.assertLessEqual(len(m["news"]), 5)
            self.assertNotIn("analysis", m)
            self.assertNotIn("watch", m)
            self.assertIn("Bản tự động từ RSS, chưa có số liệu và nhận định.", m["summary"])
            urls = [a["url"] for a in m["articles"]]
            self.assertEqual(len(urls), len(set(urls)))
            for a in m["articles"]:
                self.assertEqual(set(a), {"title", "source", "url", "time"})
                self.assertRegex(a["time"], r"^\d{2}/\d{2} \d{2}:\d{2}$")
            for n in m["news"]:
                self.assertEqual(set(n), {"title", "impact", "source", "url"})
            times = [datetime.strptime("2026/" + a["time"], "%Y/%d/%m %H:%M") for a in m["articles"]]
            self.assertEqual(times, sorted(times, reverse=True))
        for s in ed["sectors"]:
            self.assertEqual(s["status"], "soi-noi")
            self.assertTrue(1 <= s["heat"] <= 5)
            self.assertNotIn("stocks", s)
            self.assertNotIn("body", s)
        text = json.dumps(ed, ensure_ascii=False)
        self.assertNotIn('"body"', text)
        self.assertNotIn('"summary": "', json.dumps([a for g in ("banking", "securities") for a in ed[g]["articles"]]))
        self.assertEqual(ed["rss"]["failed"], ["X http://x"])
        self.assertEqual(ed["rss"]["window"], ["2026-11-06T12:00:00+07:00", "2026-11-07T09:45:00+07:00"])

    def test_window_excludes_old_fixture_items(self):
        ed = self.build()
        allarts = ed["banking"]["articles"] + ed["securities"]["articles"] + [a for s in ed["sectors"] for a in s["articles"]]
        # vietstock có bài 18:17 06/11 (trong cửa sổ sáng 07/11); không có bài nào trước 12:00 06/11
        for a in allarts:
            t = datetime.strptime("2026/" + a["time"], "%Y/%d/%m %H:%M").replace(tzinfo=VN_TZ)
            self.assertGreaterEqual(t, vn(2026, 11, 6, 12))

    def test_known_classification_on_real_fixture(self):
        ed = self.build()
        bank = {a["title"] for a in ed["banking"]["articles"]}
        self.assertIn("Lãi suất tăng, ngân hàng nào hút nhiều tiền gửi nhất?", bank)
        gold = next(s for s in ed["sectors"] if s["name"] == "Vàng – trang sức")
        self.assertGreaterEqual(len(gold["articles"]), 3)
        self.assertEqual(gold["reason"], f"{len(gold['articles'])} bài trong khung giờ, từ "
                                         f"{len({a['source'] for a in gold['articles']})} nguồn.")
        self.assertEqual(gold["url"], gold["articles"][0]["url"])

    def test_fewer_than_five_sectors_padded(self):
        items = [item("Giá vàng SJC giảm", "https://e.vn/v", published=vn(2026, 11, 7, 8), source="A"),
                 item("Lãi suất huy động giảm", "https://e.vn/l", published=vn(2026, 11, 7, 8), source="A")]
        ed = self.build(items)
        self.assertEqual(len(ed["sectors"]), 5)
        self.assertEqual(ed["sectors"][0]["name"], "Vàng – trang sức")
        names = [s["name"] for s in CFG["sectors"] if s["name"] != "Vàng – trang sức"][:4]
        self.assertEqual([s["name"] for s in ed["sectors"][1:]], names)
        for s in ed["sectors"][1:]:
            self.assertEqual((s["heat"], s["articles"], s["reason"]), (1, [], "Chưa có bài trong khung giờ này."))
            self.assertNotIn("url", s)
            self.assertNotIn("source", s)
        self.assertEqual(ed["securities"]["articles"], [])
        self.assertEqual(ed["securities"]["summary"],
                         "Chưa có bài về chứng khoán trong khung giờ này. Bản tự động từ RSS, chưa có số liệu và nhận định.")
        self.assertEqual(check_daily(self.write(ed)).errors, [])

    def test_news_order_by_score(self):
        items = [item("Ngân hàng mới", "https://e.vn/1", published=vn(2026, 11, 7, 9), source="A"),
                 item("Ngân hàng tăng lãi suất tiền gửi", "https://e.vn/2", published=vn(2026, 11, 7, 7), source="B")]
        ed = self.build(items)
        self.assertEqual([n["url"] for n in ed["banking"]["news"]], ["https://e.vn/2", "https://e.vn/1"])
        self.assertEqual([a["url"] for a in ed["banking"]["articles"]], ["https://e.vn/1", "https://e.vn/2"])

    def test_negative_controls_validate(self):
        ed = self.build()
        bad = copy.deepcopy(ed)
        bad["banking"]["metrics"] = [{"label": "Tỷ giá", "value": "25.615"}]
        self.assertTrue(any("bản rss không được có metrics" in e for e in check_daily(self.write(bad)).errors))
        bad = copy.deepcopy(ed)
        bad["mode"] = "xyz"
        self.assertTrue(any(e.startswith("mode phải thuộc") for e in check_daily(self.write(bad)).errors))
        # bản rss không bị WARN metrics/analysis; bản AI thiếu thì có
        warns = check_daily(self.write(ed)).warns
        self.assertFalse(any("metrics có" in w or "analysis trống" in w for w in warns))
        ai = copy.deepcopy(ed)
        del ai["mode"]
        warns = check_daily(self.write(ai)).warns
        self.assertTrue(any("metrics có 0 ô" in w for w in warns))
        self.assertTrue(any("analysis trống" in w for w in warns))


def make_feed(path: Path, entries):
    """Feed RSS tạm với ngày đăng tương đối so với hiện tại (giờ VN)."""
    body = "".join(f"<item><title>{t}</title><link>{u}</link><pubDate>{format_datetime(p)}</pubDate></item>"
                   for t, u, p in entries)
    path.write_text(f'<?xml version="1.0" encoding="utf-8"?><rss version="2.0"><channel><title>t</title>{body}'
                    "</channel></rss>", encoding="utf-8")


class EndToEnd(unittest.TestCase):
    """Chạy script thật với config file:// trỏ tới feed tạm. Ngày = hôm nay giờ VN."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="ntt-e2e-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.now = R.now_vn()
        self.session = R.session_for(self.now)
        self.d = self.now.date()
        if self.session == "chieu" and self.d.weekday() >= 5:
            self.session = "sang"  # chiều cuối tuần không có bản tin: dùng bản sáng cùng ngày
        # bài mẫu đăng ngay trước mốc cuối của cửa sổ (sáng: min(lúc chạy, 12:00))
        self.recent = R.window_for(self.d, self.session, self.now)[1] - timedelta(seconds=30)

    def config(self, sources):
        cfg = dict(CFG, sources=sources)
        p = self.tmp / "cfg.json"
        p.write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")
        return p

    def args(self, cfg, out):
        return ["--date", self.d.isoformat(), "--session", self.session, "--out", str(out), "--config", str(cfg)]

    def test_writes_into_out_dir(self):
        recent = self.recent
        feed = self.tmp / "f.xml"
        make_feed(feed, [("Lãi suất huy động giảm", "https://e.vn/ls", recent),
                         ("Cổ phiếu ngân hàng tăng", "https://e.vn/cp", recent),
                         ("Giá vàng SJC đi ngang", "https://e.vn/v", recent)])
        cfg = self.config([{"name": "Mẫu", "url": feed.as_uri()},
                           {"name": "Chết", "url": (self.tmp / "khong-co.xml").as_uri()}])
        out = self.tmp / "out"
        r = run_script(*self.args(cfg, out))
        self.assertEqual(r.returncode, 0, r.stderr.decode("utf-8", "replace"))
        files = list((out / "daily").glob("*.json"))
        self.assertEqual([f.name for f in files], [f"{self.d.isoformat()}-{self.session}.json"])
        ed = read_json(files[0])
        self.assertEqual(ed["mode"], "rss")
        self.assertEqual(len(ed["rss"]["failed"]), 1)
        self.assertEqual(check_daily(files[0]).errors, [])
        self.assertFalse((out / "index.json").exists())  # --out không gọi build_index
        self.assertIn("Đã ghi", r.stdout.decode("utf-8"))

        # Không ghi đè bản AI: bỏ mode → chạy lại → giữ nguyên từng byte
        ai = dict(ed)
        del ai["mode"]
        R.write_json(files[0], ai)
        before = files[0].read_bytes()
        r = run_script(*self.args(cfg, out))
        self.assertEqual(r.returncode, 0)
        self.assertIn("bỏ qua", r.stdout.decode("utf-8"))
        self.assertEqual(files[0].read_bytes(), before)
        # JSON hỏng cũng không bị chạm vào
        files[0].write_text("{hỏng", encoding="utf-8")
        r = run_script(*self.args(cfg, out))
        self.assertEqual((r.returncode, files[0].read_text(encoding="utf-8")), (0, "{hỏng"))
        # Đối chứng: bản rss thì được ghi mới
        R.write_json(files[0], dict(ed, updatedAt="2000-01-01T00:00:00+07:00"))
        r = run_script(*self.args(cfg, out))
        self.assertEqual(r.returncode, 0)
        self.assertNotEqual(read_json(files[0])["updatedAt"], "2000-01-01T00:00:00+07:00")

    def test_all_feeds_fail_exit_3_no_file(self):
        cfg = self.config([{"name": "Chết", "url": (self.tmp / "a.xml").as_uri()},
                           {"name": "Chết 2", "url": (self.tmp / "b.xml").as_uri()}])
        out = self.tmp / "out"
        r = run_script(*self.args(cfg, out))
        self.assertEqual(r.returncode, 3, r.stderr.decode("utf-8", "replace"))
        self.assertFalse((out / "daily").exists() and any((out / "daily").iterdir()))

    def test_env_config_and_weekend_afternoon(self):
        cfg = self.config([{"name": "Chết", "url": (self.tmp / "a.xml").as_uri()}])
        out = self.tmp / "out"
        r = run_script("--date", "2026-11-07", "--session", "chieu", "--out", str(out),
                       env_extra={"NTT_RSS_CONFIG": str(cfg)})
        self.assertEqual(r.returncode, 0)
        self.assertIn("Chiều cuối tuần", r.stdout.decode("utf-8"))
        self.assertFalse(out.exists())
        # --check-feeds đọc config từ biến môi trường, in một dòng mỗi feed, mã 0
        r = run_script("--check-feeds", env_extra={"NTT_RSS_CONFIG": str(cfg)})
        self.assertEqual(r.returncode, 0)
        lines = r.stdout.decode("utf-8").strip().splitlines()
        self.assertEqual(len(lines), 1)
        self.assertIn("lỗi", lines[0])

    def test_weekly_key_untouched(self):
        # tầng RSS không ghi weekly
        self.assertNotIn("weekly", R.build_edition([], CFG, date(2026, 11, 6), "chieu", vn(2026, 11, 6, 17)))
        self.assertEqual(weekly_key(date(2026, 11, 6)), "2026-W45")


class ConfigFile(unittest.TestCase):
    def test_config_shape(self):
        self.assertTrue(CFG["sources"])
        for s in CFG["sources"]:
            self.assertRegex(s["url"], r"^https://")
            self.assertNotRegex(s["url"], r"cnbc|bbc")
            self.assertIn(s.get("hint"), (None, "banking", "securities"))
        names = [s["name"] for s in CFG["sectors"]]
        for n in names:
            if " " in n and "–" not in n and n not in ("Bất động sản", "Bảo hiểm"):
                self.fail(f"tên ngành phải dùng gạch ngang U+2013: {n}")
        self.assertIn("Vàng – trang sức", names)
        all_terms = [t for s in CFG["sentiment"].values() for t in s]
        self.assertFalse([t for t in all_terms if t.isascii() and " " not in t], "còn từ tiếng Anh")


if __name__ == "__main__":
    unittest.main(verbosity=2)
