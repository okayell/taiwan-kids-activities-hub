#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
echo "==> 1. Python 語法檢查"
python3 -m py_compile "$ROOT/scripts/update_data.py" "$ROOT/tools/check_data.py"

echo "==> 2. JavaScript 語法檢查"
if command -v node >/dev/null 2>&1; then
  node --check "$ROOT/assets/tw_districts.js"
  node --check "$ROOT/assets/app.js"
else
  echo "   (略過：找不到 node)"
fi

echo "==> 3. 資料品質 Harness 檢查"
python3 "$ROOT/tools/check_data.py"

echo "==> 4. 本機靜態伺服器 HTTP 冒煙測試"
python3 - <<PYEOF
import http.server, socketserver, threading, urllib.request, json, os
os.chdir("$ROOT")
class QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, format, *args):
        pass
httpd = socketserver.TCPServer(("127.0.0.1", 0), QuietHandler)
port = httpd.server_address[1]
t = threading.Thread(target=httpd.serve_forever, daemon=True)
t.start()
try:
    for path in [
        "/",
        "/favicon.ico",
        "/site.webmanifest",
        "/assets/favicon.svg",
        "/assets/favicon.png",
        "/assets/apple-touch-icon.png",
        "/assets/icon-192.png",
        "/assets/icon-512.png",
        "/assets/styles.css",
        "/assets/tw_districts.js",
        "/assets/app.js",
        "/data/activities.json",
    ]:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=5) as r:
            assert r.status == 200, f"{path} status={r.status}"
            body = r.read()
            assert len(body) > 100, f"{path} body too small"
            if path == "/data/activities.json":
                d = json.loads(body.decode("utf-8"))
                assert d["total"] >= 100
            elif path == "/site.webmanifest":
                m = json.loads(body.decode("utf-8"))
                assert len(m.get("icons", [])) >= 2
    print(f"✅ HTTP 冒煙測試通過（port={port}，所有靜態與 JSON 資源皆 200 OK）")
finally:
    httpd.shutdown()
    httpd.server_close()
PYEOF

echo "✅ 全部驗證通過！"
