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
    """L1/N3: server.py không crash khi stdout là cp1252 (Task Scheduler, file log trên Windows)."""

    LINE = 's.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)'

    def test_cp1252_stdout_does_not_crash(self):
        alive, status, err = run_server_cp1252(ROOT / "server.py", ROOT)
        self.assertTrue(alive, err[-1500:])
        self.assertEqual(status, 200, err[-1500:])

    def test_negative_control_without_reconfigure_crashes(self):
        # Đối chứng âm: bản sao server.py bỏ dòng s.reconfigure(...) phải crash UnicodeEncodeError.
        src = (ROOT / "server.py").read_text(encoding="utf-8")
        self.assertIn(self.LINE, src)
        tmp = Path(tempfile.mkdtemp(prefix="ntt-neg-"))
        try:
            bad = tmp / "server.py"
            bad.write_text(src.replace(self.LINE, "pass"), encoding="utf-8")
            alive, status, err = run_server_cp1252(bad, tmp, wait=5.0)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        self.assertFalse(alive)
        self.assertIsNone(status)
        self.assertIn("UnicodeEncodeError", err)


def run_server_to_file(script: Path, cwd: Path, wait=10.0):
    """N4: chạy server với stdout là FILE. Khi /api/health trả 200 (tiến trình còn sống) thì đọc ngay file log.
    Trả (còn sống?, mã HTTP, nội dung file lúc đó)."""
    import time
    port = free_port()
    tmp = Path(tempfile.mkdtemp(prefix="ntt-log-"))
    log = tmp / "server.log"
    env = dict(os.environ, PYTHONIOENCODING="cp1252")
    try:
        with open(log, "wb") as out:
            proc = subprocess.Popen([sys.executable, str(script), "--port", str(port)], cwd=cwd, env=env,
                                    stdout=out, stderr=subprocess.DEVNULL)  # chỉ xét stdout (log truy cập ở stderr)
            status, content, alive = None, "", False
            try:
                deadline = time.time() + wait
                while time.time() < deadline and proc.poll() is None:
                    try:
                        status = request(port, "GET", "/api/health")[0]
                        break
                    except OSError:
                        time.sleep(0.2)
                alive = proc.poll() is None
                time.sleep(0.3)
                content = log.read_bytes().decode("utf-8", "replace")
            finally:
                proc.kill()
                proc.wait(timeout=10)
        return alive, status, content
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


class ServerLogTests(unittest.TestCase):
    """N4: dòng khởi động được ghi ngay vào file log nhờ line_buffering=True."""

    def test_startup_line_in_file_while_running(self):
        alive, status, content = run_server_to_file(ROOT / "server.py", ROOT)
        self.assertTrue(alive)
        self.assertEqual(status, 200)
        self.assertIn("Nhịp Thị Trường: http://localhost:", content)

    def test_negative_control_without_line_buffering_file_empty(self):
        src = (ROOT / "server.py").read_text(encoding="utf-8")
        self.assertIn(", line_buffering=True)", src)
        tmp = Path(tempfile.mkdtemp(prefix="ntt-neg-"))
        try:
            bad = tmp / "server.py"
            bad.write_text(src.replace(", line_buffering=True)", ")"), encoding="utf-8")
            alive, status, content = run_server_to_file(bad, tmp)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        self.assertTrue(alive)
        self.assertEqual(status, 200)
        self.assertEqual(content, "")


def copy_repo(prefix="ntt-copy-"):
    tmp = Path(tempfile.mkdtemp(prefix=prefix))
    for d in ("scripts", "docs", "data"):
        shutil.copytree(ROOT / d, tmp / d, ignore=shutil.ignore_patterns("__pycache__", "*.tmp"))
    return tmp


