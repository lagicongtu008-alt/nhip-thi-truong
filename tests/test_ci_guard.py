"""Test cho scripts/ci_guard.py và mô phỏng các bước của .github/workflows/bantin.yml.

Chạy:  python -m unittest discover -s tests -v
Phần mô phỏng shell cần bash, git, timeout và PyYAML; thiếu thì tự bỏ qua.
Mô phỏng chạy job draft và job publish trên hai "runner" riêng, file chỉ đi qua thư mục artifact.
"""
from __future__ import annotations

import json
import os
import re
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
from common import VN_TZ, daily_key, label_for, now_vn, read_json, session_for, write_json  # noqa: E402

GUARD = ROOT / "scripts" / "ci_guard.py"
FIX = ROOT / "tests" / "fixtures" / "rss"
AI_SRC = ROOT / "data" / "daily" / "2026-11-06-chieu.json"
WK_SRC = ROOT / "data" / "weekly" / "2026-W45.json"
K, W = "2026-11-06-chieu", "2026-W45"

try:
    import yaml  # chỉ dùng trong test
except ImportError:  # pragma: no cover
    yaml = None


def guard(*args, data: Path):
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    r = subprocess.run([sys.executable, str(GUARD), "--data", str(data), *map(str, args)],
                       capture_output=True, env=env)
    return r.returncode, r.stdout.decode("utf-8", "replace"), r.stderr.decode("utf-8", "replace")


def rss_edition(d: date, session: str) -> dict:
    items = []
    for n in ("cafef", "vietstock", "vneconomy", "thanhnien", "vnexpress"):
        for it in R.parse_feed((FIX / f"{n}.xml").read_bytes(), n):
            it["hint"] = None
            items.append(it)
    # dời ngày đăng của fixture về đúng ngày d để luôn có bài trong cửa sổ
    for it in items:
        it["published"] = datetime(d.year, d.month, d.day, 10, 30, tzinfo=VN_TZ) - timedelta(minutes=len(items))
    now = datetime(d.year, d.month, d.day, 11, 59, tzinfo=VN_TZ)
    return R.build_edition(items, R.read_json(ROOT / "scripts" / "rss_config.json"), d, session, now)


def ai_edition(d: date, session: str) -> dict:
    ed = read_json(AI_SRC)
    ed.update(key=daily_key(d, session), date=d.isoformat(), session=session, label=label_for(d, session))
    ed.pop("mode", None)
    return ed


class PlanTests(unittest.TestCase):
    def setUp(self):
        self.data = Path(tempfile.mkdtemp(prefix="ntt-plan-"))
        self.addCleanup(shutil.rmtree, self.data, True)
        (self.data / "daily").mkdir()

    def plan(self, *args):
        out = self.data / "gh_output.txt"
        if out.exists():
            out.unlink()
        rc, so, se = guard("plan", *args, "--github-output", out, data=self.data)
        self.assertEqual(rc, 0, se)
        kv = dict(ln.split("=", 1) for ln in out.read_text(encoding="utf-8").splitlines() if ln)
        return kv, so

    def test_friday_afternoon_has_week(self):
        kv, _ = self.plan("--date", "2026-11-06", "--session", "chieu", "--has-token", "true")
        self.assertEqual((kv["key"], kv["week"], kv["skip"], kv["claude"]), (K, W, "false", "true"))

    def test_saturday_afternoon_skip(self):
        kv, _ = self.plan("--date", "2026-11-07", "--session", "chieu", "--has-token", "true", "--force", "true")
        self.assertEqual((kv["skip"], kv["claude"], kv["week"]), ("true", "false", ""))

    def test_saturday_morning_no_week(self):
        kv, _ = self.plan("--date", "2026-11-07", "--session", "sang", "--has-token", "true")
        self.assertEqual((kv["key"], kv["week"], kv["skip"], kv["claude"]), ("2026-11-07-sang", "", "false", "true"))

    def test_token_force_matrix_with_ai_file(self):
        write_json(self.data / "daily" / f"{K}.json", ai_edition(date(2026, 11, 6), "chieu"))
        exp = {("true", "true"): "true", ("true", "false"): "false", ("false", "true"): "false", ("false", "false"): "false"}
        for (tok, force), claude in exp.items():
            with self.subTest(token=tok, force=force):
                kv, _ = self.plan("--date", "2026-11-06", "--session", "chieu", "--has-token", tok, "--force", force)
                self.assertEqual((kv["ai_exists"], kv["claude"]), ("true", claude))
        # đối chứng: file là bản rss thì ai_exists=false, có token là chạy Claude
        write_json(self.data / "daily" / f"{K}.json", rss_edition(date(2026, 11, 6), "chieu"))
        kv, _ = self.plan("--date", "2026-11-06", "--session", "chieu", "--has-token", "true", "--force", "")
        self.assertEqual((kv["ai_exists"], kv["claude"]), ("false", "true"))

    def test_no_token_warning(self):
        kv, so = self.plan("--date", "2026-11-06", "--session", "chieu", "--has-token", "false")
        self.assertEqual(kv["claude"], "false")
        self.assertIn("::warning::", so)
        self.assertIn("CLAUDE_CODE_OAUTH_TOKEN", so)
        _, so = self.plan("--date", "2026-11-06", "--session", "chieu", "--has-token", "true")
        self.assertNotIn("::warning::", so)

    def test_stdout_mode_without_github_output(self):
        rc, so, _ = guard("plan", "--date", "2026-11-06", "--session", "", "--has-token", "true", data=self.data)
        self.assertEqual(rc, 0)
        self.assertRegex(so.replace("\r", ""), r"(?m)^key=2026-11-06-(sang|chieu)$")


