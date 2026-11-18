#!/usr/bin/env python3
"""台灣親子趣 — 一鍵安全推送到 GitHub (okayell/taiwan-kids-activities-hub)

認證：沿用 ~/.config/campus_deploy/github_token（chmod 600）或環境變數 GITHUB_TOKEN。
權杖只透過單次 git 指令的 http.extraHeader 傳遞，絕不寫入 .git/config，也不會印出。

用法：
  python3 tools/git_deploy.py --dry-run    # 預覽將推送的狀態，不執行 push
  python3 tools/git_deploy.py              # 執行推送到 origin main
"""

from __future__ import annotations

import argparse
import base64
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPO_URL = "https://github.com/okayell/taiwan-kids-activities-hub.git"
TOKEN_FILE = Path.home() / ".config" / "campus_deploy" / "github_token"
GIT_NAME = "okayell"
GIT_EMAIL = "okayell@users.noreply.github.com"


def load_token() -> str:
    env_tok = os.environ.get("GITHUB_TOKEN") or os.environ.get("CAMPUS_GITHUB_TOKEN")
    if env_tok and env_tok.strip():
        return env_tok.strip()
    if TOKEN_FILE.exists():
        tok = TOKEN_FILE.read_text(encoding="utf-8").strip()
        if tok:
            return tok
    raise RuntimeError(f"找不到 GitHub 權杖（{TOKEN_FILE} 或 GITHUB_TOKEN）")


def run_git(args: list[str], auth_header: str | None = None, check: bool = True) -> subprocess.CompletedProcess:
    cmd = ["git"]
    if auth_header:
        cmd += ["-c", f"http.extraHeader={auth_header}"]
    cmd += args
    return subprocess.run(cmd, cwd=str(ROOT), text=True, capture_output=True, check=check)


def main() -> int:
    parser = argparse.ArgumentParser(description="推送台灣親子趣到 GitHub")
    parser.add_argument("--dry-run", action="store_true", help="只顯示狀態，不實際推送")
    parser.add_argument("--message", "-m", default="", help="若有未提交變更時的 commit 訊息")
    args = parser.parse_args()

    # 確保本機 repo 作者為 okayell（避免帶入公司信箱）
    run_git(["config", "user.name", GIT_NAME])
    run_git(["config", "user.email", GIT_EMAIL])

    # 確保 origin remote 指向乾淨的 HTTPS URL（不含權杖）
    remotes = run_git(["remote"]).stdout.split()
    if "origin" in remotes:
        run_git(["remote", "set-url", "origin", REPO_URL])
    else:
        run_git(["remote", "add", "origin", REPO_URL])

    status = run_git(["status", "--short"]).stdout.strip()
    if args.dry_run:
        print(f"DRY-RUN: repo={REPO_URL}")
        print(f"Working tree:\n{status or '(clean)'}")
        log = run_git(["log", "-3", "--oneline"]).stdout.strip()
        print(f"Recent commits:\n{log}")
        return 0

    if status:
        run_git(["add", "-A"])
        msg = args.message or "chore: update Taiwan kids activities hub"
        run_git(["commit", "-m", msg])

    token = load_token()
    b64 = base64.b64encode(f"x-access-token:{token}".encode("utf-8")).decode("ascii")
    auth_header = f"AUTHORIZATION: basic {b64}"

    res = run_git(["push", "-u", "origin", "main"], auth_header=auth_header, check=False)
    if res.returncode != 0:
        # 避免錯誤訊息洩漏敏感資訊，只印 git stderr
        print(f"❌ git push 失敗 (exit={res.returncode}):\n{res.stderr}", file=sys.stderr)
        return res.returncode

    print("✅ 已成功推送到 https://github.com/okayell/taiwan-kids-activities-hub (branch: main)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