class StdioTests(unittest.TestCase):
    """N3 (T8): mọi script chạy được khi stdout là pipe và PYTHONIOENCODING=cp1252 (trên bản sao repo)."""

    CMDS = [["build_index.py"], ["validate.py"], ["collect.py", "--dry-run", "--date", "2026-11-06", "--session", "chieu"],
            ["rss_collect.py", "--help"], ["ci_guard.py", "plan", "--date", "2026-11-06", "--session", "chieu"]]

    @classmethod
    def setUpClass(cls):
        cls.tmp = copy_repo("ntt-stdio-")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def run_cp1252(self, args):
        env = dict(os.environ, PYTHONIOENCODING="cp1252")
        env.pop("PYTHONUTF8", None)
        return subprocess.run([sys.executable, str(self.tmp / "scripts" / args[0]), *args[1:]], cwd=self.tmp,
                              env=env, capture_output=True)

    def test_scripts_cp1252(self):
        for args in self.CMDS:
            with self.subTest(cmd=" ".join(args)):
                r = self.run_cp1252(args)
                err = r.stderr.decode("utf-8", "replace")
                self.assertEqual(r.returncode, 0, err[-1500:])
                self.assertNotIn("UnicodeEncodeError", err)
                self.assertTrue(r.stdout)
                r.stdout.decode("utf-8")  # đã đổi sang UTF-8

    def test_negative_control_build_index_without_setup_stdio(self):
        p = self.tmp / "scripts" / "build_index.py"
        src = p.read_text(encoding="utf-8")
        self.assertIn("    setup_stdio()\n", src)
        bad = self.tmp / "scripts" / "build_index_bad.py"
        bad.write_text(src.replace("    setup_stdio()\n", ""), encoding="utf-8")
        r = self.run_cp1252(["build_index_bad.py"])
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("UnicodeEncodeError", r.stderr.decode("utf-8", "replace"))


class BuildIndexTests(unittest.TestCase):
    """T14: build_index không ghi lại khi danh sách không đổi."""

    def test_second_run_keeps_bytes(self):
        tmp = copy_repo("ntt-idx-")
        try:
            run = lambda: subprocess.run([sys.executable, str(tmp / "scripts" / "build_index.py")], cwd=tmp,  # noqa: E731
                                         capture_output=True, env=dict(os.environ, PYTHONIOENCODING="utf-8"))
            (tmp / "data" / "index.json").unlink()
            self.assertEqual(run().returncode, 0)
            first = (tmp / "data" / "index.json").read_bytes()
            self.assertEqual(run().returncode, 0)
            self.assertEqual((tmp / "data" / "index.json").read_bytes(), first)
            # đối chứng: thêm bản tin thì index đổi
            shutil.copy(tmp / "data" / "daily" / "2026-11-06-chieu.json", tmp / "data" / "daily" / "2099-01-01-sang.json")
            self.assertEqual(run().returncode, 0)
            idx = json.loads((tmp / "data" / "index.json").read_text(encoding="utf-8"))
            self.assertIn("2099-01-01-sang", idx["daily"])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class CollectAgentTests(unittest.TestCase):
    """T13: collect.py --dry-run --agent."""

    def run_collect(self, *args):
        return subprocess.run([sys.executable, str(ROOT / "scripts" / "collect.py"), *args], cwd=ROOT,
                              capture_output=True, env=dict(os.environ, PYTHONIOENCODING="utf-8"))

    def test_agent_prompt(self):
        r = self.run_collect("--dry-run", "--agent", "--date", "2026-11-06", "--session", "chieu")
        self.assertEqual(r.returncode, 0)
        out = r.stdout.decode("utf-8")
        for s in ("data/daily/2026-11-06-chieu.json", "data/weekly/2026-W45.json", "anomalies",
                  "## Cách ghi kết quả (chế độ agent)", '"mode": "rss"', "job publish sẽ tự chạy `scripts/validate.py`",
                  "Không có Bash", "CHỈ trong `data/daily/` và `data/weekly/`"):
            self.assertIn(s, out)
        self.assertNotIn("<daily>", out)
        self.assertNotIn("## Định dạng trả lời", out)

    def test_plain_dry_run_unchanged(self):
        r = self.run_collect("--dry-run", "--date", "2026-11-06", "--session", "chieu")
        self.assertEqual(r.returncode, 0)
        self.assertIn("<daily>", r.stdout.decode("utf-8"))

    def test_agent_requires_dry_run(self):
        self.assertEqual(self.run_collect("--agent", "--date", "2026-11-06", "--session", "chieu").returncode, 2)


try:
    import yaml  # chỉ dùng trong test
except ImportError:  # pragma: no cover
    yaml = None