class CheckTests(unittest.TestCase):
    """Mỗi ca một thư mục data tạm: daily (bản cũ + bản RSS của K), weekly W có anomalies."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="ntt-check-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.data, self.snap = self.tmp / "data", self.tmp / "snap"
        (self.data / "daily").mkdir(parents=True)
        (self.data / "weekly").mkdir()
        shutil.copy(ROOT / "data" / "daily" / "2026-11-05-chieu.json", self.data / "daily")
        shutil.copy(WK_SRC, self.data / "weekly")
        write_json(self.data / "daily" / f"{K}.json", rss_edition(date(2026, 11, 6), "chieu"))
        self.rss_bytes = (self.data / "daily" / f"{K}.json").read_bytes()
        self.old_bytes = (self.data / "daily" / "2026-11-05-chieu.json").read_bytes()
        self.wk_bytes = (self.data / "weekly" / f"{W}.json").read_bytes()

    def snapshot(self):
        rc, _, se = guard("snapshot", "--dir", self.snap, data=self.data)
        self.assertEqual(rc, 0, se)

    def check(self, week=W):
        rc, so, se = guard("check", "--dir", self.snap, "--key", K, "--week", week, data=self.data)
        self.assertEqual(rc, 0, se)
        return so

    @property
    def daily(self):
        return self.data / "daily" / f"{K}.json"

    def test_a_valid_ai_kept(self):
        self.snapshot()
        write_json(self.daily, ai_edition(date(2026, 11, 6), "chieu"))
        ai = self.daily.read_bytes()
        so = self.check()
        self.assertEqual(self.daily.read_bytes(), ai)
        self.assertIn("giữ bản AI", so)

    def test_b_four_sectors_restored(self):
        self.snapshot()
        ed = ai_edition(date(2026, 11, 6), "chieu")
        ed["sectors"] = ed["sectors"][:4]
        write_json(self.daily, ed)
        so = self.check()
        self.assertEqual(self.daily.read_bytes(), self.rss_bytes)
        self.assertIn("::warning::Bản Claude không hợp lệ", so)

    def test_c_broken_json_restored(self):
        self.snapshot()
        self.daily.write_text("{không phải json", encoding="utf-8")
        self.check()
        self.assertEqual(self.daily.read_bytes(), self.rss_bytes)

    def test_d_deleted_restored(self):
        self.snapshot()
        self.daily.unlink()
        so = self.check()
        self.assertEqual(self.daily.read_bytes(), self.rss_bytes)
        self.assertIn("file bị xóa", so)

    def test_e_still_rss_changed_restored(self):
        self.snapshot()
        ed = read_json(self.daily)
        ed["updatedAt"] = "2026-11-06T18:00:00+07:00"
        write_json(self.daily, ed)
        so = self.check()
        self.assertEqual(self.daily.read_bytes(), self.rss_bytes)
        self.assertIn("mode rss", so)

    def test_e2_wrong_key_restored(self):
        self.snapshot()
        write_json(self.daily, ai_edition(date(2026, 11, 5), "chieu") | {"key": K})
        self.check()
        self.assertEqual(self.daily.read_bytes(), self.rss_bytes)

    def test_f_other_files_reverted(self):
        self.snapshot()
        write_json(self.daily, ai_edition(date(2026, 11, 6), "chieu"))
        (self.data / "daily" / "2026-11-05-chieu.json").write_text("{}", encoding="utf-8")
        (self.data / "daily" / "la.json").write_text("{}", encoding="utf-8")
        (self.data / "daily" / "thu-muc-la").mkdir()
        (self.data / "daily" / "thu-muc-la" / "x.txt").write_text("x", encoding="utf-8")
        (self.data / "index.json").write_text("{}", encoding="utf-8")
        self.check(week="")  # không có W: weekly cũng là "file khác"
        self.assertEqual((self.data / "daily" / "2026-11-05-chieu.json").read_bytes(), self.old_bytes)
        self.assertFalse((self.data / "daily" / "la.json").exists())
        self.assertFalse((self.data / "daily" / "thu-muc-la").exists())
        self.assertFalse((self.data / "index.json").exists())
        self.assertNotIn("mode", read_json(self.daily))  # bản AI hợp lệ vẫn giữ

    def test_f2_weekly_changed_without_week_reverted(self):
        self.snapshot()
        (self.data / "weekly" / f"{W}.json").write_text("{}", encoding="utf-8")
        self.check(week="")
        self.assertEqual((self.data / "weekly" / f"{W}.json").read_bytes(), self.wk_bytes)

    def test_g_weekly_lost_anomaly_restored_daily_kept(self):
        self.snapshot()
        write_json(self.daily, ai_edition(date(2026, 11, 6), "chieu"))
        ai = self.daily.read_bytes()
        wk = read_json(self.data / "weekly" / f"{W}.json")
        wk["anomalies"] = wk["anomalies"][1:]
        write_json(self.data / "weekly" / f"{W}.json", wk)
        so = self.check()
        self.assertEqual((self.data / "weekly" / f"{W}.json").read_bytes(), self.wk_bytes)
        self.assertEqual(self.daily.read_bytes(), ai)
        self.assertIn("mất 1 anomaly cũ", so)

    def test_g2_weekly_keeps_all_old_anomalies_kept(self):
        # đối chứng cho (g): weekly đổi nhưng còn đủ anomaly cũ (kể cả khi đổi thứ tự) → giữ
        self.snapshot()
        wk = read_json(self.data / "weekly" / f"{W}.json")
        wk["anomalies"] = list(reversed(wk["anomalies"]))
        wk["updatedAt"] = "2026-11-06T18:00:00+07:00"
        write_json(self.data / "weekly" / f"{W}.json", wk)
        new = (self.data / "weekly" / f"{W}.json").read_bytes()
        so = self.check()
        self.assertEqual((self.data / "weekly" / f"{W}.json").read_bytes(), new)
        self.assertIn("weekly/2026-W45.json: giữ bản AI", so)

    def test_h_no_snapshot_and_broken_deleted(self):
        self.daily.unlink()
        self.snapshot()
        self.daily.write_text("{hỏng", encoding="utf-8")
        self.check()
        self.assertFalse(self.daily.exists())

    def test_h2_no_snapshot_and_valid_kept(self):
        self.daily.unlink()
        self.snapshot()
        write_json(self.daily, ai_edition(date(2026, 11, 6), "chieu"))
        self.check()
        self.assertTrue(self.daily.exists())

    def test_j_deeply_nested_daily_restored(self):
        self.snapshot()
        self.daily.write_text("[" * 200000 + "]" * 200000, encoding="utf-8")
        so = self.check()
        self.assertEqual(self.daily.read_bytes(), self.rss_bytes)
        self.assertIn("không đọc được JSON", so)
        # đối chứng: json thường đúng là không đọc nổi file này
        with self.assertRaises(RecursionError):
            json.loads("[" * 200000 + "]" * 200000)

    def test_j_deeply_nested_weekly_restored(self):
        self.snapshot()
        (self.data / "weekly" / f"{W}.json").write_text("[" * 200000 + "]" * 200000, encoding="utf-8")
        so = self.check()
        self.assertEqual((self.data / "weekly" / f"{W}.json").read_bytes(), self.wk_bytes)
        self.assertIn("không đọc được JSON", so)

    def test_j_json_array_daily_restored(self):
        self.snapshot()
        self.daily.write_text("[]", encoding="utf-8")
        self.check()
        self.assertEqual(self.daily.read_bytes(), self.rss_bytes)

    def test_i_unchanged_warns_and_keeps(self):
        self.snapshot()
        so = self.check()
        self.assertEqual(self.daily.read_bytes(), self.rss_bytes)
        self.assertIn("::warning::Claude không đổi bản tin", so)


# ---------------------------------------------------------------- import (nhận artifact)
class ImportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="ntt-imp-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.data, self.src = self.tmp / "data", self.tmp / "art"
        (self.data / "daily").mkdir(parents=True)
        (self.data / "weekly").mkdir()
        self.src.mkdir()

    def imp(self, *extra):
        rc, so, se = guard("import", "--src", self.src, "--key", K, *extra, data=self.data)
        self.assertEqual(rc, 0, se)
        return so

    def test_only_two_target_files_are_copied(self):
        (self.src / "daily").mkdir()
        (self.src / "weekly").mkdir()
        (self.src / "daily" / f"{K}.json").write_text('{"a": 1}', encoding="utf-8")
        (self.src / "weekly" / f"{W}.json").write_text('{"w": 1}', encoding="utf-8")
        (self.src / "daily" / "la.json").write_text("{}", encoding="utf-8")
        (self.src / "evil.sh").write_text("echo x", encoding="utf-8")
        self.imp("--week", W)
        self.assertEqual((self.data / "daily" / f"{K}.json").read_text(encoding="utf-8"), '{"a": 1}')
        self.assertEqual((self.data / "weekly" / f"{W}.json").read_text(encoding="utf-8"), '{"w": 1}')
        self.assertEqual(sorted(p.name for p in self.data.rglob("*") if p.is_file()), sorted([f"{K}.json", f"{W}.json"]))

    def test_flat_layout_and_missing_src(self):
        (self.src / f"{K}.json").write_text('{"b": 2}', encoding="utf-8")  # artifact 1 file: nằm ở gốc
        self.imp("--week", W)
        self.assertEqual(read_json(self.data / "daily" / f"{K}.json"), {"b": 2})
        self.assertFalse((self.data / "weekly" / f"{W}.json").exists())
        shutil.rmtree(self.src)
        self.assertIn("không có", self.imp())

    def test_only_over_rss_keeps_ai(self):
        (self.src / f"{K}.json").write_text('{"mode": "rss", "x": 1}', encoding="utf-8")
        write_json(self.data / "daily" / f"{K}.json", {"ai": True})
        self.assertIn("đã có bản AI", self.imp("--only-over-rss"))
        self.assertEqual(read_json(self.data / "daily" / f"{K}.json"), {"ai": True})
        write_json(self.data / "daily" / f"{K}.json", {"mode": "rss"})
        self.imp("--only-over-rss")
        self.assertEqual(read_json(self.data / "daily" / f"{K}.json"), {"mode": "rss", "x": 1})

    def test_bad_key_rejected(self):
        rc, _, _ = guard("import", "--src", self.src, "--key", "../../x", data=self.data)
        self.assertNotEqual(rc, 0)


# ---------------------------------------------------------------- mô phỏng workflow
BASH = shutil.which("bash")
GIT = shutil.which("git")
TIMEOUT_OK = bool(BASH) and subprocess.run([BASH, "-c", "command -v timeout"], capture_output=True).returncode == 0 \
    if BASH else False
WF = ROOT / ".github" / "workflows" / "bantin.yml"
SIM_OK = bool(BASH and GIT and TIMEOUT_OK and yaml)
# Git Bash mặc định "ln -s" là CHÉP file; bật symlink thật của Windows (cần Developer Mode hoặc quyền admin).
SYMLINK_ENV = {"MSYS": "winsymlinks:nativestrict"}


def _symlink_ok() -> bool:
    if not BASH:
        return False
    d = Path(tempfile.mkdtemp(prefix="ntt-ln-"))
    try:
        (d / "a").write_text("x", encoding="utf-8")
        r = subprocess.run([BASH, "-c", "ln -s a b && test -L b"], cwd=d, capture_output=True,
                           env=dict(os.environ, **SYMLINK_ENV))
        return r.returncode == 0 and (d / "b").is_symlink()
    finally:
        shutil.rmtree(d, ignore_errors=True)


SYMLINK_OK = _symlink_ok()

FAKE_CLAUDE = r"""#!/usr/bin/env bash
echo called > "$FAKE_MARK"
printf '%s\n' "$@" > "$FAKE_ARGS"
case "$FAKE_MODE" in
  valid)  cp "$FAKE_SRC" "data/daily/$FAKE_KEY.json" ;;
  broken) printf '{hỏng' > "data/daily/$FAKE_KEY.json"; exit 1 ;;
  sleep)  exec sleep 30 ;;
  tamper) printf '\nraise SystemExit(0)\n' >> scripts/validate.py
          printf '{}' > data/daily/2026-11-05-chieu.json
          mkdir -p scripts/__pycache__ scripts/data && printf 'x' > scripts/__pycache__/la.pyc && printf 'x' > scripts/data/la.json
          printf '{}' > data/daily/la.json
          cp "$FAKE_SRC" "data/daily/$FAKE_KEY.json" ;;
  attack) # S6: sửa script rồi chạy nó; ghi GITHUB_ENV và ~/.gitconfig; cố gửi thêm file lạ
          printf '#!/usr/bin/env bash\necho "$RUNNER_TEMP" >> "%s/bash_env_ran"\n' "$FAKE_PWNED" > "$HOME/evil.sh"
          cat >> scripts/common.py <<'PY'

