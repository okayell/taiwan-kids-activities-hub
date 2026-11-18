#!/usr/bin/env python3
"""台灣親子趣 — 本機預覽與資料同步伺服器 (ThreadingHTTPServer)"""

from __future__ import annotations

import argparse
import gzip
import json
import mimetypes
import subprocess
import sys
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parent
SYNC_LOCK = threading.Lock()
GZIP_CACHE: dict[str, tuple[int, bytes]] = {}

mimetypes.add_type("application/manifest+json", ".webmanifest")
mimetypes.add_type("image/svg+xml", ".svg")
mimetypes.add_type("application/javascript", ".js")
mimetypes.add_type("text/css", ".css")
mimetypes.add_type("application/json", ".json")

TEXT_EXTS = {".html", ".css", ".js", ".json", ".svg", ".webmanifest", ".txt"}
BLOCKED_PREFIXES = (".git", "scripts", "tools", "__pycache__")


class HubHandler(BaseHTTPRequestHandler):
    server_version = "TaiwanKidsHub/1.0"

    def log_message(self, fmt: str, *args) -> None:
        pass

    def _send_json(self, status: int, payload: dict, head_only: bool = False) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if not head_only:
            self.wfile.write(body)

    def _handle_static(self, head_only: bool = False) -> None:
        parsed = urlsplit(self.path)
        raw_path = unquote(parsed.path or "/")

        if raw_path == "/api/health":
            meta_path = ROOT / "data" / "sync_meta.json"
            rel_path = ROOT / "releases.json"
            meta = {}
            ver = "1.0.0"
            if meta_path.exists():
                try:
                    meta = json.loads(meta_path.read_text(encoding="utf-8"))
                except Exception:
                    meta = {}
            if rel_path.exists():
                try:
                    ver = json.loads(rel_path.read_text(encoding="utf-8")).get("current_version", ver)
                except Exception:
                    pass
            self._send_json(
                HTTPStatus.OK,
                {
                    "ok": True,
                    "service": "taiwan-kids-hub",
                    "version": ver,
                    "updated_date": meta.get("updated_date", ""),
                    "total": meta.get("total", 0),
                    "with_image_count": meta.get("with_image_count", 0),
                },
                head_only=head_only,
            )
            return

        rel = "index.html" if raw_path in ("", "/") else raw_path.lstrip("/")
        if any(rel == p or rel.startswith(p + "/") for p in BLOCKED_PREFIXES):
            self.send_error(HTTPStatus.NOT_FOUND, "Not Found")
            return

        target = (ROOT / rel).resolve()
        try:
            target.relative_to(ROOT)
        except ValueError:
            self.send_error(HTTPStatus.FORBIDDEN, "Forbidden")
            return

        if not target.is_file():
            # 有副檔名的缺失資源一律回 404（SITE-02：絕不把 index.html 當成 JS/CSS 回傳）
            if "." in Path(rel).name:
                self.send_error(HTTPStatus.NOT_FOUND, "Not Found")
                return
            target = ROOT / "index.html"

        stat = target.stat()
        mtime_ns = stat.st_mtime_ns
        ext = target.suffix.lower()
        ctype = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        if ext in (".html", ".css", ".js", ".json", ".svg", ".webmanifest"):
            ctype = f"{ctype}; charset=utf-8"

        accept_enc = self.headers.get("Accept-Encoding", "")
        use_gzip = ("gzip" in accept_enc) and (ext in TEXT_EXTS) and stat.st_size > 256

        if use_gzip:
            cache_key = str(target)
            cached = GZIP_CACHE.get(cache_key)
            if cached and cached[0] == mtime_ns:
                data = cached[1]
            else:
                data = gzip.compress(target.read_bytes(), compresslevel=6)
                GZIP_CACHE[cache_key] = (mtime_ns, data)
        else:
            data = target.read_bytes()

        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", ctype)
        if ext == ".json" or ext == ".html":
            self.send_header("Cache-Control", "no-cache")
        else:
            self.send_header("Cache-Control", "public, max-age=60")
        if use_gzip:
            self.send_header("Content-Encoding", "gzip")
            self.send_header("Vary", "Accept-Encoding")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        if not head_only:
            self.wfile.write(data)

    def do_GET(self) -> None:
        self._handle_static(head_only=False)

    def do_HEAD(self) -> None:
        self._handle_static(head_only=True)

    def do_POST(self) -> None:
        parsed = urlsplit(self.path)
        if parsed.path != "/api/sync":
            self._send_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "Unknown endpoint"})
            return

        if not SYNC_LOCK.acquire(blocking=False):
            self._send_json(HTTPStatus.CONFLICT, {"ok": False, "error": "目前正在同步中，請稍候…"})
            return
        try:
            proc = subprocess.run(
                [sys.executable, str(ROOT / "scripts" / "update_data.py")],
                cwd=str(ROOT),
                text=True,
                capture_output=True,
                timeout=240,
            )
            if proc.returncode != 0:
                self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"ok": False, "error": proc.stderr[-300:] or "同步失敗"})
                return
            chk = subprocess.run(
                [sys.executable, str(ROOT / "tools" / "check_data.py")],
                cwd=str(ROOT),
                text=True,
                capture_output=True,
                timeout=20,
            )
            if chk.returncode != 0:
                self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"ok": False, "error": chk.stderr[-300:] or "資料檢查未通過"})
                return
            GZIP_CACHE.clear()
            meta = json.loads((ROOT / "data" / "sync_meta.json").read_text(encoding="utf-8"))
            self._send_json(HTTPStatus.OK, {"ok": True, **meta})
        except Exception as exc:
            self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"ok": False, "error": str(exc)})
        finally:
            SYNC_LOCK.release()


def main() -> int:
    parser = argparse.ArgumentParser(description="台灣親子趣 Local Server")
    parser.add_argument("--host", default="127.0.0.1", help="Bind address (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8956, help="Bind port (default: 8956)")
    args = parser.parse_args()

    httpd = ThreadingHTTPServer((args.host, args.port), HubHandler)
    print(f"🧸 台灣親子趣 Local Server 啟動於 http://{args.host}:{args.port}/", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