@unittest.skipUnless(yaml, "không có PyYAML")
class WorkflowTests(unittest.TestCase):
    """Kiểm tra tĩnh .github/workflows/bantin.yml bằng PyYAML (khóa `on:` được đọc thành True).
    Vòng sửa 1: job draft (Claude, chỉ đọc) tách khỏi job publish (ghi repo, không chạy Claude)."""

    @classmethod
    def setUpClass(cls):
        cls.text = (ROOT / ".github" / "workflows" / "bantin.yml").read_text(encoding="utf-8")
        cls.wf = yaml.safe_load(cls.text)
        cls.jobs = cls.wf["jobs"]
        cls.draft = cls.jobs["draft"]["steps"]
        cls.publish = cls.jobs["publish"]["steps"]

    def find(self, steps, pred, what):
        for i, s in enumerate(steps):
            if pred(s):
                return i, s
        self.fail(f"không thấy bước: {what}")

    def by_run(self, steps, needle):
        if needle == "scripts/validate.py":  # đúng bước "Kiểm tra dữ liệu"
            return self.find(steps, lambda s: (s.get("run") or "").strip() == "python scripts/validate.py", needle)
        return self.find(steps, lambda s: needle in (s.get("run") or ""), needle)

    def by_uses(self, steps, uses, name):
        return self.find(steps, lambda s: str(s.get("uses", "")).startswith(uses) and s.get("with", {}).get("name") == name,
                         f"{uses} {name}")

    def claude_flags(self):
        run = self.by_run(self.draft, "claude -p")[1]["run"]
        flags = dict(re.findall(r'(--[A-Za-z-]+) "([^"]*)"', run))
        return run, flags

    def test_triggers(self):
        on = self.wf.get(True, self.wf.get("on"))
        self.assertEqual([c["cron"] for c in on["schedule"]], ["45 23 * * *", "45 9 * * *"])
        self.assertEqual(set(on["workflow_dispatch"]["inputs"]), {"session", "force"})
        self.assertEqual(on["push"]["paths"], ["web/**", "data/**"])
        self.assertEqual(self.text.splitlines()[1], "# Không có secret thì workflow vẫn soạn bản tự động (RSS) và đăng trang.")

    def test_no_api_key_no_tabs_concurrency(self):
        self.assertNotIn("ANTHROPIC_API_KEY", self.text)
        self.assertNotIn("\t", self.text)
        self.assertEqual(self.wf["concurrency"]["group"], "bantin")
        self.assertIs(self.wf["concurrency"]["cancel-in-progress"], False)

    def test_job_graph(self):
        self.assertEqual(set(self.jobs), {"draft", "publish", "deploy"})
        self.assertEqual(self.jobs["draft"]["if"], "github.event_name != 'push'")
        self.assertEqual(self.jobs["publish"]["needs"], "draft")
        dep = self.jobs["deploy"]
        self.assertEqual(dep["needs"], "publish")
        self.assertEqual(dep["if"], "always() && (needs.publish.result == 'success' || needs.publish.result == 'skipped')")
        self.assertEqual(dep["permissions"]["pages"], "write")
        self.assertEqual(dep["permissions"]["id-token"], "write")
        co = next(s for s in dep["steps"] if str(s.get("uses", "")).startswith("actions/checkout"))
        self.assertEqual(co["with"]["ref"], "main")

    def test_permissions_split(self):
        self.assertEqual(self.wf["permissions"], {"contents": "read"})
        self.assertEqual(self.jobs["draft"]["permissions"], {"contents": "read"})
        self.assertEqual(self.jobs["publish"]["permissions"], {"contents": "write"})
        draft_text = yaml.safe_dump(self.jobs["draft"], allow_unicode=True)
        self.assertNotIn("GH_TOKEN", draft_text)
        self.assertNotIn("github.token", draft_text)
        for s in self.publish:  # job có quyền ghi không chạy claude và không có token Pro
            self.assertNotIn("claude ", s.get("run") or "", s.get("name"))
            self.assertNotIn("CLAUDE_CODE_OAUTH_TOKEN", yaml.safe_dump(s, allow_unicode=True), s.get("name"))
        # token Pro chỉ nằm trong env của đúng bước Claude
        holders = [s.get("name") for s in self.draft if "secrets.CLAUDE_CODE_OAUTH_TOKEN }}" in yaml.safe_dump(s.get("env") or {})]
        self.assertEqual(holders, ["Tầng 2 – Claude Code (gói Pro)"])
        for job in ("draft", "publish"):
            co = next(s for s in self.jobs[job]["steps"] if str(s.get("uses", "")).startswith("actions/checkout"))
            self.assertIs(co["with"]["persist-credentials"], False, job)

    def test_draft_order_rss_artifact_before_claude(self):
        i_plan = self.by_run(self.draft, "ci_guard.py plan")[0]
        i_rss = self.by_run(self.draft, "rss_collect.py")[0]
        i_up_rss, up_rss = self.by_uses(self.draft, "actions/upload-artifact", "rss")
        i_cli = self.by_run(self.draft, "npm install -g @anthropic-ai/claude-code")[0]
        i_claude = self.by_run(self.draft, "claude -p")[0]
        i_up_ai, up_ai = self.by_uses(self.draft, "actions/upload-artifact", "ai")
        self.assertEqual([i_plan, i_rss, i_up_rss, i_cli, i_claude, i_up_ai],
                         sorted([i_plan, i_rss, i_up_rss, i_cli, i_claude, i_up_ai]))
        self.assertEqual(up_rss["with"]["path"], "data/daily/${{ steps.plan.outputs.key }}.json")
        paths = [p.strip() for p in up_ai["with"]["path"].splitlines() if p.strip()]
        self.assertEqual(paths, ["data/daily/${{ steps.plan.outputs.key }}.json",
                                 "data/weekly/${{ steps.plan.outputs.week || 'khong-co-tuan' }}.json"])
        self.assertEqual(set(self.jobs["draft"]["outputs"]), {"date", "session", "key", "week", "skip", "claude"})

    def test_publish_order(self):
        i_dl_rss = self.by_uses(self.publish, "actions/download-artifact", "rss")[0]
        i_imp_rss = self.by_run(self.publish, "--only-over-rss")[0]
        i_snap = self.by_run(self.publish, "ci_guard.py snapshot")[0]
        i_dl_ai = self.by_uses(self.publish, "actions/download-artifact", "ai")[0]
        i_check = self.by_run(self.publish, "ci_guard.py check")[0]
        i_val = self.by_run(self.publish, "scripts/validate.py")[0]
        i_idx = self.find(self.publish, lambda s: (s.get("run") or "").strip() == "python scripts/build_index.py", "index")[0]
        i_commit = self.by_run(self.publish, "git commit")[0]
        order = [i_dl_rss, i_imp_rss, i_snap, i_dl_ai, i_check, i_val, i_idx, i_commit]
        self.assertEqual(order, sorted(order))
        run = self.publish[i_check]["run"]
        self.assertLess(run.index("ci_guard.py import"), run.index("ci_guard.py check"))
        self.assertIn("git clean -fdqx -e /data -- .", run)
        self.assertLess(run.index("git checkout -- . ':(exclude)data'"), run.index("python"))

    def test_claude_step(self):
        i, s = self.by_run(self.draft, "claude -p")
        self.assertIs(s.get("continue-on-error"), True)
        self.assertIsInstance(s.get("timeout-minutes"), int)
        run, flags = self.claude_flags()
        self.assertIn("timeout ", run)
        self.assertIn("--dry-run --agent", run)
        self.assertIn("--restricted", run)
        self.assertIn("--strict-mcp-config", run)  # R2: không nạp MCP server nào
        self.assertNotIn("dangerously", run)
        self.assertNotIn("bypassPermissions", run)
        self.assertEqual(flags["--tools"], "WebSearch,WebFetch,Read,Write,Edit")
        self.assertEqual(re.search(r"--permission-mode (\w+)", run).group(1), "dontAsk")
        allowed = flags["--allowedTools"].replace("${FETCH}", "").split(",")
        self.assertEqual([a for a in allowed if "(" not in a], ["WebSearch"], "không có Read/Write/Edit/Bash trần")
        self.assertFalse([a for a in allowed if a.startswith(("Bash", "Write("))])
        self.assertEqual(sorted(a for a in allowed if a.startswith("Edit(")), ["Edit(./data/daily/**)", "Edit(./data/weekly/**)"])
        self.assertIn("Read(./**)", allowed)
        denied = flags["--disallowedTools"].split(",")
        for d in ("Bash", "Read(//proc/**)", "Read(//sys/**)", "Read(//etc/**)", "Read(~/**)", "Read(//home/runner/work/_temp/**)",
                  "Read(./.git/**)", "Edit(./scripts/**)", "Edit(./.git/**)", "Edit(./.github/**)"):
            self.assertIn(d, denied)
        self.assertIn("WebFetch(domain:$d)", run)
        self.assertIn("cafef.vn", s["env"]["FETCH_DOMAINS"])
        self.assertEqual(s["env"]["CLAUDE_CODE_OAUTH_TOKEN"], "${{ secrets.CLAUDE_CODE_OAUTH_TOKEN }}")
        # CLI ghim phiên bản đã đối chiếu cú pháp cờ
        self.assertIn("@anthropic-ai/claude-code@2.1.319", self.by_run(self.draft, "npm install")[1]["run"])

    def test_cleanup_step_before_ai_upload(self):
        # R1: bước RIÊNG dọn symlink/đích lạ, sau Tầng 2 và trước "Gửi bản AI"
        i_claude = self.by_run(self.draft, "claude -p")[0]
        i_clean, st = self.by_run(self.draft, "find data -type l -delete")
        i_up = self.by_uses(self.draft, "actions/upload-artifact", "ai")[0]
        self.assertTrue(i_claude < i_clean < i_up)
        self.assertEqual(i_clean, i_claude + 1)
        self.assertEqual(i_up, i_clean + 1)
        self.assertEqual(st.get("if"), "steps.plan.outputs.claude == 'true'")
        self.assertNotIn("continue-on-error", st)
        self.assertNotIn("claude -p", st["run"])
        self.assertIn('if [ -e "$f" ] && [ ! -f "$f" ]; then rm -rf -- "$f"; fi', st["run"])
        self.assertEqual(st["env"], {"K": "${{ steps.plan.outputs.key }}", "W": "${{ steps.plan.outputs.week }}"})
        self.assertIn("exit 1", self.draft[i_claude]["run"])  # vì vậy dọn không được nằm trong Tầng 2

    def test_guards(self):
        cond = "steps.plan.outputs.claude == 'true'"
        for needle in ("claude -p", "npm install -g @anthropic-ai/claude-code"):
            self.assertEqual(self.by_run(self.draft, needle)[1].get("if"), cond, needle)
        self.assertEqual(self.by_uses(self.draft, "actions/upload-artifact", "ai")[1].get("if"), cond)
        _, rss = self.by_run(self.draft, "rss_collect.py")
        self.assertIs(rss.get("continue-on-error"), True)
        self.assertEqual(rss.get("if"), "steps.plan.outputs.skip != 'true'")
        pcond = "needs.draft.outputs.claude == 'true'"
        for needle in ("ci_guard.py snapshot", "ci_guard.py check"):
            self.assertEqual(self.by_run(self.publish, needle)[1].get("if"), pcond, needle)
        self.assertEqual(self.by_uses(self.publish, "actions/download-artifact", "ai")[1].get("if"), pcond)
        for needle in ("scripts/validate.py", "git commit"):
            self.assertIsNone(self.by_run(self.publish, needle)[1].get("if"), needle)

    def test_no_expressions_in_run(self):
        for job in self.jobs.values():
            for s in job["steps"]:
                self.assertNotIn("${{", s.get("run") or "", s.get("name"))