import os as _o
if _o.environ.get("FAKE_PWNED"):
    open(_o.path.join(_o.environ["FAKE_PWNED"], "validate_ran"), "a").write("x")
    if _o.environ.get("GITHUB_ENV"):
        open(_o.environ["GITHUB_ENV"], "a").write("BASH_ENV=" + _o.path.join(_o.path.expanduser("~"), "evil.sh") + "\n")
PY
          python scripts/validate.py "data/daily/2026-11-05-chieu.json" > /dev/null || true
          echo "BASH_ENV=$HOME/evil.sh" >> "$GITHUB_ENV"
          printf '[core]\n\tfsmonitor = "echo pwned >> %s/fsmonitor_ran #"\n' "$FAKE_PWNED" > "$HOME/.gitconfig"
          echo "$CLAUDE_CODE_OAUTH_TOKEN" > data/daily/leak.json
          cp "$FAKE_SRC" "data/daily/$FAKE_KEY.json" ;;
  symlink) rm -f "data/daily/$FAKE_KEY.json"; ln -s "$FAKE_SECRET" "data/daily/$FAKE_KEY.json"; exit 1 ;;
  dirtrick) rm -f "data/daily/$FAKE_KEY.json"; mkdir -p "data/daily/$FAKE_KEY.json/sub"
            printf 'x' > "data/daily/$FAKE_KEY.json/sub/la.txt" ;;
  nested) python -c "import sys; open(sys.argv[1], 'w').write('[' * 200000 + ']' * 200000)" "data/daily/$FAKE_KEY.json" ;;
