#!/usr/bin/env python3
"""
台灣 12 歲以下兒童活動與親子場館自動同步爬蟲
- 零第三方套件依賴（純 Python 3 標準函式庫），可於本機與 GitHub Actions 直接執行
- 平行抓取政府公開資料 API + 整合全台 22 縣市常設親子場館庫
- 具備 Keep-Previous-On-Failure 防呆機制：任一來源異常時自動保留上一版有效資料
"""

from __future__ import annotations

import concurrent.futures
import datetime as dt
import hashlib
import html
import json
import os
import re
import ssl
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
ASSETS_DIR = ROOT / "assets"
CURATED_PATH = DATA_DIR / "curated_venues.json"
OUT_PATH = DATA_DIR / "activities.json"
META_PATH = DATA_DIR / "sync_meta.json"
DISTRICTS_JS = ASSETS_DIR / "tw_districts.js"

TZ_TW = dt.timezone(dt.timedelta(hours=8))

SSL_CTX = ssl.create_default_context()
SSL_CTX.check_hostname = False
SSL_CTX.verify_mode = ssl.CERT_NONE

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; TaiwanKidsActivitiesHub/1.0; +https://github.com/)",
    "Accept": "application/json, text/plain, */*",
}

# 縣市別名標準化
CITY_ALIASES = {
    "台北市": "臺北市",
    "台北": "臺北市",
    "臺北": "臺北市",
    "新北市": "新北市",
    "新北": "新北市",
    "基隆市": "基隆市",
    "基隆": "基隆市",
    "桃園市": "桃園市",
    "桃園縣": "桃園市",
    "桃園": "桃園市",
    "新竹市": "新竹市",
    "新竹縣": "新竹縣",
    "苗栗縣": "苗栗縣",
    "苗栗": "苗栗縣",
    "台中市": "臺中市",
    "台中": "臺中市",
    "臺中": "臺中市",
    "彰化縣": "彰化縣",
    "彰化": "彰化縣",
    "南投縣": "南投縣",
    "南投": "南投縣",
    "雲林縣": "雲林縣",
    "雲林": "雲林縣",
    "嘉義市": "嘉義市",
    "嘉義縣": "嘉義縣",
    "台南市": "臺南市",
    "台南": "臺南市",
    "臺南": "臺南市",
    "高雄市": "高雄市",
    "高雄": "高雄市",
    "屏東縣": "屏東縣",
    "屏東": "屏東縣",
    "宜蘭縣": "宜蘭縣",
    "宜蘭": "宜蘭縣",
    "花蓮縣": "花蓮縣",
    "花蓮": "花蓮縣",
    "台東縣": "臺東縣",
    "台東": "臺東縣",
    "臺東": "臺東縣",
    "澎湖縣": "澎湖縣",
    "澎湖": "澎湖縣",
    "金門縣": "金門縣",
    "金門": "金門縣",
    "連江縣": "連江縣",
    "馬祖": "連江縣",
}

# 兒童（12歲以下）相關關鍵字
KIDS_KEYWORDS = (
    "親子", "兒童", "小孩", "幼兒", "幼童", "寶寶", "學童", "國小", "幼稚園", "幼兒園",
    "繪本", "故事", "童話", "童趣", "童玩", "偶劇", "紙風車", "如果兒童", "巧虎",
    "恐龍", "動物園", "遊樂園", "遊戲場", "沙坑", "溜滑梯", "共融", "積木", "樂高",
    "手作", "DIY", "體驗營", "夏令營", "冬令營", "小農夫", "小小", "寫生", "繪畫比賽",
    "機器人", "科學實驗", "天文", "化石", "昆蟲", "蝴蝶", "海洋", "水族", "農場",
    "牧場", "觀光工廠", "市集", "嘉年華", "園遊會", "寶貝", "萌娃", "家庭日",
)

# 排除明顯成人取向活動
ADULT_EXCLUDE = (
    "品酒", "微醺", "酒吧", "調酒", "威士忌", "紅酒", "聯誼", "單身", "相親",
    "銀髮", "樂齡", "高齡", "長照", "失智", "投資理財", "股票", "期貨", "房地產",
    "限制級", "18禁", "成人限定",
)


