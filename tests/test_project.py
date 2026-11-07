"""Test cho server.py, workflow, validate.py và đối chiếu số liệu tab Phân tích.

Chạy:  python -m unittest discover -s tests -v
Chỉ dùng thư viện chuẩn. Test cần node / git / Chrome-Edge sẽ tự bỏ qua nếu máy không có.
"""
from __future__ import annotations

import http.client
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from collections import Counter
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import server  # noqa: E402

NODE = shutil.which("node")


def start(handler_cls, **kw):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler_cls if not kw else (lambda *a: handler_cls(*a, **kw)))
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    return srv


def request(port, method, path, body=None, headers=None):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    c.request(method, path, body=body, headers=headers or {})
    r = c.getresponse()
    data = r.read()
    c.close()
    return r.status, r.getheader("Content-Type") or "", data


class QuietHandler(server.Handler):
    def log_message(self, *a):
        pass


class ServerTests(unittest.TestCase):
    """TC9: server.py"""

    @classmethod
    def setUpClass(cls):
        cls.srv = start(QuietHandler)
        cls.port = cls.srv.server_address[1]

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()

    def test_root_200(self):
        st, _, body = request(self.port, "GET", "/")
        self.assertEqual(st, 200)
        self.assertIn("Nhịp Thị Trường".encode("utf-8"), body)

    def test_index_json_200(self):
        st, _, body = request(self.port, "GET", "/data/index.json")
        self.assertEqual(st, 200)
        self.assertIn("daily", json.loads(body))

    def test_health_ok(self):
        for path in ("/api/health", "/api/health?t=1"):
            st, ct, body = request(self.port, "GET", path)
            self.assertEqual(st, 200, path)
            self.assertIn("application/json", ct)
            j = json.loads(body)
            self.assertIs(j["ok"], True)
            self.assertIsInstance(j["ai"], bool)

    def test_unknown_api_404(self):
        st, _, _ = request(self.port, "GET", "/api/khong-co")
        self.assertEqual(st, 404)

    def test_post_ask_empty_400(self):
        st, _, body = request(self.port, "POST", "/api/ask", body=b"", headers={"Content-Length": "0"})
        self.assertEqual(st, 400)
        self.assertIn("error", json.loads(body))

    def test_post_other_404(self):
        st, _, _ = request(self.port, "POST", "/api/health", body=b"{}", headers={"Content-Length": "2"})
        self.assertEqual(st, 404)


