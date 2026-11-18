#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APPLY=0
if [[ "${1:-}" == "--apply" ]]; then
  APPLY=1
fi

echo "==> 1. Python 語法檢查"
python3 -m py_compile "$ROOT/server.py" "$ROOT/scripts/update_data.py" "$ROOT/tools/check_data.py" "$ROOT/tools/git_deploy.py"

echo "==> 2. JavaScript 語法檢查"
if command -v node >/dev/null 2>&1; then
  node --check "$ROOT/assets/tw_districts.js"
  node --check "$ROOT/assets/app.js"
else
  echo "   (略過：找不到 node)"
fi

echo "==> 3. 資料品質 Harness 檢查"
python3 "$ROOT/tools/check_data.py"

echo "==> 4. 本機 server.py 全資源 HTTP 冒煙測試"
python3 - <<PYEOF
import json, os, re, socket, subprocess, sys, time, urllib.error, urllib.request

root = "$ROOT"
with socket.socket() as s:
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]

proc = subprocess.Popen(
    [sys.executable, os.path.join(root, "server.py"), "--host", "127.0.0.1", "--port", str(port)],
    stdout=subprocess.DEVNULL,
    stderr=subprocess.PIPE,
)
try:
    base = f"http://127.0.0.1:{port}"
    for _ in range(30):
        try:
            with urllib.request.urlopen(f"{base}/api/health", timeout=2) as r:
                h = json.loads(r.read().decode("utf-8"))
                assert h.get("ok") is True
                break
        except Exception:
            time.sleep(0.1)
    else:
        raise RuntimeError("server.py failed to start")

    # GET / and dynamically extract all local href/src sub-resources (SITE-02)
    with urllib.request.urlopen(f"{base}/", timeout=5) as r:
        assert r.status == 200
        html = r.read().decode("utf-8")

    local_refs = set(re.findall(r'(?:href|src)=["\']([^"\']+)["\']', html))
    check_paths = {"/", "/data/activities.json", "/favicon.ico", "/site.webmanifest"}
    for ref in local_refs:
        if not ref.startswith(("http://", "https://", "//", "#", "data:", "mailto:")):
            check_paths.add("/" + ref.lstrip("/"))

    for path in sorted(check_paths):
        req = urllib.request.Request(f"{base}{path}", headers={"Accept-Encoding": "gzip"})
        with urllib.request.urlopen(req, timeout=5) as r:
            assert r.status == 200, f"{path} status={r.status}"
            body = r.read()
            assert len(body) > 50, f"{path} body too small"

    # HEAD check & 404 check for missing asset with extension (SITE-01 & SITE-02)
    head_req = urllib.request.Request(f"{base}/", method="HEAD")
    with urllib.request.urlopen(head_req, timeout=5) as r:
        assert r.status == 200

    try:
        urllib.request.urlopen(f"{base}/assets/does_not_exist.js", timeout=5)
        raise AssertionError("Expected 404 for missing .js")
    except urllib.error.HTTPError as e:
        assert e.code == 404

    print(f"✅ server.py HTTP 冒煙測試通過（port={port}，共驗證 {len(check_paths)} 項資源 + HEAD + 404 防護）")
finally:
    proc.terminate()
    proc.wait(timeout=5)
PYEOF

if [[ "$APPLY" == "1" ]]; then
  echo "==> 5. 套用並重啟常駐 systemd 服務 (taiwan-kids-hub-backend + taiwan-kids-hub)"
  systemctl --user daemon-reload
  systemctl --user enable --now taiwan-kids-hub-backend.service taiwan-kids-hub.service
  systemctl --user restart taiwan-kids-hub-backend.service taiwan-kids-hub.service
  sleep 1
  systemctl --user is-active --quiet taiwan-kids-hub-backend.service
  systemctl --user is-active --quiet taiwan-kids-hub.service
  echo "✅ 常駐 Local 服務已啟動：Backend(:8956) + Autobahn Proxy(:8957)"
fi

echo "✅ 全部驗證通過！"