def load_tw_districts() -> tuple[dict[str, list[str]], dict[str, list[float]]]:
    """從 assets/tw_districts.js 讀取單一真相來源的 22 縣市與 368 鄉鎮區。"""
    text = DISTRICTS_JS.read_text(encoding="utf-8")
    m_dist = re.search(r"window\.TW_DISTRICTS\s*=\s*(\{[\s\S]*?\});", text)
    m_coord = re.search(r"window\.TW_CITY_COORDS\s*=\s*(\{[\s\S]*?\});", text)
    if not m_dist or not m_coord:
        raise RuntimeError("無法解析 assets/tw_districts.js")
    return json.loads(m_dist.group(1)), json.loads(m_coord.group(1))


TW_DISTRICTS, TW_CITY_COORDS = load_tw_districts()
ALL_CITIES = list(TW_DISTRICTS.keys())

# 建立唯一鄉鎮區反查表（不含跨縣市重名如中正區、東區等）
_dist_to_cities: dict[str, list[str]] = {}
for _c, _ds in TW_DISTRICTS.items():
    for _d in _ds:
        _dist_to_cities.setdefault(_d, []).append(_c)
UNIQUE_DISTRICT_TO_CITY = {d: cs[0] for d, cs in _dist_to_cities.items() if len(cs) == 1}


def strip_html(raw: str) -> str:
    if not raw:
        return ""
    s = html.unescape(str(raw))
    s = re.sub(r"<[^>]+>", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def make_id(prefix: str, key: str) -> str:
    h = hashlib.md5(key.encode("utf-8")).hexdigest()[:12]
    return f"{prefix}-{h}"


def fetch_json(url: str, timeout: int = 12) -> Any:
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=timeout, context=SSL_CTX) as resp:
        raw = resp.read()
    text = raw.decode("utf-8-sig", errors="replace")
    return json.loads(text)


def extract_city_district(text: str, default_city: str = "") -> tuple[str, str]:
    """從地址、地點或標題抽取標準化後的 (縣市, 鄉鎮市區)。"""
    norm = (text or "").replace("台", "臺")
    city = ""
    if default_city:
        city = CITY_ALIASES.get(default_city, default_city)
        if city not in TW_DISTRICTS:
            city = ""

    if not city:
        for c in ALL_CITIES:
            if c in norm:
                city = c
                break
    if not city:
        for alias, target in CITY_ALIASES.items():
            if alias.replace("台", "臺") in norm:
                city = target
                break

    district = ""
    if city:
        for d in TW_DISTRICTS[city]:
            if d in norm:
                district = d
                break
    else:
        for d, c in UNIQUE_DISTRICT_TO_CITY.items():
            if d in norm:
                city = c
                district = d
                break

    if not city:
        return "", ""
    if not district:
        # 找不到鄉鎮區時保留空字串（前端顯示為「全區／未標示」）
        district = ""
    return city, district


def parse_date(s: str) -> str:
    if not s:
        return ""
    m = re.search(r"(\d{4})[/-](\d{1,2})[/-](\d{1,2})", str(s))
    if not m:
        return ""
    y, mo, da = int(m.group(1)), int(m.group(2)), int(m.group(3))
    try:
        return dt.date(y, mo, da).isoformat()
    except ValueError:
        return ""


def infer_age_groups(text: str, category: str) -> list[str]:
    t = text or ""
    ages: list[str] = []
    if any(k in t for k in ("0-3", "0~3", "0歲", "嬰幼", "寶寶", "爬行", "學步", "幼兒", "親子館", "托育")):
        ages.append("0-3")
    if any(k in t for k in ("3-6", "3~6", "學齡前", "幼兒園", "繪本", "故事", "沙坑", "溜滑梯", "親子", "兒童")):
        ages.append("3-6")
    if any(k in t for k in ("6-12", "6~12", "國小", "學童", "科學", "考古", "比賽", "競賽", "寫生", "營隊", "手作", "DIY", "創客", "機器人")):
        ages.append("6-12")
    if not ages:
        if category == "parent_center":
            return ["0-3", "3-6"]
        if category in ("contest", "course"):
            return ["3-6", "6-12"]
        return ["0-3", "3-6", "6-12"]
    return ages


def infer_indoor(text: str, category: str) -> bool:
    t = text or ""
    if any(k in t for k in ("室內", "美術館", "博物館", "圖書館", "文化中心", "演藝廳", "劇場", "音樂廳", "展覽館", "親子館", "教室", "書局", "書店", "百貨", "生活美學館", "科教館", "科博館", "科工館")):
        return True
    if any(k in t for k in ("公園", "戶外", "草地", "草坪", "廣場", "農場", "牧場", "步道", "生態園區", "動物園", "老街", "河濱")):
        return False
    return category in ("kids_museum", "parent_center", "course", "contest")


