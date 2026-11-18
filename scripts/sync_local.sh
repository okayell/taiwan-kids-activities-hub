#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
echo "🔄 [1/2] 同步全台灣兒童活動與場館最新資料…"
python3 "$ROOT/scripts/update_data.py"

echo "🔍 [2/2] 執行資料品質檢查…"
python3 "$ROOT/tools/check_data.py"

echo "🎉 本機資料同步與驗證完成！"