class DataTests(unittest.TestCase):
    """TC1: validate.py trả 0. (Việc data/ docs/ scripts/ không đổi do Tester kiểm bằng git status khi nghiệm thu.)"""

    def test_validate_returns_0(self):
        r = subprocess.run([sys.executable, str(ROOT / "scripts" / "validate.py")], cwd=ROOT, capture_output=True)
        self.assertEqual(r.returncode, 0, r.stdout.decode("utf-8", "replace")[-2000:])

    def run_validate(self, *files):
        return subprocess.run([sys.executable, str(ROOT / "scripts" / "validate.py"), *map(str, files)], cwd=ROOT,
                              capture_output=True, env=dict(os.environ, PYTHONIOENCODING="utf-8"))

    def test_bad_files_are_errors_not_tracebacks(self):
        # N1 (vòng sửa 2): file lồng sâu và file "[]" → mã 1, có dòng ERROR, không có Traceback; file tốt vẫn được kiểm
        tmp = Path(tempfile.mkdtemp(prefix="ntt-val-"))
        try:
            (tmp / "daily").mkdir()
            deep = tmp / "daily" / "2026-11-06-chieu.json"
            deep.write_text("[" * 200000 + "]" * 200000, encoding="utf-8")
            arr = tmp / "daily" / "2026-11-05-chieu.json"
            arr.write_text("[]", encoding="utf-8")
            good = ROOT / "data" / "daily" / "2026-11-06-chieu.json"
            for bad, err_name in ((deep, "RecursionError"), (arr, "AttributeError")):
                with self.subTest(file=bad.name):
                    r = self.run_validate(bad, good)
                    out, err = r.stdout.decode("utf-8"), r.stderr.decode("utf-8", "replace")
                    self.assertEqual(r.returncode, 1)
                    self.assertIn(f"ERROR không đọc được: {err_name}", out)
                    self.assertNotIn("Traceback", err)
                    self.assertIn("[OK ] 2026-11-06-chieu.json", out)  # file tốt phía sau vẫn được kiểm
            # đối chứng: chỉ file tốt thì mã 0
            self.assertEqual(self.run_validate(good).returncode, 0)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