def infer_free(price_text: str, text: str) -> bool:
    combined = f"{price_text} {text}"
    if any(k in combined for k in ("免費", "免門票", "自由入場", "無需購票", "0元")):
        return True
    if re.search(r"\d{2,4}\s*元|購票|售票|NT\$|門票", combined):
        return False
    return False


def classify_event_category(title: str, desc: str, src_cat: str = "") -> str:
    text = f"{title} {desc}"
    if src_cat in ("11", "13", "14") or any(k in text for k in ("比賽", "競賽", "徵件", "徵選", "寫生", "錦標賽", "大賽", "徵畫")):
        return "contest"
    if any(k in text for k in ("市集", "園遊會", "嘉年華", "派對", "野餐日", "夜市", "文創市集")):
        return "market"
    if src_cat == "19" or any(k in text for k in ("課程", "工作坊", "DIY", "手作", "研習", "夏令營", "冬令營", "體驗營", "說故事", "講堂", "繪本導讀", "體驗課")):
        return "course"
    return "activity"


def is_kids_relevant(title: str, desc: str, force_kids: bool = False) -> bool:
    text = f"{title} {desc}"
    if any(bad in text for bad in ADULT_EXCLUDE):
        return False
    if force_kids:
        return True
    return any(k in text for k in KIDS_KEYWORDS)


def scrape_iculture() -> list[dict[str, Any]]:
    """抓取文化部全國藝文活動 Open Data（涵蓋全台灣 22 縣市）。"""
    today = dt.datetime.now(TZ_TW).date()
    # (category_id, force_kids)
    cats = [
        ("4", True),    # 親子活動（全收）
        ("6", False),   # 展覽
        ("2", False),   # 戲劇（兒童劇／偶劇）
        ("7", False),   # 講座（繪本／親子）
        ("1", False),   # 音樂（親子音樂會）
        ("11", False),  # 綜藝
        ("13", False),  # 競賽
        ("14", False),  # 徵選
        ("15", False),  # 其他（常含市集／嘉年華）
        ("19", False),  # 研習課程
    ]

    def _fetch_cat(cat_pair: tuple[str, bool]) -> tuple[str, bool, list[dict[str, Any]]]:
        cid, force = cat_pair
        url = f"https://cloud.culture.tw/frontsite/trans/SearchShowAction.do?method=doFindTypeJ&category={cid}"
        data = fetch_json(url, timeout=12)
        return cid, force, data if isinstance(data, list) else []

    items: list[dict[str, Any]] = []
    seen_uids: set[str] = set()

    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as ex:
        futs = [ex.submit(_fetch_cat, pair) for pair in cats]
        for fut in concurrent.futures.as_completed(futs):
            try:
                cid, force, rows = fut.result()
            except Exception as exc:
                print(f"[WARN] iCulture fetch failed: {exc}", file=sys.stderr)
                continue

            for row in rows:
                uid = str(row.get("UID") or "").strip()
                if not uid or uid in seen_uids:
                    continue
                title = strip_html(row.get("title") or "")
                if not title:
                    continue
                desc = strip_html(row.get("descriptionFilterHtml") or "")
                if not is_kids_relevant(title, desc, force_kids=force):
                    continue

                end_d = parse_date(row.get("endDate") or "")
                start_d = parse_date(row.get("startDate") or "")
                if end_d:
                    try:
                        if dt.date.fromisoformat(end_d) < today - dt.timedelta(days=1):
                            continue
                    except ValueError:
                        pass

                show_info = row.get("showInfo") or []
                first_show = show_info[0] if isinstance(show_info, list) and show_info else {}
                loc_addr = strip_html(first_show.get("location") or "")
                loc_name = strip_html(first_show.get("locationName") or "")
                price_raw = strip_html(first_show.get("price") or row.get("discountInfo") or "")
                on_sales = str(first_show.get("onSales") or "").upper()

                city, district = extract_city_district(f"{loc_addr} {loc_name} {title}")
                if not city:
                    continue

                lat_raw = first_show.get("latitude")
                lng_raw = first_show.get("longitude")
                lat, lng, exact_coords = None, None, False
                try:
                    if lat_raw and lng_raw:
                        lf, gf = float(lat_raw), float(lng_raw)
                        if 21.5 <= lf <= 26.5 and 118.0 <= gf <= 122.5:
                            lat, lng, exact_coords = lf, gf, True
                except (ValueError, TypeError):
                    pass
                if lat is None or lng is None:
                    cc = TW_CITY_COORDS.get(city, [25.033, 121.565, 11])
                    lat, lng = cc[0], cc[1]

                cat = classify_event_category(title, desc, src_cat=cid)
                is_free = (on_sales == "N") or infer_free(price_raw, desc)
                price_text = price_raw or ("免費參加（詳見官網）" if is_free else "依主辦單位公告")
                url = (row.get("sourceWebPromote") or row.get("webSales") or "").strip()
                if url and not url.startswith(("http://", "https://")):
                    url = "https://" + url
                if not url:
                    url = "https://cloud.culture.tw/"

                summary = desc[:140] + ("…" if len(desc) > 140 else "") if desc else f"{loc_name or city} 舉辦之親子藝文活動。"
                hours_text = f"{start_d or '近期'} ～ {end_d or '依官網公告'}"
                if loc_name and loc_name not in loc_addr:
                    address = f"{loc_addr}（{loc_name}）" if loc_addr else loc_name
                else:
                    address = loc_addr or loc_name or f"{city}{district}"

                seen_uids.add(uid)
                items.append({
                    "id": make_id("iculture", uid),
                    "title": title,
                    "kind": "event",
                    "category": cat,
                    "city": city,
                    "district": district,
                    "address": address,
                    "lat": round(lat, 5),
                    "lng": round(lng, 5),
                    "exact_coords": exact_coords,
                    "age_groups": infer_age_groups(f"{title} {desc}", cat),
                    "is_indoor": infer_indoor(f"{loc_name} {loc_addr} {title} {desc}", cat),
                    "is_free": is_free,
                    "price_text": price_text[:90],
                    "hours": hours_text,
                    "start_date": start_d,
                    "end_date": end_d,
                    "is_recurring_weekend": False,
                    "summary": summary,
                    "url": url,
                    "source": "文化部全國藝文活動",
                })
    return items