esac
"""


class Runner:
    """Một runner GitHub giả: thư mục repo, HOME, RUNNER_TEMP, GITHUB_OUTPUT riêng; env do GITHUB_ENV tích lũy."""

    def __init__(self, base: Path, name: str, src_repo: Path):
        self.dir = base / name
        self.repo, self.home, self.temp = self.dir / "repo", self.dir / "home", self.dir / "temp"
        shutil.copytree(src_repo, self.repo)
        self.home.mkdir(parents=True)
        self.temp.mkdir()
        self.job_env: dict[str, str] = {}
        self.n = 0


@unittest.skipUnless(SIM_OK, "cần bash, git, timeout và PyYAML")
class WorkflowSimulation(unittest.TestCase):
    """Chạy NGUYÊN VĂN các khối run của job draft rồi job publish (trừ cài CLI và push) bằng bash.
    Hai job chạy trên hai "runner" riêng (repo, HOME, RUNNER_TEMP, env riêng); file chỉ đi qua thư mục artifact."""

    @classmethod
    def setUpClass(cls):
        cls.wf = yaml.safe_load(WF.read_text(encoding="utf-8"))

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="ntt-sim-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        origin = self.tmp / "origin"
        origin.mkdir()
        for d in ("scripts", "docs", "data"):
            shutil.copytree(ROOT / d, origin / d, ignore=shutil.ignore_patterns("__pycache__", "*.tmp"))
        shutil.copy(ROOT / "AGENTS.md", origin)
        now = now_vn()
        self.session = session_for(now)
        if self.session == "chieu" and now.weekday() >= 5:
            self.session = "sang"
        self.d = now.date()
        self.key = daily_key(self.d, self.session)
        (origin / "data" / "daily" / f"{self.key}.json").unlink(missing_ok=True)
        for a in (["init", "-q"], ["config", "core.autocrlf", "false"], ["add", "-A"],
                  ["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "init"]):
            subprocess.run([GIT, *a], cwd=origin, check=True, capture_output=True)
        self.origin = origin
        recent = R.window_for(self.d, self.session, now)[1] - timedelta(seconds=30)
        feed = self.tmp / "feed.xml"
        body = "".join(f"<item><title>{t}</title><link>https://e.vn/{i}</link><pubDate>{format_datetime(recent)}</pubDate></item>"
                       for i, t in enumerate(["Lãi suất huy động giảm", "Cổ phiếu ngân hàng tăng", "Giá vàng SJC đi ngang"]))
        feed.write_text(f'<?xml version="1.0" encoding="utf-8"?><rss version="2.0"><channel><title>t</title>{body}</channel></rss>',
                        encoding="utf-8")
        cfg = dict(read_json(ROOT / "scripts" / "rss_config.json"), sources=[{"name": "Mẫu", "url": feed.as_uri()}])
        (self.tmp / "cfg.json").write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")
        write_json(self.tmp / "ai.json", ai_edition(self.d, self.session))
        bindir = self.tmp / "bin"
        bindir.mkdir()
        (bindir / "claude").write_text(FAKE_CLAUDE, encoding="utf-8", newline="\n")
        os.chmod(bindir / "claude", 0o755)
        self.artifacts = self.tmp / "artifacts"
        self.artifacts.mkdir()
        self.pwned = self.tmp / "pwned"
        self.pwned.mkdir()
        self.mark, self.args_file = self.tmp / "called", self.tmp / "args.txt"
        env = {k: v for k, v in os.environ.items() if k not in ("BASH_ENV", "ENV", "NTT_BROWSER", "GITHUB_ENV")}
        self.base_env = dict(env, PYTHONIOENCODING="utf-8", PATH=str(bindir) + os.pathsep + os.environ.get("PATH", ""),
                             NTT_RSS_CONFIG=str(self.tmp / "cfg.json"), GIT_CONFIG_NOSYSTEM="1",
                             FAKE_MARK=self.mark.as_posix(), FAKE_ARGS=self.args_file.as_posix(),
                             FAKE_SRC=(self.tmp / "ai.json").as_posix(), FAKE_KEY=self.key,
                             FAKE_PWNED=self.pwned.as_posix(),
                             # Git Bash (Windows) tự đổi tham số dạng "/data" thành đường dẫn Windows; runner Linux thì không
                             MSYS_NO_PATHCONV="1", MSYS2_ARG_CONV_EXCL="*", **SYMLINK_ENV,
                             FAKE_SECRET=(self.tmp / "secret.txt").as_posix())
        (self.tmp / "secret.txt").write_text("TOKEN-BI-MAT-123", encoding="utf-8")
        self.plan_out: dict[str, str] = {}
        self.draft_out: dict[str, str] = {}

    # ---- biểu thức ${{ }} và điều kiện if
    def expr(self, e: str, token: str, runner: Runner) -> str:
        e = e.strip()
        if "||" in e:
            a, b = (x.strip() for x in e.split("||", 1))
            return self.expr(a, token, runner) or b.strip("'")
        if e == "secrets.CLAUDE_CODE_OAUTH_TOKEN != ''":
            return "true" if token else "false"
        if e == "secrets.CLAUDE_CODE_OAUTH_TOKEN":
            return token
        if e == "inputs.session":
            return self.session
        if e == "inputs.force":
            return ""
        if e == "runner.temp":
            return runner.temp.as_posix()
        m = re.fullmatch(r"steps\.plan\.outputs\.(\w+)", e)
        if m:
            return self.plan_out.get(m.group(1), "")
        m = re.fullmatch(r"needs\.draft\.outputs\.(\w+)", e)
        if m:
            return self.draft_out.get(m.group(1), "")
        raise AssertionError(f"biểu thức chưa hỗ trợ trong mô phỏng: {e}")

    def sub(self, v, token, runner):
        return re.sub(r"\$\{\{(.*?)\}\}", lambda m_: self.expr(m_.group(1), token, runner), str(v))

    def cond(self, c) -> bool:
        if c is None:
            return True
        m = re.fullmatch(r"(steps\.plan|needs\.draft)\.outputs\.(\w+) (==|!=) '([^']*)'", str(c).strip())
        self.assertIsNotNone(m, f"điều kiện chưa hỗ trợ: {c}")
        src = self.plan_out if m.group(1) == "steps.plan" else self.draft_out
        v = src.get(m.group(2), "")
        return (v == m.group(4)) if m.group(3) == "==" else (v != m.group(4))

    # ---- chạy một job
    def run_job(self, job: str, runner: Runner, token: str, mode: str, timeout_s: str = "5s", strict: bool = True,
                drop: tuple = ()):
        log = []
        for st in self.wf["jobs"][job]["steps"]:
            name = st.get("name") or st.get("uses") or ""
            if drop and name.startswith(drop):  # đối chứng âm: bỏ hẳn một bước
                continue
            uses, run = st.get("uses", ""), st.get("run")
            if uses.startswith(("actions/checkout", "actions/setup-python")) or (run and ("npm install" in run or "git push" in run)):
                continue
            if not self.cond(st.get("if")):
                log.append((name, "skipped", ""))
                continue
            if uses.startswith("actions/upload-artifact"):
                w = st["with"]
                dest = self.artifacts / w["name"]
                for line in self.sub(w["path"], token, runner).splitlines():
                    p = runner.repo / line.strip()
                    if not line.strip() or not p.exists():
                        continue
                    # như upload-artifact: đi theo symlink, thư mục thì gửi cả cây
                    if p.is_dir():
                        shutil.copytree(p, dest / line.strip(), dirs_exist_ok=True)
                    else:
                        (dest / line.strip()).parent.mkdir(parents=True, exist_ok=True)
                        shutil.copyfile(p, dest / line.strip())
                log.append((name, 0, ""))
                continue
            if uses.startswith("actions/download-artifact"):
                w = st["with"]
                src = self.artifacts / w["name"]
                if src.is_dir():
                    shutil.copytree(src, Path(self.sub(w["path"], token, runner)), dirs_exist_ok=True)
                    log.append((name, 0, ""))
                else:
                    log.append((name, 1, "Artifact not found"))
                    self.assertTrue(st.get("continue-on-error"), f"bước '{name}' lỗi")
                continue
            self.assertIsNotNone(run, f"bước chưa mô phỏng: {name}")
            runner.n += 1
            gh_env = runner.temp / f"github_env_{runner.n}"
            gh_env.write_text("", encoding="utf-8")
            env = dict(self.base_env, **runner.job_env, FAKE_MODE=mode, HOME=runner.home.as_posix(),
                       USERPROFILE=str(runner.home), RUNNER_TEMP=runner.temp.as_posix(),
                       GITHUB_OUTPUT=(runner.temp / "gh_output").as_posix(), GITHUB_ENV=gh_env.as_posix())
            for k, v in (st.get("env") or {}).items():
                env[k] = self.sub(v, token, runner)
            if "CLAUDE_TIMEOUT" in env:
                env["CLAUDE_TIMEOUT"] = timeout_s
            script = runner.temp / "step.sh"
            script.write_text(run, encoding="utf-8", newline="\n")
            r = subprocess.run([BASH, "--noprofile", "--norc", "-eo", "pipefail", script.as_posix()],
                               cwd=runner.repo, env=env, capture_output=True, timeout=120)
            text = r.stdout.decode("utf-8", "replace") + r.stderr.decode("utf-8", "replace")
            log.append((name, r.returncode, text))
            # runner thật: GITHUB_ENV của bước này áp cho các bước SAU trong cùng job
            for ln in gh_env.read_text(encoding="utf-8").splitlines():
                if "=" in ln:
                    k, v = ln.split("=", 1)
                    runner.job_env[k] = v
            if st.get("id") == "plan":
                out = (runner.temp / "gh_output").read_text(encoding="utf-8")
                self.plan_out = dict(ln.split("=", 1) for ln in out.splitlines() if "=" in ln)
            if name.startswith("Tầng 1"):
                p = runner.repo / "data" / "daily" / f"{self.key}.json"
                self.rss_bytes = p.read_bytes() if p.exists() else None
            if name.startswith("Tầng 2"):
                self.after_claude = {rel: (runner.repo / rel).read_bytes() if (runner.repo / rel).is_file() else None
                                     for rel in ("scripts/validate.py", "scripts/common.py",
                                                 "data/daily/2026-11-05-chieu.json", f"data/daily/{self.key}.json")}
            if r.returncode and not st.get("continue-on-error") and strict:
                self.fail(f"bước '{name}' lỗi (mã {r.returncode}):\n{text[-2000:]}")
        if job == "draft":
            self.draft_out = {k: self.sub(v, token, runner) for k, v in self.wf["jobs"]["draft"]["outputs"].items()}
        return log

    def simulate(self, token: str, mode: str = "", timeout_s: str = "5s", same_runner: bool = False, drop: tuple = ()):
        """same_runner=True là ĐỐI CHỨNG ÂM: publish chạy tiếp trên đúng runner/repo/HOME/env mà Claude đã chạm
        (giống thiết kế một job trước vòng sửa 1)."""
        self.draft = Runner(self.tmp, "draft", self.origin)
        log = self.run_job("draft", self.draft, token, mode, timeout_s, drop=drop)
        self.publish = self.draft if same_runner else Runner(self.tmp, "publish", self.origin)
        log += self.run_job("publish", self.publish, token, mode, timeout_s, strict=not same_runner)
        return log

    def final(self):
        return self.publish.repo / "data" / "daily" / f"{self.key}.json"

    def step(self, log, prefix):
        return next(x for x in log if (x[0] or "").startswith(prefix))

    def validate_rc(self):
        return subprocess.run([sys.executable, str(self.publish.repo / "scripts" / "validate.py")], cwd=self.publish.repo,
                              capture_output=True, env=self.base_env).returncode

    def test_s1_no_token(self):
        log = self.simulate(token="")
        self.assertIn("::warning::", self.step(log, "Lập kế hoạch")[2])
        self.assertIn("CLAUDE_CODE_OAUTH_TOKEN", self.step(log, "Lập kế hoạch")[2])
        self.assertFalse(self.mark.exists(), "không được gọi claude khi không có token")
        self.assertEqual(self.step(log, "Tầng 2")[1], "skipped")
        self.assertEqual(self.step(log, "Kiểm tra kết quả Claude")[1], "skipped")
        self.assertEqual(read_json(self.final())["mode"], "rss")
        self.assertEqual(self.final().read_bytes(), self.rss_bytes)
        self.assertEqual(self.validate_rc(), 0)
        self.assertIn(self.key, read_json(self.publish.repo / "data" / "index.json")["daily"])

    def test_s2_valid_ai_and_cli_flags(self):
        log = self.simulate(token="tok", mode="valid")
        self.assertTrue(self.mark.exists())
        self.assertEqual(self.step(log, "Tầng 2")[1], 0, self.step(log, "Tầng 2")[2])
        self.assertNotIn("mode", read_json(self.final()))
        self.assertEqual(self.validate_rc(), 0)
        lines = self.args_file.read_text(encoding="utf-8").splitlines()
        self.assertEqual(lines[0], "-p")
        prompt = "\n".join(lines[1:lines.index("--restricted")])
        self.assertIn("chế độ agent", prompt)
        self.assertIn(f"data/daily/{self.key}.json", prompt)
        val = lambda flag: lines[lines.index(flag) + 1]  # noqa: E731
        self.assertEqual(val("--tools"), "WebSearch,WebFetch,Read,Write,Edit")
        self.assertEqual(val("--permission-mode"), "dontAsk")
        allowed = val("--allowedTools").split(",")
        self.assertEqual([a for a in allowed if "(" not in a], ["WebSearch"], "không được có Read/Write/Edit/Bash trần")
        self.assertIn("Edit(./data/daily/**)", allowed)
        self.assertIn("Edit(./data/weekly/**)", allowed)
        self.assertIn("WebFetch(domain:cafef.vn)", allowed)
        self.assertFalse([a for a in allowed if a.startswith("Bash")])
        denied = val("--disallowedTools").split(",")
        for d in ("Bash", "Read(//proc/**)", "Read(~/**)", "Edit(./scripts/**)", "Edit(./.git/**)", "Edit(./.github/**)"):
            self.assertIn(d, denied)
        self.assertEqual(val("--max-turns"), "150")

    def test_s3_broken_ai_restored(self):
        log = self.simulate(token="tok", mode="broken")
        self.assertNotEqual(self.step(log, "Tầng 2")[1], 0)
        self.assertIn("::warning::Claude Code lỗi", self.step(log, "Tầng 2")[2])
        self.assertIsNotNone(self.rss_bytes)
        self.assertNotEqual(self.after_claude[f"data/daily/{self.key}.json"], self.rss_bytes)
        self.assertEqual(self.final().read_bytes(), self.rss_bytes)
        self.assertEqual(self.validate_rc(), 0)

    def test_s4_timeout_restored(self):
        import time
        t0 = time.monotonic()
        log = self.simulate(token="tok", mode="sleep", timeout_s="3s")
        self.assertLess(time.monotonic() - t0, 40, "timeout không cắt được claude giả (sleep 30)")
        self.assertIn("mã 124", self.step(log, "Tầng 2")[2])
        self.assertEqual(self.final().read_bytes(), self.rss_bytes)
        self.assertEqual(self.validate_rc(), 0)

    def test_s5_tamper_stays_on_draft_runner(self):
        orig_validate = (self.origin / "scripts" / "validate.py").read_bytes()
        orig_old = (self.origin / "data" / "daily" / "2026-11-05-chieu.json").read_bytes()
        self.simulate(token="tok", mode="tamper")
        # đối chứng: trên runner draft, Claude giả đã thật sự sửa
        self.assertNotEqual(self.after_claude["scripts/validate.py"], orig_validate)
        self.assertNotEqual(self.after_claude["data/daily/2026-11-05-chieu.json"], orig_old)
        self.assertTrue((self.draft.repo / "scripts" / "data" / "la.json").exists())
        pub = self.publish.repo
        self.assertEqual((pub / "scripts" / "validate.py").read_bytes(), orig_validate)
        self.assertEqual((pub / "data" / "daily" / "2026-11-05-chieu.json").read_bytes(), orig_old)
        self.assertFalse((pub / "scripts" / "data").exists())
        self.assertFalse((pub / "scripts" / "__pycache__" / "la.pyc").exists())
        self.assertFalse((pub / "data" / "daily" / "la.json").exists())
        self.assertNotIn("mode", read_json(self.final()))  # bản AI hợp lệ vẫn giữ
        self.assertEqual(self.validate_rc(), 0)

    def test_s6_attack_isolated_by_two_jobs(self):
        log = self.simulate(token="tok-bi-mat", mode="attack")
        self.assertTrue((self.pwned / "validate_ran").exists(), "đối chứng: script bị sửa đã chạy trên runner draft")
        self.assertTrue((self.draft.home / ".gitconfig").exists())
        self.assertIn("BASH_ENV", self.draft.job_env)
        # evil.sh ghi RUNNER_TEMP của runner nơi nó chạy. Trên runner draft (sau Claude) nó có thể chạy – đó vẫn là
        # vùng không quyền ghi, không token; điều cần chứng minh là KHÔNG có dòng nào của runner publish.
        ran = (self.pwned / "bash_env_ran").read_text(encoding="utf-8").split() if (self.pwned / "bash_env_ran").exists() else []
        self.assertNotIn(self.publish.temp.as_posix(), ran, "BASH_ENV do Claude cài không được chạy ở job publish")
        self.assertFalse((self.pwned / "fsmonitor_ran").exists())
        self.assertNotIn("BASH_ENV", self.publish.job_env)
        self.assertFalse((self.publish.home / ".gitconfig").exists())
        pub = self.publish.repo
        self.assertEqual((pub / "scripts" / "common.py").read_bytes(), (self.origin / "scripts" / "common.py").read_bytes())
        self.assertFalse((pub / "data" / "daily" / "leak.json").exists())
        self.assertFalse(list(self.artifacts.rglob("leak.json")), "artifact chỉ chứa 2 file đích")
        self.assertNotIn("tok-bi-mat", "".join(x[2] for x in log if x[0].startswith(("Đưa", "Kiểm tra", "Cập nhật"))))
        self.assertNotIn("mode", read_json(self.final()))
        self.assertEqual(self.validate_rc(), 0)

    def test_s6_negative_control_single_runner_is_compromised(self):
        # Đối chứng âm: nếu bước có quyền ghi chạy tiếp trên runner Claude đã chạm (thiết kế một job cũ),
        # BASH_ENV do Claude ghi vào GITHUB_ENV được bash nạp ở mọi bước sau → chạy được lệnh tùy ý.
        self.simulate(token="tok-bi-mat", mode="attack", same_runner=True)
        ran = (self.pwned / "bash_env_ran").read_text(encoding="utf-8").split()
        # các bước của publish (chạy sau bước dọn của draft) cũng nạp evil.sh: số lần chạy > số bước draft sau Claude
        self.assertGreater(len(ran), 2)


    # ---- vòng sửa 2
    def artifact_texts(self, name="ai"):
        base = self.artifacts / name
        return {p.relative_to(base).as_posix(): p.read_bytes() for p in base.rglob("*") if p.is_file()} if base.exists() else {}

    def test_s7_nested_json_keeps_rss(self):
        log = self.simulate(token="tok", mode="nested")
        self.assertTrue(self.after_claude[f"data/daily/{self.key}.json"].startswith(b"[[[["))  # đối chứng: đã ghi file lồng sâu
        name, rc, text = self.step(log, "Kiểm tra kết quả Claude")
        self.assertEqual(rc, 0, text[-2000:])
        self.assertIn("không đọc được JSON", text)
        self.assertEqual(self.step(log, "Kiểm tra dữ liệu")[1], 0)
        self.assertEqual(self.final().read_bytes(), self.rss_bytes)
        self.assertEqual(self.validate_rc(), 0)

    def test_s8_dirtrick_no_foreign_tree(self):
        log = self.simulate(token="tok", mode="dirtrick")
        self.assertEqual(self.step(log, "Dọn symlink")[1], 0)
        self.assertFalse([k for k in self.artifact_texts() if "la.txt" in k or "/sub/" in k], list(self.artifact_texts()))
        self.assertEqual(self.final().read_bytes(), self.rss_bytes)
        self.assertEqual(self.validate_rc(), 0)

    def test_s8_dirtrick_negative_control_without_cleanup(self):
        self.simulate(token="tok", mode="dirtrick", drop=("Dọn symlink",))
        self.assertTrue([k for k in self.artifact_texts() if k.endswith("sub/la.txt")])

    @unittest.skipUnless(SYMLINK_OK, "máy này không tạo được symlink thật (Git Bash với MSYS=winsymlinks:nativestrict "
                                     "cần Developer Mode hoặc quyền admin trên Windows)")
    def test_s9_symlink_to_secret_not_uploaded(self):
        log = self.simulate(token="tok", mode="symlink")
        self.assertNotEqual(self.step(log, "Tầng 2")[1], 0)  # Claude giả thoát 1, bước dọn vẫn chạy
        self.assertEqual(self.step(log, "Dọn symlink")[1], 0)
        self.assertFalse(any(b"TOKEN-BI-MAT-123" in v for v in self.artifact_texts().values()))
        self.assertEqual(self.final().read_bytes(), self.rss_bytes)

    @unittest.skipUnless(SYMLINK_OK, "máy này không tạo được symlink thật (Git Bash với MSYS=winsymlinks:nativestrict "
                                     "cần Developer Mode hoặc quyền admin trên Windows)")
    def test_s9_symlink_negative_control_without_cleanup(self):
        log = self.simulate(token="tok", mode="symlink", drop=("Dọn symlink",))
        self.assertTrue((self.draft.repo / "data" / "daily" / f"{self.key}.json").is_symlink())
        self.assertTrue(any(b"TOKEN-BI-MAT-123" in v for v in self.artifact_texts().values()))


@unittest.skipUnless(SIM_OK, "cần bash, git và PyYAML")
class GitCleanTests(unittest.TestCase):
    """L2: dòng `git clean` của workflow phải xóa scripts/data/ nhưng giữ data/ ở gốc repo."""

    def clean_line(self):
        wf = yaml.safe_load(WF.read_text(encoding="utf-8"))
        runs = [s.get("run") or "" for s in wf["jobs"]["publish"]["steps"]]
        lines = [ln.strip() for r in runs for ln in r.splitlines() if ln.strip().startswith("git clean")]
        self.assertEqual(len(lines), 1)
        return lines[0]

    def run_clean(self, line):
        tmp = Path(tempfile.mkdtemp(prefix="ntt-clean-"))
        try:
            subprocess.run([GIT, "init", "-q"], cwd=tmp, check=True)
            (tmp / "scripts").mkdir()
            (tmp / "scripts" / "a.py").write_text("x", encoding="utf-8")
            subprocess.run([GIT, "add", "-A"], cwd=tmp, check=True)
            subprocess.run([GIT, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "i"], cwd=tmp, check=True)
            (tmp / "scripts" / "data").mkdir()
            (tmp / "scripts" / "data" / "la.json").write_text("{}", encoding="utf-8")
            (tmp / "data" / "daily").mkdir(parents=True)
            (tmp / "data" / "daily" / "moi.json").write_text("{}", encoding="utf-8")
            env = dict(os.environ, MSYS_NO_PATHCONV="1", MSYS2_ARG_CONV_EXCL="*")  # như runner Linux
            subprocess.run([BASH, "-c", line], cwd=tmp, check=True, env=env)
            return (tmp / "scripts" / "data").exists(), (tmp / "data" / "daily" / "moi.json").exists()
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_anchored_exclude(self):
        line = self.clean_line()
        self.assertIn("-e /data", line)
        self.assertEqual(self.run_clean(line), (False, True))

    def test_negative_control_unanchored(self):
        # đối chứng: "-e data" khớp mọi thư mục tên data nên scripts/data/ sống sót
        self.assertEqual(self.run_clean(self.clean_line().replace("-e /data", "-e data")), (True, True))


if __name__ == "__main__":
    unittest.main(verbosity=2)
