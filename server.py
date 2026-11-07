"""Máy chủ cục bộ cho trang Nhịp Thị Trường.

- Phục vụ trang web (web/index.html) tại http://localhost:8000
- Phục vụ dữ liệu bản tin tại /data/... (thư mục data/)
- Endpoint POST /api/ask cho tab "Hỏi đáp" (gọi Anthropic API bằng ANTHROPIC_API_KEY trong .env)
- Endpoint GET /api/health: {"ok": true, "ai": <có khóa hay không>} để trang biết có máy chủ AI

Chạy:  python server.py            (cổng mặc định 8000)
       python server.py --port 9000
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent
WEB = ROOT / "web"
DATA = ROOT / "data"

try:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
except ImportError:  # python-dotenv là tùy chọn
    pass

MODEL = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-5-5")
MAX_BODY = 2 * 1024 * 1024


class Handler(SimpleHTTPRequestHandler):
    def translate_path(self, path: str) -> str:
        clean = path.split("?", 1)[0].split("#", 1)[0]
        if clean.startswith("/data/"):
            target = (DATA / clean[len("/data/"):]).resolve()
            base = DATA.resolve()
        else:
            rel = clean.lstrip("/") or "index.html"
            target = (WEB / rel).resolve()
            base = WEB.resolve()
        if base not in target.parents and target != base:
            return str(WEB / "__forbidden__")
        return str(target)

    def end_headers(self) -> None:
        if self.path.startswith("/data/") or self.path.startswith("/api/"):
            self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def do_GET(self) -> None:  # noqa: N802
        # Trang web gọi /api/health để biết có máy chủ AI (hiện tab Hỏi đáp) hay không.
        if self.path.split("?", 1)[0].split("#", 1)[0] == "/api/health":
            return self._json(HTTPStatus.OK, {"ok": True, "ai": bool(os.getenv("ANTHROPIC_API_KEY"))})
        super().do_GET()

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/api/ask":
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0 or length > MAX_BODY:
            return self._json(HTTPStatus.BAD_REQUEST, {"error": "Yêu cầu không hợp lệ"})
        try:
            body = json.loads(self.rfile.read(length))
            messages = body["messages"]
            assert isinstance(messages, list) and messages
        except Exception:
            return self._json(HTTPStatus.BAD_REQUEST, {"error": "JSON không hợp lệ"})

        if not os.getenv("ANTHROPIC_API_KEY"):
            return self._json(HTTPStatus.SERVICE_UNAVAILABLE, {"error": "Thiếu ANTHROPIC_API_KEY trong .env"})
        try:
            import anthropic
        except ImportError:
            return self._json(HTTPStatus.SERVICE_UNAVAILABLE, {"error": "Chưa cài thư viện anthropic (pip install -r requirements.txt)"})

        # Gộp các lượt liên tiếp cùng vai trò (API yêu cầu xen kẽ user/assistant)
        merged: list[dict] = []
        for m in messages:
            role = "assistant" if m.get("role") == "assistant" else "user"
            text = str(m.get("content") or "")
            if merged and merged[-1]["role"] == role:
                merged[-1]["content"] += "\n\n" + text
            else:
                merged.append({"role": role, "content": text})
        if merged[0]["role"] != "user":
            merged.insert(0, {"role": "user", "content": "(bắt đầu)"})

        try:
            client = anthropic.Anthropic()
            resp = client.messages.create(model=MODEL, max_tokens=2000, messages=merged)
            text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
            return self._json(HTTPStatus.OK, {"text": text})
        except anthropic.RateLimitError:
            return self._json(HTTPStatus.TOO_MANY_REQUESTS, {"error": "Chạm giới hạn API"})
        except Exception as e:  # pragma: no cover
            return self._json(HTTPStatus.BAD_GATEWAY, {"error": str(e)})

    def _json(self, status: HTTPStatus, obj: dict) -> None:
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def main() -> None:
    # Windows: stdout chuyển hướng (Task Scheduler, file log) có thể là cp1252, in tiếng Việt sẽ crash.
    # line_buffering=True: dòng khởi động được ghi ngay vào file log, không chờ tiến trình thoát.
    for s in (sys.stdout, sys.stderr):
        if hasattr(s, "reconfigure"):
            s.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", type=int, default=int(os.getenv("PORT", "8000")))
    ap.add_argument("--host", default="127.0.0.1")
    args = ap.parse_args()
    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"Nhịp Thị Trường: http://localhost:{args.port}  (Ctrl+C để dừng)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nĐã dừng.")
        sys.exit(0)


if __name__ == "__main__":
    main()