def load_daily(keys, data=ROOT / "data"):
    return [json.loads((data / "daily" / f"{k}.json").read_text(encoding="utf-8")) for k in keys]


def expected_keys(limit, data=ROOT / "data"):
    """N1: tự tính độc lập danh sách bản trang tải: sắp (date, chiều=2/sáng=1) giảm dần, lấy `limit` đầu.
    Trả (keys, analysis_keys) – analysis_keys là các key có mode != "rss"."""
    idx = json.loads((data / "index.json").read_text(encoding="utf-8"))
    keys = sorted(idx.get("daily") or [], key=lambda k: (k[:10], 2 if k.endswith("-chieu") else 1), reverse=True)[:limit]
    ana = [k for k, d in zip(keys, load_daily(keys, data)) if d.get("mode") != "rss"]
    return keys, ana


_LIMIT = None


def edition_limit():
    """N.EDITION_LIMIT của sản phẩm, đọc qua ntt_load.js."""
    global _LIMIT
    if _LIMIT is None:
        r = subprocess.run([NODE, str(ROOT / "tests" / "ntt_load.js")], capture_output=True)
        _LIMIT = json.loads(r.stdout.decode("utf-8"))["limit"]
    return _LIMIT


def expected_edition_count(data=ROOT / "data"):
    """Số bản tin tab Phân tích dùng: tập trang tải (giới hạn N.EDITION_LIMIT) bỏ các bản mode rss."""
    return len(expected_keys(edition_limit(), data)[1])