def scrape_taipei_events() -> list[dict[str, Any]]:
    """抓取臺北市觀光傳播局最新活動與節慶行事曆。"""
    today = dt.datetime.now(TZ_TW).date()
    urls = [
        "https://www.travel.taipei/open-api/zh-tw/Events/Activity?page=1",
        "https://www.travel.taipei/open-api/zh-tw/Events/Calendar?page=1",
    ]
    items: list[dict[str, Any]] = []
    seen: set[str] = set()

    for url in urls:
        try:
            payload = fetch_json(url, timeout=12)
        except Exception as exc:
            print(f"[WARN] Taipei events fetch failed ({url}): {exc}", file=sys.stderr)
            continue
        rows = payload.get("data") if isinstance(payload, dict) else []
        for row in rows or []:
            title = strip_html(row.get("title") or "")
            if not title or title in seen:
                continue
            desc = strip_html(row.get("description") or "")
            if not is_kids_relevant(title, desc, force_kids=False):
                continue

            start_d = parse_date(row.get("begin") or "")
            end_d = parse_date(row.get("end") or "")
            if end_d:
                try:
                    if dt.date.fromisoformat(end_d) < today - dt.timedelta(days=1):
                        continue
                except ValueError:
                    pass

            addr = strip_html(row.get("address") or "")
            dist_raw = strip_html(row.get("distric") or "")
            city, district = extract_city_district(f"臺北市 {dist_raw} {addr} {title}", default_city="臺北市")
            lat, lng, exact_coords = 25.033, 121.5654, False
            try:
                if row.get("nlat") and row.get("elong"):
                    lf, gf = float(row["nlat"]), float(row["elong"])
                    if 24.8 <= lf <= 25.3 and 121.3 <= gf <= 121.8:
                        lat, lng, exact_coords = lf, gf, True
            except (ValueError, TypeError):
                pass

            cat = classify_event_category(title, desc)
            ticket = strip_html(row.get("ticket") or "")
            is_free = infer_free(ticket, desc)
            seen.add(title)
            items.append({
                "id": make_id("tpe-evt", f"{row.get('id', '')}-{title}"),
                "title": title,
                "kind": "event",
                "category": cat,
                "city": city or "臺北市",
                "district": district,
                "address": addr or f"臺北市{district}",
                "lat": round(lat, 5),
                "lng": round(lng, 5),
                "exact_coords": exact_coords,
                "age_groups": infer_age_groups(f"{title} {desc}", cat),
                "is_indoor": infer_indoor(f"{addr} {title} {desc}", cat),
                "is_free": is_free,
                "price_text": (ticket[:80] if ticket else ("免費參加" if is_free else "詳見活動官網")),
                "hours": f"{start_d or '近期'} ～ {end_d or '依官網公告'}",
                "start_date": start_d,
                "end_date": end_d,
                "is_recurring_weekend": False,
                "summary": (desc[:140] + ("…" if len(desc) > 140 else "")) if desc else "臺北市官方精選親子活動。",
                "url": row.get("url") or "https://www.travel.taipei/",
                "source": "臺北旅遊網 Open API",
            })
    return items