def free_port():
    import socket
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def run_server_cp1252(script: Path, cwd: Path, wait=10.0):
    """Chạy server.py như khi stdout bị chuyển hướng trên Windows (cp1252).
    Trả (còn sống sau khi chờ?, mã HTTP của /api/health hoặc None, stderr)."""
    import time
    port = free_port()
    env = dict(os.environ, PYTHONIOENCODING="cp1252")
    proc = subprocess.Popen([sys.executable, str(script), "--port", str(port)], cwd=cwd, env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    status = None
    try:
        deadline = time.time() + wait
        while time.time() < deadline and proc.poll() is None:
            try:
                status = request(port, "GET", "/api/health")[0]
                break
            except OSError:
                time.sleep(0.2)
        alive = proc.poll() is None
    finally:
        proc.kill()
        _, err = proc.communicate(timeout=10)
    return alive, status, err.decode("utf-8", "replace")


class ServerEncodingTests(unittest.TestCase):
    """L1: server.py không crash khi stdout là cp1252 (Task Scheduler, file log trên Windows)."""

    def test_cp1252_stdout_does_not_crash(self):
        alive, status, err = run_server_cp1252(ROOT / "server.py", ROOT)
        self.assertTrue(alive, err[-1500:])
        self.assertEqual(status, 200, err[-1500:])

    def test_negative_control_without_reconfigure_crashes(self):
        # Đối chứng âm: bản sao server.py bỏ dòng reconfigure phải crash UnicodeEncodeError.
        src = (ROOT / "server.py").read_text(encoding="utf-8")
        line = 'sys.stdout.reconfigure(encoding="utf-8", errors="replace")'
        self.assertIn(line, src)
        tmp = Path(tempfile.mkdtemp(prefix="ntt-neg-"))
        try:
            bad = tmp / "server.py"
            bad.write_text(src.replace(line, "pass"), encoding="utf-8")
            alive, status, err = run_server_cp1252(bad, tmp, wait=5.0)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        self.assertFalse(alive)
        self.assertIsNone(status)
        self.assertIn("UnicodeEncodeError", err)


class WorkflowTests(unittest.TestCase):
    """TC15: kiểm tra tĩnh .github/workflows/bantin.yml bằng regex (không phụ thuộc PyYAML)."""

    text = (ROOT / ".github" / "workflows" / "bantin.yml").read_text(encoding="utf-8")

    def step_block(self, name_re):
        m = re.search(r"^      - (?:name: )?" + name_re + r".*?(?=^      - |^  \w|\Z)", self.text, re.S | re.M)
        self.assertIsNotNone(m, name_re)
        return m.group(0)

    def test_key_step_first(self):
        steps = self.text.split("    steps:", 1)[1]
        first = re.search(r"^      - (.*)$", steps, re.M).group(1)
        self.assertIn("Kiểm tra khóa API", first)
        blk = self.step_block("Kiểm tra khóa API")
        self.assertIn("id: key", blk)
        self.assertIn('echo "has=true" >> "$GITHUB_OUTPUT"', blk)
        self.assertIn('echo "has=false" >> "$GITHUB_OUTPUT"', blk)
        self.assertIn("::warning::", blk)

    def test_guarded_steps(self):
        cond = "if: steps.key.outputs.has == 'true'"
        for name in (r"uses: actions/setup-python", r"run: pip install", r"Soạn bản tin", r"Lưu dữ liệu mới vào repo"):
            self.assertIn(cond, self.step_block(name), name)
        # checkout của job collect không bị chặn: dòng ngay sau nó là bước kế tiếp
        collect = self.text.split("  deploy:", 1)[0]
        self.assertRegex(collect, r"      - uses: actions/checkout@v4\n      - uses: actions/setup-python")

    def test_deploy_condition_and_cron(self):
        deploy = self.text.split("  deploy:", 1)[1]
        self.assertIn("needs: collect", deploy)
        self.assertIn("if: always() && (needs.collect.result == 'success' || needs.collect.result == 'skipped')", deploy)
        self.assertIn("ref: main", deploy)
        self.assertIn('cron: "45 23 * * *"', self.text)
        self.assertIn('cron: "45 9 * * *"', self.text)
        self.assertIn("Không có secret thì workflow vẫn đăng lại trang", self.text.splitlines()[1])

    def test_no_tabs_in_yaml(self):
        self.assertNotIn("\t", self.text)


class DataTests(unittest.TestCase):
    """TC1: validate.py trả 0. (Việc data/ docs/ scripts/ không đổi do Tester kiểm bằng git status khi nghiệm thu.)"""

    def test_validate_returns_0(self):
        r = subprocess.run([sys.executable, str(ROOT / "scripts" / "validate.py")], cwd=ROOT, capture_output=True)
        self.assertEqual(r.returncode, 0, r.stdout.decode("utf-8", "replace")[-2000:])


def load_daily(keys):
    """Đọc đúng danh sách key mà phía JS đã dùng (ntt_load.js in ra), để hai bên so cùng một tập."""
    return [json.loads((ROOT / "data" / "daily" / f"{k}.json").read_text(encoding="utf-8")) for k in keys]


def expected_edition_count():
    """Số bản tin trang hiển thị: min(số key trong index.json, 40) – giống loadData."""
    idx = json.loads((ROOT / "data" / "index.json").read_text(encoding="utf-8"))
    return min(len(idx.get("daily") or []), 40)


@unittest.skipUnless(NODE, "không có node")
class AnalyticsCrossCheck(unittest.TestCase):
    """TC10: so số liệu JS với cách đếm độc lập bằng Python."""

    @classmethod
    def setUpClass(cls):
        r = subprocess.run([NODE, str(ROOT / "tests" / "ntt_load.js")], capture_output=True)
        if r.returncode:
            raise RuntimeError(r.stderr.decode("utf-8", "replace"))
        cls.js = json.loads(r.stdout.decode("utf-8"))
        cls.daily = load_daily(cls.js["keys"])

    def test_same_edition_set_and_limit(self):
        self.assertEqual(len(self.daily), self.js["editions"])
        self.assertEqual(len(self.js["keys"]), expected_edition_count())

    def test_sector_top5_counts(self):
        c = Counter()
        for d in self.daily:
            names = {str(s.get("name") or "").strip() for s in (d.get("sectors") or [])[:5]} - {""}
            c.update(names)
        js = {r["name"]: r["count"] for r in self.js["sectors"]}
        self.assertEqual(js, dict(c))
        top = self.js["sectors"][0]
        self.assertEqual(top["count"], max(c.values()))

    def test_unique_articles_by_url(self):
        seen, src = set(), Counter()
        for d in self.daily:
            arts = list((d.get("banking") or {}).get("articles") or []) + list((d.get("securities") or {}).get("articles") or [])
            for s in d.get("sectors") or []:
                arts += s.get("articles") or []
            for a in arts:
                url = str(a.get("url") or "").strip()
                k = "u:" + url if url else "t:" + str(a.get("title") or "").strip() + "|" + str(a.get("source") or "").strip()
                if k in seen:
                    continue
                seen.add(k)
                src[str(a.get("source") or "").strip() or "Khác"] += 1
        self.assertEqual(self.js["sources"]["unique"], len(seen))
        self.assertEqual(self.js["sources"]["sources"], len(src))
        self.assertEqual(self.js["sources"]["list"][0]["n"], src.most_common(1)[0][1])

    def test_sentiment_totals(self):
        t = Counter()
        for d in self.daily:
            for k in ("banking", "securities"):
                for n in (d.get(k) or {}).get("news") or []:
                    t[n.get("impact")] += 1
        tot = self.js["sentiment"]["total"]
        self.assertEqual((tot["pos"], tot["neu"], tot["neg"]), (t["tich-cuc"], t["trung-tinh"], t["tieu-cuc"]))

    def test_series_only_afternoon(self):
        for sid, pts in self.js["series"].items():
            for p in pts:
                self.assertTrue(p["key"].endswith("-chieu"), (sid, p["key"]))


def find_browser():
    cands = [
        os.environ.get("NTT_BROWSER"),
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        shutil.which("google-chrome"), shutil.which("chromium"), shutil.which("chromium-browser"), shutil.which("msedge"),
    ]
    for c in cands:
        if c and Path(c).exists():
            return c
    return None


BROWSER = find_browser()


def dump_dom(url, width=1200):
    prof = tempfile.mkdtemp(prefix="ntt-prof-")
    try:
        r = subprocess.run([BROWSER, "--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
                            f"--user-data-dir={prof}", f"--window-size={width},900", "--virtual-time-budget=8000",
                            "--enable-logging=stderr", "--v=0", "--dump-dom", url],
                           capture_output=True, timeout=90)
        dom = r.stdout.decode("utf-8", "replace")
        # Bỏ mã nguồn <script>/<style> để chỉ kiểm tra phần trang đã render
        dom = re.sub(r"<(script|style)\b[^>]*>.*?</\1>", "", dom, flags=re.S | re.I)
        return dom, r.stderr.decode("utf-8", "replace")
    finally:
        shutil.rmtree(prof, ignore_errors=True)


def console_errors(stderr):
    # Dòng console của trang có dạng: CONSOLE(123)] "..." / "Uncaught ..."
    return [ln for ln in stderr.splitlines() if "CONSOLE" in ln and ("Uncaught" in ln or "Error" in ln)]


@unittest.skipUnless(BROWSER, "không có Chrome/Edge headless")
class BrowserSmoke(unittest.TestCase):
    """TC11 (một phần): render thật bằng trình duyệt headless, đọc DOM sau khi chạy JS."""

    def test_with_server_py_qa_visible_and_analysis_rendered(self):
        srv = start(QuietHandler)
        try:
            dom, err = dump_dom(f"http://127.0.0.1:{srv.server_address[1]}/")
        finally:
            srv.shutdown(); srv.server_close()
        self.assertRegex(dom, r'<button[^>]*id="t-qa"(?![^>]*hidden)[^>]*>')
        self.assertRegex(dom, rf"Tổng hợp từ {expected_edition_count()} bản tin, \d{{2}}/\d{{2}} – \d{{2}}/\d{{2}}/\d{{4}}")
        self.assertIn("Hỏi Claude về tin này", dom)
        self.assertIn('class="keybar"', dom)
        self.assertNotIn("Không tính được mục này", dom)
        self.assertIn("Bản tin tiếp theo dự kiến", dom)
        self.assertIn("kiểm tra lúc", dom)
        self.assertEqual(console_errors(err), [])

    def test_static_host_hides_qa(self):
        site = Path(tempfile.mkdtemp(prefix="ntt-site-"))
        try:
            shutil.copytree(ROOT / "web", site, dirs_exist_ok=True)
            (site / "data").mkdir()
            for d in ("daily", "weekly"):
                shutil.copytree(ROOT / "data" / d, site / "data" / d)
            shutil.copy(ROOT / "data" / "index.json", site / "data" / "index.json")

            class Static(SimpleHTTPRequestHandler):
                def __init__(self, *a, **k):
                    super().__init__(*a, directory=str(site), **k)

                def log_message(self, *a):
                    pass

            srv = start(Static)
            try:
                dom, err = dump_dom(f"http://127.0.0.1:{srv.server_address[1]}/", width=375)
            finally:
                srv.shutdown(); srv.server_close()
        finally:
            shutil.rmtree(site, ignore_errors=True)
        self.assertRegex(dom, r'<button[^>]*id="t-qa"[^>]*hidden')
        self.assertNotIn("Hỏi Claude về tin này", dom)
        self.assertIn(f"Tổng hợp từ {expected_edition_count()} bản tin", dom)
        self.assertEqual(console_errors(err), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