@unittest.skipUnless(NODE, "không có node")
class AnalyticsCrossCheck(unittest.TestCase):
    """TC10/N1: so số liệu JS với cách đếm độc lập bằng Python, trên tập phân tích."""

    @classmethod
    def setUpClass(cls):
        r = subprocess.run([NODE, str(ROOT / "tests" / "ntt_load.js")], capture_output=True)
        if r.returncode:
            raise RuntimeError(r.stderr.decode("utf-8", "replace"))
        cls.js = json.loads(r.stdout.decode("utf-8"))
        cls.keys, cls.ana = expected_keys(cls.js["limit"])
        cls.daily = load_daily(cls.ana)

    def test_same_edition_set_and_limit(self):
        self.assertEqual(self.js["limit"], 40)
        self.assertEqual(self.js["keys"], self.keys)
        self.assertEqual(self.js["analysisKeys"], self.ana)
        self.assertEqual(self.js["editions"], len(self.ana))

    def test_expected_keys_negative_control(self):
        # Đối chứng: sắp theo chuỗi (sort().reverse()) cho 06-sang trước 06-chieu – khác cách sắp đúng
        ks = ["2026-11-06-sang", "2026-11-06-chieu"]
        self.assertEqual(sorted(ks, reverse=True), ["2026-11-06-sang", "2026-11-06-chieu"])
        self.assertEqual(sorted(ks, key=lambda k: (k[:10], 2 if k.endswith("-chieu") else 1), reverse=True),
                         ["2026-11-06-chieu", "2026-11-06-sang"])

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