def scrape_taipei_venues() -> list[dict[str, Any]]:
    """從臺北旅遊網 Open API 抓取親子共遊、公園、博物館與兒童場館（抓取前 8 頁涵蓋主要點位）。"""
    items: list[dict[str, Any]] = []
    pages = list(range(1, 9))

    def _fetch_page(p: int) -> list[dict[str, Any]]:
        url = f"https://www.travel.taipei/open-api/zh-tw/Attractions/All?page={p}"
        payload = fetch_json(url, timeout=12)
        return payload.get("data") or [] if isinstance(payload, dict) else []

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
        for rows in ex.map(_fetch_page, pages):
            for row in rows:
                name = strip_html(row.get("name") or "")
                intro = strip_html(row.get("introduction") or "")
                cats = [c.get("name", "") for c in (row.get("category") or []) if isinstance(c, dict)]
                targets = [t.get("name", "") for t in (row.get("target") or []) if isinstance(t, dict)]
                tag_str = " ".join(cats + targets)

                is_family = (
                    "親子共遊" in tag_str
                    or "公園" in name
                    or "兒童" in name
                    or "博物館" in name
                    or "故事館" in name
                    or "生態" in name
                )
                if not is_family or any(bad in f"{name} {intro}" for bad in ADULT_EXCLUDE):
                    continue

                # 決定常設場館分類
                if any(k in name for k in ("樂園", "遊樂")):
                    cat = "amusement"
                elif any(k in name for k in ("親子館", "育兒")):
                    cat = "parent_center"
                elif any(k in name for k in ("公園", "河濱", "親水", "遊戲場", "森林")):
                    cat = "park"
                elif any(k in name for k in ("博物館", "美術館", "科學", "天文", "動物園", "故事館", "紀念館", "探索館", "展覽館")):
                    cat = "kids_museum"
                else:
                    continue

                addr = strip_html(row.get("address") or "")
                dist_raw = strip_html(row.get("distric") or "")
                city, district = extract_city_district(f"臺北市 {dist_raw} {addr}", default_city="臺北市")
                lat, lng, exact_coords = 25.033, 121.5654, False
                try:
                    lf, gf = float(row.get("nlat") or 0), float(row.get("elong") or 0)
                    if 24.8 <= lf <= 25.3 and 121.3 <= gf <= 121.8:
                        lat, lng, exact_coords = lf, gf, True
                except (ValueError, TypeError):
                    pass

                ticket = strip_html(row.get("ticket") or "")
                is_free = (cat == "park") or infer_free(ticket, intro)
                items.append({
                    "id": make_id("tpe-venue", str(row.get("id") or name)),
                    "title": name,
                    "kind": "venue",
                    "category": cat,
                    "city": city or "臺北市",
                    "district": district,
                    "address": addr or f"臺北市{district}",
                    "lat": round(lat, 5),
                    "lng": round(lng, 5),
                    "exact_coords": exact_coords,
                    "age_groups": infer_age_groups(f"{name} {intro}", cat),
                    "is_indoor": infer_indoor(f"{name} {intro}", cat),
                    "is_free": is_free,
                    "price_text": ticket[:80] if ticket else ("免費開放" if is_free else "依場館公告"),
                    "hours": strip_html(row.get("open_time") or "")[:90] or "依場館官網公告",
                    "summary": (intro[:140] + ("…" if len(intro) > 140 else "")) if intro else "臺北市推薦親子共遊景點。",
                    "url": row.get("official_site") or row.get("url") or "https://www.travel.taipei/",
                    "source": "臺北旅遊網 Open API",
                })
    return items


