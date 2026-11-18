#!/usr/bin/env python3
"""台灣親子趣 — 版號與更新紀錄管理工具 (releases.json + assets/releases_data.js)

規則：
- 預設不自動進版；僅在使用者明確要求發佈時執行 publish。
- 預設進 minor（新功能／版面調整）或 patch（修正／微調），除非使用者明確指定才進 major。
- 每週排程更新 data/activities.json 屬於內容同步，不進版號。

用法：
  python3 tools/release_mgr.py status
  python3 tools/release_mgr.py sync-js
  python3 tools/release_mgr.py publish --part minor --title "標題" --changes "項目1" "項目2" [--push]
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RELEASES_JSON = ROOT / "releases.json"
RELEASES_JS = ROOT / "assets" / "releases_data.js"
TZ_TW = dt.timezone(dt.timedelta(hours=8))

TRACKED_FILES = [
    "index.html",
    "assets/styles.css",
    "assets/tw_districts.js",
    "assets/app.js",
    "scripts/update_data.py",
    "server.py",
]


def now_tw_str() -> str:
    return dt.datetime.now(TZ_TW).strftime("%Y-%m-%d %H:%M")


def compute_fingerprint() -> tuple[str, list[str]]:
    h = hashlib.sha256()
    present: list[str] = []
    for rel in TRACKED_FILES:
        p = ROOT / rel
        if p.is_file():
            present.append(rel)
            h.update(rel.encode("utf-8") + b"\0" + p.read_bytes() + b"\0")
    return h.hexdigest()[:16], present


def write_releases_js(payload: dict) -> None:
    js = "// 自動由 tools/release_mgr.py 產生，請勿手動編輯\nwindow.TW_KIDS_RELEASES = " + json.dumps(payload, ensure_ascii=False, indent=2) + ";\n"
    RELEASES_JS.write_text(js, encoding="utf-8")


def bump_semver(ver: str, part: str) -> str:
    nums = [int(x) for x in ver.lstrip("v").split(".")]
    while len(nums) < 3:
        nums.append(0)
    if part == "major":
        return f"{nums[0] + 1}.0.0"
    if part == "minor":
        return f"{nums[0]}.{nums[1] + 1}.0"
    return f"{nums[0]}.{nums[1]}.{nums[2] + 1}"


def load_payload() -> dict:
    if RELEASES_JSON.is_file():
        return json.loads(RELEASES_JSON.read_text(encoding="utf-8"))
    fp, _ = compute_fingerprint()
    return {
        "current_version": "1.0.0",
        "last_fingerprint": fp,
        "releases": [],
    }


def save_payload(payload: dict) -> None:
    RELEASES_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    write_releases_js(payload)


def main() -> int:
    parser = argparse.ArgumentParser(description="台灣親子趣版號管理")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("status", help="查看目前版號與是否有未發佈變更")
    sub.add_parser("sync-js", help="從 releases.json 重新同步產生 assets/releases_data.js")

    pub = sub.add_parser("publish", help="進版並寫入更新紀錄")
    pub.add_argument("--part", choices=["patch", "minor", "major"], default="minor")
    pub.add_argument("--version", default="", help="明確指定版號（例如 1.2.0）")
    pub.add_argument("--title", required=True, help="繁體中文版本標題")
    pub.add_argument("--changes", nargs="+", required=True, help="使用者看得懂的繁體中文更新重點")
    pub.add_argument("--push", action="store_true", help="進版後一併呼叫 tools/git_deploy.py 推上 GitHub")

    args = parser.parse_args()
    payload = load_payload()
    fp, files = compute_fingerprint()

    if args.cmd == "status":
        dirty = fp != payload.get("last_fingerprint")
        print(f"Current version : v{payload.get('current_version')}")
        print(f"Code fingerprint: {fp} ({'有未發佈修改' if dirty else '與最新發佈一致'})")
        print(f"Tracked files   : {', '.join(files)}")
        return 0

    if args.cmd == "sync-js":
        payload["last_fingerprint"] = fp
        save_payload(payload)
        print(f"✅ 已同步 assets/releases_data.js (v{payload['current_version']})")
        return 0

    if args.cmd == "publish":
        new_ver = args.version.lstrip("v") if args.version else bump_semver(payload["current_version"], args.part)
        entry = {
            "version": new_ver,
            "date": now_tw_str(),
            "title": args.title,
            "changes": args.changes,
        }
        payload["current_version"] = new_ver
        payload["last_fingerprint"] = fp
        payload["releases"] = [entry] + [r for r in payload.get("releases", []) if r.get("version") != new_ver]
        save_payload(payload)
        print(f"✅ 已發佈 v{new_ver}：{args.title}")

        if args.push:
            res = subprocess.run(
                [sys.executable, str(ROOT / "tools" / "git_deploy.py"), "--message", f"release v{new_ver}: {args.title}"],
                cwd=str(ROOT),
            )
            return res.returncode
        return 0

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