def rss_edition_after_newest(data=ROOT / "data"):
    """Bản rss mới hơn bản mới nhất hiện có, sinh bằng rss_collect.build_edition từ fixture thật."""
    sys.path.insert(0, str(ROOT / "scripts"))
    import rss_collect as R
    from datetime import date as _date, datetime as _dt, timedelta as _td
    idx = json.loads((data / "index.json").read_text(encoding="utf-8"))
    newest = max(_date.fromisoformat(k[:10]) for k in idx["daily"])
    d = newest + _td(days=1)
    now = _dt(d.year, d.month, d.day, 11, 0, tzinfo=R.VN_TZ)
    items = []
    for f in sorted((ROOT / "tests" / "fixtures" / "rss").glob("*.xml")):
        for i, it in enumerate(R.parse_feed(f.read_bytes(), f.stem)):
            it["published"] = now - _td(minutes=5 + i)
            it["hint"] = None
            items.append(it)
    cfg = json.loads((ROOT / "scripts" / "rss_config.json").read_text(encoding="utf-8"))
    return R.build_edition(items, cfg, d, "sang", now)


class StaticSite:
    def __init__(self, rss_only=False):
        self.dir = Path(tempfile.mkdtemp(prefix="ntt-site-"))
        shutil.copytree(ROOT / "web", self.dir, dirs_exist_ok=True)
        data = self.dir / "data"
        data.mkdir()
        ed = rss_edition_after_newest()
        if rss_only:
            (data / "daily").mkdir()
            (data / "weekly").mkdir()
            daily = [ed["key"]]
        else:
            for d in ("daily", "weekly"):
                shutil.copytree(ROOT / "data" / d, data / d)
            daily = json.loads((ROOT / "data" / "index.json").read_text(encoding="utf-8"))["daily"] + [ed["key"]]
        (data / "daily" / f"{ed['key']}.json").write_text(json.dumps(ed, ensure_ascii=False), encoding="utf-8")
        weekly = sorted(p.stem for p in (data / "weekly").glob("*.json"))
        (data / "index.json").write_text(json.dumps({"daily": sorted(daily), "weekly": weekly}), encoding="utf-8")
        self.data, self.key = data, ed["key"]
        site = self.dir

        class Static(SimpleHTTPRequestHandler):
            def __init__(self, *a, **k):
                super().__init__(*a, directory=str(site), **k)

            def log_message(self, *a):
                pass
        self.handler = Static

    def dump(self, width=1200):
        srv = start(self.handler)
        try:
            return dump_dom(f"http://127.0.0.1:{srv.server_address[1]}/", width=width)
        finally:
            srv.shutdown(); srv.server_close()

    def close(self):
        shutil.rmtree(self.dir, ignore_errors=True)


@unittest.skipUnless(BROWSER and NODE, "không có Chrome/Edge headless hoặc node")
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


    def test_rss_newest_edition(self):
        site = StaticSite()
        try:
            dom, err = site.dump()
            n = expected_edition_count(site.data)
            n_all = len(expected_keys(edition_limit(), site.data)[0])
        finally:
            site.close()
        self.assertEqual(n_all - n, 1)
        self.assertIn("Bản tự động – chưa có nhận định AI", dom)
        self.assertRegex(dom, r'<span class="stamp mode-rss" id="mode-note">')
        self.assertRegex(dom, r'id="keybar-wrap"[^>]*hidden|hidden[^>]*id="keybar-wrap"')
        self.assertNotIn('class="metrics"', dom)
        self.assertIn("Tóm tắt tự động (RSS)", dom)
        self.assertIn(" · tự động</option>", dom)
        self.assertIn("Bỏ qua 1 bản tự động (RSS) chưa có số liệu.", dom)
        self.assertIn(f"Tổng hợp từ {n} bản tin", dom)
        self.assertEqual(console_errors(err), [])

    def test_rss_only_site(self):
        site = StaticSite(rss_only=True)
        try:
            dom, err = site.dump(width=375)
        finally:
            site.close()
        self.assertIn("Chưa có dữ liệu phân tích", dom)
        self.assertNotIn("Tổng hợp từ", dom)
        self.assertIn("Bản tự động – chưa có nhận định AI", dom)
        self.assertEqual(console_errors(err), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