def scrape_tainan_venues() -> list[dict[str, Any]]:
    """從臺南市政府觀光旅遊局開放資料抓取親子場館、公園、博物館與觀光工廠。"""
    url = "https://www.twtainan.net/data/attractions_zh-tw.json"
    rows = fetch_json(url, timeout=15)
    if not isinstance(rows, list):
        return []
    items: list[dict[str, Any]] = []
    for row in rows:
        name = strip_html(row.get("name") or "")
        summary = strip_html(row.get("summary") or row.get("introduction") or "")
        cats = row.get("category") or []
        cat_str = " ".join(str(c) for c in cats) if isinstance(cats, list) else str(cats)
        text = f"{name} {summary} {cat_str}"

        if any(bad in text for bad in ADULT_EXCLUDE):
            continue
        if not any(k in text for k in ("親子", "兒童", "公園", "博物館", "化石", "科學", "農場", "牧場", "觀光工廠", "動物園", "生態", "遊戲場")):
            continue

        if any(k in name for k in ("樂園", "動物園", "農場", "牧場")):
            cat = "amusement"
        elif any(k in name for k in ("公園", "親水", "森林", "運動公園", "遊戲場")):
            cat = "park"
        elif any(k in name for k in ("博物館", "科學館", "化石", "故事館", "觀光工廠", "紀念館", "美術館", "天文")):
            cat = "kids_museum"
        else:
            continue

        addr = strip_html(row.get("address") or "")
        dist_raw = strip_html(row.get("district") or "")
        city, district = extract_city_district(f"臺南市 {dist_raw} {addr}", default_city="臺南市")
        lat, lng, exact_coords = 22.9999, 120.2269, False
        try:
            lf, gf = float(row.get("lat") or 0), float(row.get("long") or 0)
            if 22.8 <= lf <= 23.45 and 119.9 <= gf <= 120.7:
                lat, lng, exact_coords = lf, gf, True
        except (ValueError, TypeError):
            pass

        is_free = (cat == "park") or infer_free("", summary)
        items.append({
            "id": make_id("tnn-venue", str(row.get("id") or name)),
            "title": name,
            "kind": "venue",
            "category": cat,
            "city": city or "臺南市",
            "district": district,
            "address": addr or f"臺南市{district}",
            "lat": round(lat, 5),
            "lng": round(lng, 5),
            "exact_coords": exact_coords,
            "age_groups": infer_age_groups(text, cat),
            "is_indoor": infer_indoor(text, cat),
            "is_free": is_free,
            "price_text": "免費開放" if is_free else "依場館公告",
            "hours": strip_html(row.get("open_time") or "")[:90] or "依場館官網公告",
            "summary": (summary[:140] + ("…" if len(summary) > 140 else "")) if summary else "臺南市推薦親子場館。",
            "url": f"https://www.twtainan.net/zh-tw/attractions/detail/{row.get('id')}" if row.get("id") else "https://www.twtainan.net/",
            "source": "臺南旅遊網 Open Data",
        })
    return items


def load_previous_by_source() -> dict[str, list[dict[str, Any]]]:
    if not OUT_PATH.exists():
        return {}
    try:
        prev = json.loads(OUT_PATH.read_text(encoding="utf-8"))
        by_src: dict[str, list[dict[str, Any]]] = {}
        for item in prev.get("items", []):
            by_src.setdefault(item.get("source", "unknown"), []).append(item)
        return by_src
    except Exception:
        return {}


def normalize_title_key(title: str, city: str) -> str:
    clean = re.sub(r"[\s\-－—（）()【】\[\]・‧·]", "", title or "").lower()
    clean = clean.replace("台", "臺")
    return f"{city}:{clean}"


