"""Người gác cho workflow GitHub Actions: lập kế hoạch lần chạy, sao lưu dữ liệu, kiểm tra và khôi phục.

    python scripts/ci_guard.py plan [--date D] [--session ""|sang|chieu] [--force true|false|""]
                                    [--has-token true|false] [--github-output FILE]
    python scripts/ci_guard.py snapshot --dir SNAP
    python scripts/ci_guard.py check --dir SNAP --key K [--week W]
    python scripts/ci_guard.py import --src DIR --key K [--week W] [--only-over-rss]

Tham số chung: --data DIR (mặc định data/ của repo).
`check` luôn thoát 0 (trừ lỗi nội bộ): bản Claude hợp lệ thì giữ, không hợp lệ thì khôi phục
nguyên byte bản trước đó (thường là bản RSS), mọi file khác trong data/ bị Claude đổi đều bị hoàn tác.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
from datetime import date
from pathlib import Path

from common import DATA, daily_key, now_vn, session_for, setup_stdio, weekly_key
from validate import check_daily, check_weekly

NO_TOKEN_WARNING = ("::warning::Chưa có secret CLAUDE_CODE_OAUTH_TOKEN – chỉ có bản tự động RSS "
                    "(chưa có nhận định AI).")


def truthy(s: str | None) -> bool:
    return str(s or "").strip().lower() in ("true", "1", "yes", "on")


def read_obj(p: Path):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError, RecursionError, MemoryError):  # JSON lồng quá sâu → RecursionError
        return None


# ---------------------------------------------------------------- plan
def plan(data: Path, d_str: str | None, session: str | None, force: bool, has_token: bool) -> dict:
    now = now_vn()
    d = date.fromisoformat(d_str) if d_str else now.date()
    session = session or session_for(now)
    weekend = d.weekday() >= 5
    skip = session == "chieu" and weekend
    key = daily_key(d, session)
    p = data / "daily" / f"{key}.json"
    obj = read_obj(p) if p.exists() else None
    ai_exists = isinstance(obj, dict) and obj.get("mode") != "rss"
    claude = has_token and not skip and (not ai_exists or force)
    b = lambda x: "true" if x else "false"  # noqa: E731
    return {"date": d.isoformat(), "session": session, "key": key,
            "week": weekly_key(d) if session == "chieu" and not weekend else "",
            "skip": b(skip), "ai_exists": b(ai_exists), "has_token": b(has_token), "claude": b(claude)}


# ---------------------------------------------------------------- snapshot
def snapshot(data: Path, snap: Path) -> None:
    """Chép toàn bộ data/ (daily, weekly, index.json…) vào snap."""
    if snap.exists():
        shutil.rmtree(snap)
    shutil.copytree(data, snap)


# ---------------------------------------------------------------- check
def _files(base: Path) -> set[str]:
    return {p.relative_to(base).as_posix() for p in base.rglob("*") if p.is_file()} if base.exists() else set()


def _restore(data: Path, snap: Path, rel: str) -> str:
    """Đưa data/rel về đúng bản snapshot (hoặc xóa nếu snapshot không có). Trả mô tả việc đã làm."""
    cur, old = data / rel, snap / rel
    if old.is_file():
        cur.parent.mkdir(parents=True, exist_ok=True)
        if cur.is_dir():
            shutil.rmtree(cur)
        shutil.copyfile(old, cur)
        return "khôi phục bản trước"
    if cur.is_dir():
        shutil.rmtree(cur)
    elif cur.exists():
        cur.unlink()
    return "xóa (trước đó không có)"


def _same(a: Path, b: Path) -> bool:
    return a.is_file() and b.is_file() and a.read_bytes() == b.read_bytes()


def daily_problem(p: Path, key: str) -> str | None:
    """None nếu bản Claude hợp lệ, ngược lại là lý do."""
    if not p.is_file():
        return "file bị xóa"
    obj = read_obj(p)
    if not isinstance(obj, dict):
        return "không đọc được JSON"
    if obj.get("mode") == "rss":
        return "vẫn là mode rss"
    if obj.get("key") != key or daily_key(_date(obj.get("date")), obj.get("session") or "") != key:
        return f"key/date/session không khớp {key}"
    try:
        rep = check_daily(p)
    except Exception as e:  # cấu trúc lạ làm validate ném lỗi
        return f"validate lỗi: {type(e).__name__}"
    if rep.errors:
        return f"{len(rep.errors)} ERROR: {rep.errors[0]}"
    return None


def _date(s) -> date:
    try:
        return date.fromisoformat(str(s))
    except ValueError:
        return date(1, 1, 1)


def weekly_problem(p: Path, old: Path, week: str) -> str | None:
    if not p.is_file():
        return "file bị xóa"
    obj = read_obj(p)
    if not isinstance(obj, dict):
        return "không đọc được JSON"
    if obj.get("key") != week:
        return f"key không khớp {week}"
    try:
        rep = check_weekly(p)
    except Exception as e:
        return f"validate lỗi: {type(e).__name__}"
    if rep.errors:
        return f"{len(rep.errors)} ERROR: {rep.errors[0]}"
    prev = read_obj(old) if old.is_file() else None
    if isinstance(prev, dict):
        have = {(a.get("date"), a.get("title")) for a in obj.get("anomalies") or [] if isinstance(a, dict)}
        lost = [a for a in prev.get("anomalies") or [] if isinstance(a, dict) and (a.get("date"), a.get("title")) not in have]
        if lost:
            return f"mất {len(lost)} anomaly cũ"
    return None


def _guard_target(data: Path, snap: Path, rel: str, problem: str | None, label: str) -> str:
    cur, old = data / rel, snap / rel
    if not cur.exists() and not old.exists():
        print(f"{label}: không có file (Claude không ghi).")
        return "không có"
    if _same(cur, old):
        print(f"::warning::Claude không đổi bản tin {label} – giữ nguyên.")
        return "không đổi"
    if problem is None:
        print(f"{label}: giữ bản AI.")
        return "giữ bản AI"
    what = _restore(data, snap, rel)
    print(f"::warning::Bản Claude không hợp lệ ({problem}) – khôi phục bản RSS. {label}: {what}.")
    return "khôi phục"


def check(data: Path, snap: Path, key: str, week: str | None) -> dict:
    targets = {f"daily/{key}.json"} | ({f"weekly/{week}.json"} if week else set())
    reverted = []
    for rel in sorted((_files(data) | _files(snap)) - targets):
        if not _same(data / rel, snap / rel):
            reverted.append(f"{rel}: {_restore(data, snap, rel)}")
    # thư mục rỗng do Claude tạo
    for d in sorted((p for p in data.rglob("*") if p.is_dir()), key=lambda x: len(x.parts), reverse=True):
        rel = d.relative_to(data).as_posix()
        if not (snap / rel).is_dir() and not any(d.iterdir()):
            d.rmdir()
    for r in reverted:
        print(f"::warning::Hoàn tác file ngoài phạm vi: {r}")

    drel = f"daily/{key}.json"
    res = {"daily": _guard_target(data, snap, drel, daily_problem(data / drel, key), drel)}
    if week:
        wrel = f"weekly/{week}.json"
        res["weekly"] = _guard_target(data, snap, wrel, weekly_problem(data / wrel, snap / wrel, week), wrel)
    res["reverted"] = len(reverted)
    print("Tóm tắt: " + ", ".join(f"{k}={v}" for k, v in res.items()))
    return res


# ---------------------------------------------------------------- import (nhận artifact)
KEY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}-(sang|chieu)$")
WEEK_RE = re.compile(r"^\d{4}-W\d{2}$")
MAX_BYTES = 5 * 1024 * 1024


def _find(src: Path, name: str, prefer: str) -> Path | None:
    """Tìm file thường (không phải symlink) tên đúng `name` trong src; nhiều bản thì ưu tiên nằm trong thư mục `prefer`."""
    found = []
    for dirpath, _dirs, files in os.walk(src, followlinks=False):
        if name in files:
            p = Path(dirpath) / name
            if p.is_file() and not p.is_symlink():
                found.append(p)
    if len(found) > 1:
        found = [p for p in found if p.parent.name == prefer] or found
    return found[0] if len(found) == 1 else None


def import_files(data: Path, src: Path, key: str, week: str | None, only_over_rss: bool) -> dict:
    """Chép CHỈ daily/<key>.json (và weekly/<week>.json) từ thư mục artifact vào data/. Mọi file khác bị bỏ qua."""
    if not KEY_RE.match(key) or (week and not WEEK_RE.match(week)):
        raise SystemExit(f"key/week không hợp lệ: {key!r} {week!r}")
    res = {}
    targets = [("daily", f"{key}.json")] + ([("weekly", f"{week}.json")] if week else [])
    for sub, name in targets:
        p = _find(src, name, sub) if src.is_dir() else None
        dst = data / sub / name
        if p is None:
            res[name] = "không có"
            continue
        if p.stat().st_size > MAX_BYTES:
            res[name] = "bỏ qua (quá lớn)"
            continue
        if only_over_rss and dst.exists():
            old = read_obj(dst)
            if not (isinstance(old, dict) and old.get("mode") == "rss"):
                res[name] = "bỏ qua (đã có bản AI)"
                continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(p, dst)
        res[name] = "đã nhận"
    print("Nhận artifact: " + ", ".join(f"{k}={v}" for k, v in res.items()))
    return res


# ---------------------------------------------------------------- dòng lệnh
def main(argv: list[str] | None = None) -> int:
    setup_stdio()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", default=str(DATA), help="thư mục dữ liệu (mặc định data/ của repo)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("plan")
    p.add_argument("--date", default="")
    p.add_argument("--session", default="", choices=["", "sang", "chieu"])
    p.add_argument("--force", default="")
    p.add_argument("--has-token", default="false")
    p.add_argument("--github-output", default="")
    s = sub.add_parser("snapshot")
    s.add_argument("--dir", required=True)
    c = sub.add_parser("check")
    c.add_argument("--dir", required=True)
    c.add_argument("--key", required=True)
    c.add_argument("--week", default="")
    im = sub.add_parser("import")
    im.add_argument("--src", required=True)
    im.add_argument("--key", required=True)
    im.add_argument("--week", default="")
    im.add_argument("--only-over-rss", action="store_true", help="không ghi đè file đích nếu đó là bản AI")
    args = ap.parse_args(argv)
    data = Path(args.data)

    if args.cmd == "plan":
        has_token = truthy(args.has_token)
        out = plan(data, args.date or None, args.session or None, truthy(args.force), has_token)
        lines = [f"{k}={v}" for k, v in out.items()]
        if args.github_output:
            with open(args.github_output, "a", encoding="utf-8", newline="\n") as f:
                f.write("\n".join(lines) + "\n")
            print("Kế hoạch: " + ", ".join(lines))
        else:
            print("\n".join(lines))
        if not has_token:
            print(NO_TOKEN_WARNING)
        return 0
    if args.cmd == "snapshot":
        snapshot(data, Path(args.dir))
        print(f"Đã sao lưu {data} vào {args.dir}")
        return 0
    if args.cmd == "import":
        import_files(data, Path(args.src), args.key, args.week or None, args.only_over_rss)
        return 0
    check(data, Path(args.dir), args.key, args.week or None)
    return 0


if __name__ == "__main__":
    sys.exit(main())
