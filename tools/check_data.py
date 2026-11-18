#!/usr/bin/env python3
"""
資料品質 Harness（供本機驗證與 GitHub Actions CI 使用）
檢查項目：
1. 總筆數門檻與 ID 唯一性
2. 必要欄位與經緯度合法範圍
3. 全台灣 22 縣市覆蓋率與鄉鎮市區合法性（比對 assets/tw_districts.js）
4. 8 大分類（遊樂園／兒童館／親子館／公園／活動／比賽／市集／課程）皆不為空
5. 資料新鮮度（不可超過 8 天）
"""

from __future__ import annotations

import datetime as dt
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_PATH = ROOT / "data" / "activities.json"
DISTRICTS_JS = ROOT / "assets" / "tw_districts.js"

REQUIRED_CATEGORIES = {
    "amusement",
    "kids_museum",
    "parent_center",
    "park",
    "festival",
    "activity",
    "contest",
    "market",
    "course",
}
REQUIRED_FIELDS = ("id", "title", "kind", "category", "city", "lat", "lng", "summary")


def load_districts() -> dict[str, list[str]]:
    text = DISTRICTS_JS.read_text(encoding="utf-8")
    m = re.search(r"window\.TW_DISTRICTS\s*=\s*(\{[\s\S]*?\});", text)
    if not m:
        raise RuntimeError("無法解析 assets/tw_districts.js")
    return json.loads(m.group(1))


def validate_payload(payload: dict, tw_districts: dict[str, list[str]]) -> list[str]:
    errors: list[str] = []
    items = payload.get("items")
    if not isinstance(items, list):
        return ["activities.json 缺少 items 陣列"]

    if len(items) < 800:
        errors.append(f"總筆數過低：{len(items)} < 800")

    # 新鮮度檢查
    updated_at = payload.get("updated_at", "")
    try:
        ts = dt.datetime.fromisoformat(updated_at)
        age_days = (dt.datetime.now(ts.tzinfo) - ts).total_seconds() / 86400
        if age_days > 8:
            errors.append(f"資料已過期 {age_days:.1f} 天（上限 8 天）")
    except Exception:
        errors.append(f"updated_at 格式不合法：{updated_at!r}")

    seen_ids: set[str] = set()
    cities_seen: set[str] = set()
    cats_seen: set[str] = set()
    with_img = 0

    for idx, it in enumerate(items):
        for field in REQUIRED_FIELDS:
            if field not in it or it[field] in (None, ""):
                errors.append(f"item[{idx}] ({it.get('id')}) 缺少必要欄位 {field}")

        img = it.get("image_url") or ""
        if img:
            with_img += 1
            if not img.startswith(("http://", "https://")) or " " in img or "culture.twhttp" in img:
                errors.append(f"item[{idx}] ({it.get('id')}) image_url 格式異常：{img[:80]!r}")

        iid = it.get("id")
        if iid in seen_ids:
            errors.append(f"重複 ID：{iid}")
        seen_ids.add(iid)

        city = it.get("city", "")
        district = it.get("district", "")
        if city not in tw_districts:
            errors.append(f"item[{idx}] ({iid}) 不合法縣市：{city!r}")
        else:
            cities_seen.add(city)
            if district and district not in tw_districts[city]:
                errors.append(f"item[{idx}] ({iid}) 鄉鎮區 {district!r} 不屬於 {city}")

        cat = it.get("category", "")
        if cat not in REQUIRED_CATEGORIES:
            errors.append(f"item[{idx}] ({iid}) 不合法分類：{cat!r}")
        else:
            cats_seen.add(cat)

        lat, lng = it.get("lat"), it.get("lng")
        if not (isinstance(lat, (int, float)) and 21.5 <= lat <= 26.5):
            errors.append(f"item[{idx}] ({iid}) 緯度超出台灣範圍：{lat}")
        if not (isinstance(lng, (int, float)) and 118.0 <= lng <= 122.5):
            errors.append(f"item[{idx}] ({iid}) 經度超出台灣範圍：{lng}")

    missing_cities = set(tw_districts.keys()) - cities_seen
    if missing_cities:
        errors.append(f"缺少縣市資料：{sorted(missing_cities)}")

    missing_cats = REQUIRED_CATEGORIES - cats_seen
    if missing_cats:
        errors.append(f"以下分類筆數為 0：{sorted(missing_cats)}")

    if with_img < 600:
        errors.append(f"具真實照片之筆數過低：{with_img} < 600")

    # 回歸防護：確保各區指標室內兒童樂園（例如基隆市中正室內兒童樂園等）必在清單中且具鄉鎮區與真圖
    keelung_zz = [
        it for it in items
        if it.get("city") == "基隆市" and "中正室內兒童樂園" in it.get("title", "")
    ]
    if not keelung_zz:
        errors.append("漏抓回歸失敗：缺少「基隆市中正室內兒童樂園」")
    else:
        z = keelung_zz[0]
        if z.get("district") != "中正區":
            errors.append(f"基隆市中正室內兒童樂園 鄉鎮區錯誤：{z.get('district')!r}")
        if not z.get("image_url"):
            errors.append("基隆市中正室內兒童樂園 缺少真實照片 image_url")

    # 回歸防護：確保全齡大型節慶（如新北歡樂耶誕城、燈會、煙火秀等）正常收錄且數量充足
    festivals = [it for it in items if it.get("category") == "festival"]
    if len(festivals) < 20:
        errors.append(f"大型節慶 (festival) 筆數過低：{len(festivals)} < 20")
    if not any("新北歡樂耶誕城" in it.get("title", "") for it in festivals):
        errors.append("漏抓回歸失敗：大型節慶缺少「新北歡樂耶誕城」")

    return errors


def main() -> int:
    if not DATA_PATH.exists():
        print(f"❌ 找不到 {DATA_PATH}", file=sys.stderr)
        return 1
    tw_districts = load_districts()
    payload = json.loads(DATA_PATH.read_text(encoding="utf-8"))
    errors = validate_payload(payload, tw_districts)
    if errors:
        print(f"❌ 資料品質檢查失敗（共 {len(errors)} 項錯誤）：", file=sys.stderr)
        for e in errors[:20]:
            print(f"  - {e}", file=sys.stderr)
        return 1

    with_img = sum(1 for it in payload["items"] if it.get("image_url"))
    print(
        f"✅ check_data.py PASS — {len(payload['items'])} 筆資料（含真實圖片 {with_img} 筆）、"
        f"22/22 縣市全覆蓋、9/9 分類完整、更新時間 {payload.get('updated_date')}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