def main() -> int:
    now_tw = dt.datetime.now(TZ_TW)
    prev_by_src = load_previous_by_source()

    # 1. 讀取全台 22 縣市常設精選場館基準庫
    curated: list[dict[str, Any]] = json.loads(CURATED_PATH.read_text(encoding="utf-8"))
    for item in curated:
        item.setdefault("exact_coords", True)
        item.setdefault("start_date", "")
        item.setdefault("end_date", "")
        item.setdefault("is_recurring_weekend", False)

    # 2. 平行執行所有即時公開資料爬蟲
    sources = {
        "文化部全國藝文活動": (scrape_iculture, 15),
        "臺北旅遊網 Open API (活動)": (scrape_taipei_events, 1),
        "臺北旅遊網 Open API (場館)": (scrape_taipei_venues, 10),
        "臺南旅遊網 Open Data": (scrape_tainan_venues, 10),
    }

    source_results: dict[str, list[dict[str, Any]]] = {}
    source_health: list[dict[str, Any]] = []

    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as ex:
        fut_map = {ex.submit(fn): (name, min_guard) for name, (fn, min_guard) in sources.items()}
        for fut in concurrent.futures.as_completed(fut_map):
            name, min_guard = fut_map[fut]
            prev_key = "臺北旅遊網 Open API" if name.startswith("臺北旅遊網") else name
            prev_items = prev_by_src.get(prev_key, [])
            if name == "臺北旅遊網 Open API (活動)":
                prev_items = [x for x in prev_items if x.get("kind") == "event"]
            elif name == "臺北旅遊網 Open API (場館)":
                prev_items = [x for x in prev_items if x.get("kind") == "venue"]

            try:
                fetched = fut.result()
                threshold = max(min_guard, int(len(prev_items) * 0.6)) if prev_items else 0
                if prev_items and len(fetched) < threshold:
                    print(f"GUARD: {name} fetched {len(fetched)} < threshold {threshold}, keeping {len(prev_items)} previous items.")
                    source_results[name] = prev_items
                    source_health.append({
                        "source": name,
                        "status": "guarded_fallback",
                        "fetched": len(fetched),
                        "used": len(prev_items),
                    })
                else:
                    source_results[name] = fetched
                    source_health.append({
                        "source": name,
                        "status": "ok",
                        "fetched": len(fetched),
                        "used": len(fetched),
                    })
            except Exception as exc:
                print(f"GUARD: {name} error ({exc}), keeping {len(prev_items)} previous items.", file=sys.stderr)
                source_results[name] = prev_items
                source_health.append({
                    "source": name,
                    "status": "error_fallback",
                    "error": str(exc)[:120],
                    "used": len(prev_items),
                })

    # 3. 合併去重（精選常設庫優先，其次為即時活動與即時場館）
    merged: list[dict[str, Any]] = []
    seen_keys: set[str] = set()
    seen_ids: set[str] = set()

    ordered_batches = [
        curated,
        source_results.get("文化部全國藝文活動", []),
        source_results.get("臺北旅遊網 Open API (活動)", []),
        source_results.get("臺北旅遊網 Open API (場館)", []),
        source_results.get("臺南旅遊網 Open Data", []),
    ]

    for batch in ordered_batches:
        for item in batch:
            cid = item.get("id")
            city = item.get("city", "")
            title = item.get("title", "")
            if not cid or not city or city not in TW_DISTRICTS or not title:
                continue
            key = normalize_title_key(title, city)
            if cid in seen_ids or key in seen_keys:
                continue
            seen_ids.add(cid)
            seen_keys.add(key)
            merged.append(item)

    # 統計資料
    cat_counts: dict[str, int] = {}
    city_counts: dict[str, int] = {}
    kind_counts = {"venue": 0, "event": 0}
    for it in merged:
        cat_counts[it["category"]] = cat_counts.get(it["category"], 0) + 1
        city_counts[it["city"]] = city_counts.get(it["city"], 0) + 1
        kind_counts[it["kind"]] = kind_counts.get(it["kind"], 0) + 1

    payload = {
        "updated_at": now_tw.isoformat(timespec="seconds"),
        "updated_date": now_tw.strftime("%Y-%m-%d %H:%M (台灣時間)"),
        "total": len(merged),
        "kind_counts": kind_counts,
        "category_counts": cat_counts,
        "city_counts": city_counts,
        "sources": source_health,
        "items": merged,
    }

    tmp_out = OUT_PATH.with_suffix(".json.tmp")
    tmp_out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp_out.replace(OUT_PATH)

    meta = {
        "updated_at": payload["updated_at"],
        "updated_date": payload["updated_date"],
        "total": payload["total"],
        "kind_counts": kind_counts,
        "category_counts": cat_counts,
        "cities_covered": len(city_counts),
        "sources": source_health,
    }
    META_PATH.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    print(
        f"✅ Synced {len(merged)} items across {len(city_counts)}/22 cities "
        f"(venues={kind_counts['venue']}, events={kind_counts['event']})."
    )
    print(f"   Categories: {json.dumps(cat_counts, ensure_ascii=False)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
