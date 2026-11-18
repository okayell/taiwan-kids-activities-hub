#!/usr/bin/env python3
"""
台灣 12 歲以下兒童活動與親子場館自動同步爬蟲
- 零第三方套件依賴（純 Python 3 標準函式庫），可於本機與 GitHub Actions 直接執行
- 平行抓取政府公開資料 API + 整合全台 22 縣市常設親子場館庫
- 自動擷取真實活動海報與場館實景照片（API imageUrl / images + 官方頁面 og:image + 臺南景點圖庫），無真實照片時保持空字串不塞假圖
- 具備 Keep-Previous-On-Failure 防呆機制：任一來源異常時自動保留上一版有效資料
"""

from __future__ import annotations

import concurrent.futures
import datetime as dt
import hashlib
import html
import json
import re
import ssl
import sys
import urllib.error
import urllib.parse
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
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/json, text/html, text/plain, */*",
}

# 過濾網站通用預設分享圖／Logo（只保留真實活動海報或場館照片）
BAD_IMAGE_RE = re.compile(
    r"(social-share|fbsharelogo|default[-_]?share|default[-_]?img|no[-_]?image|"
    r"logo\.svg|\.svg($|\?)|favicon|spacer\.gif|blank\.|cloud\.culture\.tw/frontsite)",
    re.I,
)

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


def clean_image_url(raw_url: str, base_url: str = "") -> str:
    """清理並驗證圖片網址（修正文化部雙重前綴、編碼空白與中文字、排除預設假 Logo）。"""
    if not raw_url:
        return ""
    u = html.unescape(str(raw_url)).strip()
    if not u:
        return ""
    # 修正文化部 iCulture 常見的 https://cloud.culture.twhttps://... 雙重前綴
    u = re.sub(r"^https?://cloud\.culture\.tw(?=https?://)", "", u, flags=re.I)
    if base_url and not u.startswith(("http://", "https://", "//")):
        u = urllib.parse.urljoin(base_url, u)
    elif u.startswith("//"):
        u = "https:" + u

    # 修正 https://domain.com/domain.com/... 錯誤相對路徑
    m_dup = re.match(r"^(https?://([^/]+))/\2(/.*)$", u, flags=re.I)
    if m_dup:
        u = m_dup.group(1) + m_dup.group(3)

    if not u.startswith(("http://", "https://")):
        return ""
    if BAD_IMAGE_RE.search(u):
        return ""

    try:
        parts = urllib.parse.urlsplit(u)
        if not parts.netloc:
            return ""
        safe_path = urllib.parse.quote(urllib.parse.unquote(parts.path), safe="/:@!$&'()*+,;=-._~")
        safe_query = urllib.parse.quote(urllib.parse.unquote(parts.query), safe="=&/:@!$'()*+,;-._~?")
        return urllib.parse.urlunsplit((parts.scheme, parts.netloc, safe_path, safe_query, ""))
    except Exception:
        return ""


def fetch_og_image(page_url: str, timeout: int = 6) -> str:
    """從活動官方頁面／售票頁前 64KB HTML 擷取真實活動海報 og:image。"""
    if not page_url or not page_url.startswith(("http://", "https://")):
        return ""
    if "cloud.culture.tw" in page_url and page_url.rstrip("/") == "https://cloud.culture.tw":
        return ""
    try:
        req = urllib.request.Request(page_url, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=timeout, context=SSL_CTX) as resp:
            ctype = (resp.headers.get("Content-Type") or "").lower()
            if "image/" in ctype:
                return clean_image_url(page_url)
            chunk = resp.read(65536).decode("utf-8", errors="ignore")
        for pat in (
            r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']',
            r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\']',
            r'<meta[^>]+name=["\']twitter:image["\'][^>]+content=["\']([^"\']+)["\']',
        ):
            m = re.search(pat, chunk, re.I)
            if m:
                cleaned = clean_image_url(m.group(1), base_url=page_url)
                if cleaned:
                    return cleaned
    except Exception:
        pass
    return ""


def fetch_tainan_detail_image(detail_url: str, timeout: int = 6) -> str:
    """從臺南旅遊網景點內頁擷取真實景點照片。"""
    if not detail_url:
        return ""
    try:
        req = urllib.request.Request(detail_url, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=timeout, context=SSL_CTX) as resp:
            chunk = resp.read(98304).decode("utf-8", errors="ignore")
        m = re.search(
            r"https?://www\.twtainan\.net/content/images/attractions/[^\s\"'<>]+?\.(?:jpg|jpeg|png|webp)",
            chunk,
            re.I,
        )
        if m:
            return clean_image_url(m.group(0))
    except Exception:
        pass
    return ""


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


def scrape_iculture(prev_img_cache: dict[str, str]) -> list[dict[str, Any]]:
    """抓取文化部全國藝文活動 Open Data（涵蓋全台灣 22 縣市），並補齊活動海報圖。"""
    today = dt.datetime.now(TZ_TW).date()
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
                promo_url = (row.get("sourceWebPromote") or "").strip()
                sales_url = (row.get("webSales") or "").strip()
                url = promo_url or sales_url
                if url and not url.startswith(("http://", "https://")):
                    url = "https://" + url
                if not url:
                    url = "https://cloud.culture.tw/"

                item_id = make_id("iculture", uid)
                img_url = clean_image_url(row.get("imageUrl") or "") or prev_img_cache.get(item_id, "")

                summary = desc[:140] + ("…" if len(desc) > 140 else "") if desc else f"{loc_name or city} 舉辦之親子藝文活動。"
                hours_text = f"{start_d or '近期'} ～ {end_d or '依官網公告'}"
                if loc_name and loc_name not in loc_addr:
                    address = f"{loc_addr}（{loc_name}）" if loc_addr else loc_name
                else:
                    address = loc_addr or loc_name or f"{city}{district}"

                seen_uids.add(uid)
                items.append({
                    "id": item_id,
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
                    "image_url": img_url,
                    "_alt_url": sales_url if sales_url != promo_url else "",
                    "source": "文化部全國藝文活動",
                })

    # 對尚未取得 image_url 的活動，平行向官方活動頁／售票頁擷取真實海報 og:image
    missing = [it for it in items if not it["image_url"] and it["url"] != "https://cloud.culture.tw/"]
    if missing:
        def _resolve_og(it: dict[str, Any]) -> None:
            img = fetch_og_image(it["url"], timeout=6)
            if not img and it.get("_alt_url"):
                alt = it["_alt_url"]
                if not alt.startswith(("http://", "https://")):
                    alt = "https://" + alt
                img = fetch_og_image(alt, timeout=6)
            it["image_url"] = img

        with concurrent.futures.ThreadPoolExecutor(max_workers=18) as ex:
            list(ex.map(_resolve_og, missing))

    for it in items:
        it.pop("_alt_url", None)
    return items


def scrape_taipei_events(prev_img_cache: dict[str, str]) -> list[dict[str, Any]]:
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

            item_id = make_id("tpe-evt", f"{row.get('id', '')}-{title}")
            img_url = ""
            for fobj in (row.get("files") or []):
                if isinstance(fobj, dict) and str(fobj.get("ext", "")).lower() in (".jpg", ".jpeg", ".png", ".webp"):
                    img_url = clean_image_url(fobj.get("src") or "")
                    if img_url:
                        break
            if not img_url:
                img_url = prev_img_cache.get(item_id, "") or fetch_og_image(row.get("url") or "", timeout=6)

            cat = classify_event_category(title, desc)
            ticket = strip_html(row.get("ticket") or "")
            is_free = infer_free(ticket, desc)
            seen.add(title)
            items.append({
                "id": item_id,
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
                "image_url": img_url,
                "source": "臺北旅遊網 Open API",
            })
    return items


def scrape_taipei_venues(prev_img_cache: dict[str, str]) -> list[dict[str, Any]]:
    """從臺北旅遊網 Open API 抓取親子共遊、公園、博物館與兒童場館（含官方實景照片）。"""
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

                item_id = make_id("tpe-venue", str(row.get("id") or name))
                img_url = ""
                for im in (row.get("images") or []):
                    if isinstance(im, dict) and im.get("src"):
                        img_url = clean_image_url(im["src"])
                        if img_url:
                            break
                if not img_url:
                    img_url = prev_img_cache.get(item_id, "")

                ticket = strip_html(row.get("ticket") or "")
                is_free = (cat == "park") or infer_free(ticket, intro)
                items.append({
                    "id": item_id,
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
                    "image_url": img_url,
                    "source": "臺北旅遊網 Open API",
                })
    return items


def scrape_tainan_venues(prev_img_cache: dict[str, str]) -> list[dict[str, Any]]:
    """從臺南市政府觀光旅遊局開放資料抓取親子場館，並從景點頁補齊實景照片。"""
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

        item_id = make_id("tnn-venue", str(row.get("id") or name))
        detail_url = f"https://www.twtainan.net/zh-tw/attractions/detail/{row.get('id')}" if row.get("id") else "https://www.twtainan.net/"
        is_free = (cat == "park") or infer_free("", summary)
        items.append({
            "id": item_id,
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
            "url": detail_url,
            "image_url": prev_img_cache.get(item_id, ""),
            "source": "臺南旅遊網 Open Data",
        })

    missing = [it for it in items if not it["image_url"] and "/detail/" in it["url"]]
    if missing:
        def _resolve_tn_img(it: dict[str, Any]) -> None:
            it["image_url"] = fetch_tainan_detail_image(it["url"], timeout=6)

        with concurrent.futures.ThreadPoolExecutor(max_workers=16) as ex:
            list(ex.map(_resolve_tn_img, missing))

    return items


def fetch_html(url: str, timeout: int = 12) -> str:
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=timeout, context=SSL_CTX) as resp:
        return resp.read().decode("utf-8", errors="ignore")


def scrape_keelung_official() -> list[dict[str, Any]]:
    """抓取基隆市政府兒少處官網：全市 7 區室內兒童樂園 + 各區公辦親子館（含實景附圖與地址）。"""
    index_pages = [
        ("amusement", "https://www.klcg.gov.tw/tw/children/4293.html", r"4293-\d+\.html"),
        ("parent_center", "https://www.klcg.gov.tw/tw/children/4301.html", r"4301-\d+\.html"),
    ]
    sub_links: list[tuple[str, str, str]] = []
    seen_hrefs: set[str] = set()

    for cat, idx_url, pat in index_pages:
        try:
            h = fetch_html(idx_url, timeout=10)
        except Exception as exc:
            print(f"[WARN] Keelung official fetch failed ({idx_url}): {exc}", file=sys.stderr)
            continue
        for m in re.finditer(rf'<a[^>]+href=["\']([^"\']*?{pat})["\'][^>]*>(.*?)</a>', h, re.S | re.I):
            href = urllib.parse.urljoin(idx_url, m.group(1).strip())
            raw_title = strip_html(m.group(2))
            if not raw_title or any(skip in raw_title for skip in ("須知", "Q&A", "QA", "服務項目", "集錦")):
                continue
            if href in seen_hrefs:
                continue
            seen_hrefs.add(href)
            title = raw_title if raw_title.startswith("基隆") else f"基隆市{raw_title}"
            sub_links.append((cat, title, href))

    def _parse_detail(entry: tuple[str, str, str]) -> dict[str, Any] | None:
        cat, title, url = entry
        try:
            dh = fetch_html(url, timeout=10)
        except Exception:
            return None
        clean_txt = re.sub(r"<script[\s\S]*?</script>|<style[\s\S]*?</style>", "", dh, flags=re.I)
        lines = [strip_html(x) for x in re.sub(r"<[^>]+>", "\n", clean_txt).splitlines() if strip_html(x)]
        addr = ""
        hours = "週三至週日分場次開放（週一、二休館，詳見官網預約）" if cat == "amusement" else "週二至週日 09:00–17:00（週一休館）"
        for i, line in enumerate(lines):
            if ("地址" in line) and i + 1 < len(lines) and ("基隆" in lines[i + 1] or "區" in lines[i + 1]):
                addr = lines[i + 1].rstrip("\\/ ")
                break
            m_inline = re.search(r"地址[：:]\s*(基隆[^\s，。]+)", line)
            if m_inline:
                addr = m_inline.group(1).rstrip("\\/ ")
                break
        _, district = extract_city_district(f"{addr} {title}", default_city="基隆市")
        imgs = re.findall(r'(/wSite/public/Attachment/[^\s"\'<>]+?\.(?:jpg|jpeg|png|webp))', dh, re.I)
        img_url = clean_image_url(imgs[0], base_url="https://www.klcg.gov.tw") if imgs else ""
        cc = TW_CITY_COORDS["基隆市"]
        desc_label = "基隆市政府設置之免費室內兒童樂園（限身高128公分以下兒童，線上與現場預約並行）。" if cat == "amusement" else "基隆市政府公辦免費親子館，提供學齡前幼兒遊戲、繪本共讀與親子活動。"
        return {
            "id": make_id("klcg", url),
            "title": title,
            "kind": "venue",
            "category": cat,
            "city": "基隆市",
            "district": district,
            "address": addr or f"基隆市{district}",
            "lat": cc[0],
            "lng": cc[1],
            "exact_coords": False,
            "age_groups": ["0-3", "3-6", "6-12"] if cat == "amusement" else ["0-3", "3-6"],
            "is_indoor": True,
            "is_free": True,
            "price_text": "免費入場（建議線上預約）",
            "hours": hours,
            "start_date": "",
            "end_date": "",
            "is_recurring_weekend": False,
            "summary": desc_label,
            "url": url,
            "image_url": img_url,
            "source": "基隆市政府兒少處",
        }

    out: list[dict[str, Any]] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
        for res in ex.map(_parse_detail, sub_links):
            if res:
                out.append(res)
    return out


def scrape_kaohsiung_official() -> list[dict[str, Any]]:
    """抓取高雄市政府社會局兒福中心官網：全高雄市公辦親子館一覽表。"""
    url = "https://cwsc.kcg.gov.tw/cp.aspx?n=FB81D67DDCEA0CF0"
    h = fetch_html(url, timeout=12)
    rows = re.findall(r"<tr[\s\S]*?</tr>", h, re.I)
    items: list[dict[str, Any]] = []
    cc = TW_CITY_COORDS["高雄市"]
    for r in rows:
        cells = [strip_html(c) for c in re.findall(r"<t[dh][^>]*>([\s\S]*?)</t[dh]>", r, re.I)]
        if len(cells) < 2:
            continue
        name, addr = cells[0], cells[1]
        if not name or "單位名稱" in name or "整備中" in addr or "暫不提供" in addr:
            continue
        if not any(k in name for k in ("親子", "婦幼", "兒福", "育兒")):
            continue
        title = name.split("(")[0].strip()
        if not title.startswith("高雄"):
            title = f"高雄市{title}"
        full_addr = addr if addr.startswith("高雄") else f"高雄市{addr}"
        _, district = extract_city_district(f"{full_addr} {title}", default_city="高雄市")
        items.append({
            "id": make_id("kcg-pc", title),
            "title": title,
            "kind": "venue",
            "category": "parent_center",
            "city": "高雄市",
            "district": district,
            "address": full_addr,
            "lat": cc[0],
            "lng": cc[1],
            "exact_coords": False,
            "age_groups": ["0-3", "3-6"],
            "is_indoor": True,
            "is_free": True,
            "price_text": "免費入場",
            "hours": "週二至週日分場次開放（週一休館）",
            "start_date": "",
            "end_date": "",
            "is_recurring_weekend": False,
            "summary": "高雄市政府社會局設置之免費育兒資源與親子遊戲空間，提供 0–6 歲分齡教具、繪本與親職活動。",
            "url": url,
            "image_url": "",
            "source": "高雄市政府兒福中心",
        })
    return items


ABIC_AREA_DEFAULT_CITY = {
    "基隆": "基隆市",
    "宜蘭": "宜蘭縣",
    "桃園": "桃園市",
    "苗栗": "苗栗縣",
    "台中": "臺中市",
    "彰化": "彰化縣",
    "南投": "南投縣",
    "雲林": "雲林縣",
    "台南": "臺南市",
    "高雄": "高雄市",
    "澎湖": "澎湖縣",
    "屏東": "屏東縣",
    "台東": "臺東縣",
    "花蓮": "花蓮縣",
    "金門": "金門縣",
    "馬祖": "連江縣",
}

ABIC_SKIP_TITLE_RE = re.compile(
    r"(結束營業|暫停營業|停止營業|已歇業|已無親子設施|活動結束|活動已結束|"
    r"飯店|酒店|行旅|民宿|旅店|會館|商旅|汽車旅館|露營|豪華帳|"
    r"景觀餐廳|景觀咖啡|咖啡廳|貓餐廳|貓咖啡|火鍋|燒肉|牛排|餐酒|居酒屋|冰店|夜市|小吃)",
    re.I,
)

ABIC_SCENIC_SKIP_RE = re.compile(
    r"(砲台|步道|瀑布|燈塔|登島|隧道|老鷹岩|峽谷|千島湖|紀念碑|漁港|吊橋|古道|陵寢|商圈|老街)",
    re.I,
)


def classify_abic_item(tag_kind: str, title: str, msg: str) -> tuple[str, str] | None:
    """回傳 (kind, category)，若非兒童場館／親子活動則回傳 None。"""
    if ABIC_SKIP_TITLE_RE.search(title) and not any(k in title for k in ("主題樂園", "遊樂世界", "兒童樂園", "親子館", "特色遊戲場")):
        return None
    text = f"{title} {msg}"
    if any(bad in text for bad in ADULT_EXCLUDE):
        return None

    if tag_kind == "event":
        return "event", classify_event_category(title, msg)

    if any(k in title for k in ("親子館", "托育資源", "親子悠遊館", "公共親子中心", "育兒資源", "育兒+", "祖孫館", "婦幼福利", "兒童福利服務", "社會福利綜合館")):
        return "venue", "parent_center"
    if any(k in title for k in ("室內兒童樂園", "兒童新樂園", "兒童樂園", "主題樂園", "遊樂園", "遊樂世界", "海洋世界", "海洋公園", "水世界", "湯姆熊", "貝兒絲", "騎士堡", "遊戲愛樂園", "奇幻島", "卡丁車", "動物園", "頑皮世界", "綠世界")):
        return "venue", "amusement"
    if any(k in title for k in ("特色遊戲場", "共融", "親子公園", "兒童公園", "運動公園", "親水公園", "森林公園", "溜滑梯")) or ("公園" in title and any(k in text for k in ("遊戲", "溜滑梯", "沙坑", "鞦韆", "滑索", "玩水", "戲水", "滑步車", "兒童", "親子"))):
        return "venue", "park"
    if tag_kind in ("museum", "factory") or any(k in title for k in ("博物館", "美術館", "科學", "天文", "海科館", "海生館", "水族館", "探索館", "故事館", "文物館", "文化館", "文化藝術館", "教育館", "教育中心", "觀光工廠", "體驗館", "漫畫圖書館", "兒童圖書館", "牧場", "農場", "生態園區")):
        if ABIC_SCENIC_SKIP_RE.search(title) and "遊戲場" not in title:
            return None
        return "venue", "kids_museum"
    if tag_kind in ("under12", "under6", "amusement"):
        if "公園" in title:
            return "venue", "park"
        return "venue", "amusement"

    # tag/63 政府開辦：過濾掉純成人登山步道／古蹟砲台
    if ABIC_SCENIC_SKIP_RE.search(title):
        return None
    if any(k in text for k in ("遊戲場", "溜滑梯", "沙坑", "共融", "兒童遊具")):
        return "venue", "park"
    if any(k in text for k in ("親子館", "托育", "0-6歲")):
        return "venue", "parent_center"
    return None


def scrape_abic_nationwide(prev_item_cache: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """抓取全台公開親子景點目錄（涵蓋 22 縣市室內兒童樂園、親子館、特色遊戲場、博物館、遊樂園與展覽活動；不設硬上限）。"""
    tag_sources = [
        ("amusement", "/index/search/tag/64/filter/74"),  # 大型遊樂園
        ("under12", "/index/search/tag/83/filter/74"),    # 限制12歲以下（室內兒童樂園等）
        ("under6", "/index/search/tag/81/filter/74"),     # 限制6歲以下（親子館／幼兒樂園）
        ("museum", "/index/search/tag/79/filter/74"),     # 博物館／兒童館
        ("factory", "/index/search/tag/56/filter/74"),    # 觀光工廠／親子DIY
        ("event", "/index/search/tag/59/filter/74"),      # 展覽演出／期間限定親子活動
        ("gov", "/index/search/tag/63/filter/74"),        # 全國政府開辦親子館／室內樂園／特色公園
    ]

    page_jobs: list[tuple[str, str]] = []
    for tkind, base_path in tag_sources:
        try:
            first_html = fetch_html("https://www.abic.com.tw" + base_path, timeout=12)
        except Exception as exc:
            print(f"[WARN] ABIC tag index failed ({base_path}): {exc}", file=sys.stderr)
            continue
        pages = [int(p) for p in re.findall(r"/page/(\d+)", first_html)]
        max_p = max(pages) if pages else 1
        for p in range(1, max_p + 1):
            page_jobs.append((tkind, f"https://www.abic.com.tw{base_path}/page/{p}"))

    card_re = re.compile(
        r'<div class="post [^"]*">[\s\S]*?<div class="area"><a [^>]*>([^<]+)</a></div>'
        r'[\s\S]*?<div class="title"><a href="/place/view/id/(\d+)">([^<]+)</a></div>'
        r'[\s\S]*?<img src="([^"]+)"[\s\S]*?<div class="message"><a [^>]*>([\s\S]*?)</a></div>',
        re.I,
    )

    candidates: dict[str, dict[str, str]] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as ex:
        futs = [ex.submit(fetch_html, u, 12) for _, u in page_jobs]
        for (tkind, _), fut in zip(page_jobs, futs):
            try:
                h = fut.result()
            except Exception:
                continue
            for m in card_re.finditer(h):
                area, pid, raw_title, thumb, raw_msg = (
                    strip_html(m.group(1)),
                    m.group(2),
                    strip_html(m.group(3)),
                    m.group(4).strip(),
                    strip_html(m.group(5)),
                )
                if pid in candidates:
                    continue
                cls = classify_abic_item(tkind, raw_title, raw_msg)
                if not cls:
                    continue
                candidates[pid] = {
                    "pid": pid,
                    "tkind": tkind,
                    "kind": cls[0],
                    "category": cls[1],
                    "area": area,
                    "title": raw_title,
                    "thumb": thumb,
                    "msg": raw_msg,
                }

    items: list[dict[str, Any]] = []
    to_enrich: list[dict[str, Any]] = []

    for pid, cand in candidates.items():
        item_id = f"abic-{pid}"
        cached = prev_item_cache.get(item_id)
        if cached and cached.get("city") in TW_DISTRICTS and cached.get("address"):
            # 直接重用已快取之明細欄位，大幅加速每週排程更新
            reused = dict(cached)
            reused["title"] = cand["title"]
            reused["kind"] = cand["kind"]
            reused["category"] = cand["category"]
            if not reused.get("image_url"):
                reused["image_url"] = clean_image_url(cand["thumb"])
            items.append(reused)
        else:
            to_enrich.append(cand)

    def _enrich_one(cand: dict[str, str]) -> dict[str, Any] | None:
        pid = cand["pid"]
        detail_url = f"https://www.abic.com.tw/place/view/id/{pid}"
        addr, hours, price_raw, og_img = "", "", "", ""
        lat, lng, exact_coords = None, None, False
        try:
            dh = fetch_html(detail_url, timeout=9)
            m_desc = re.search(r'<meta[^>]+property=["\']og:description["\'][^>]+content=["\']([^"\']+)["\']', dh, re.I)
            if m_desc:
                og_desc = html.unescape(m_desc.group(1))
                m_a = re.search(r"★地址：(.*?)(?:\.{2,}|★|$)", og_desc)
                m_h = re.search(r"★營業時間：(.*?)(?:\.{2,}|★|$)", og_desc)
                m_p = re.search(r"★價位：(.*?)(?:\.{2,}|★|$)", og_desc)
                if m_a:
                    addr = strip_html(m_a.group(1))
                if m_h:
                    hours = strip_html(m_h.group(1))
                if m_p:
                    price_raw = strip_html(m_p.group(1))
            m_img = re.search(r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']', dh, re.I)
            if m_img and "abic_logo" not in m_img.group(1):
                og_img = clean_image_url(m_img.group(1))
            m_coord = re.search(r"(2[1-6]\.\d{4,8})\s*,\s*(1[12]\d\.\d{4,8})", dh)
            if m_coord:
                lf, gf = float(m_coord.group(1)), float(m_coord.group(2))
                if 21.5 <= lf <= 26.5 and 118.0 <= gf <= 122.5:
                    lat, lng, exact_coords = lf, gf, True
        except Exception:
            pass

        default_city = ABIC_AREA_DEFAULT_CITY.get(cand["area"], "")
        city, district = extract_city_district(f"{addr} {cand['title']} {cand['msg']}", default_city=default_city)
        if not city:
            return None
        if lat is None or lng is None:
            cc = TW_CITY_COORDS.get(city, [25.033, 121.565, 11])
            lat, lng = cc[0], cc[1]

        cat = cand["category"]
        text_all = f"{cand['title']} {cand['msg']} {price_raw}"
        is_free = (cat in ("park", "parent_center")) or infer_free(price_raw, text_all)
        img_final = og_img or clean_image_url(cand["thumb"])

        age_groups = infer_age_groups(text_all, cat)
        if cand["tkind"] == "under6" and "0-3" not in age_groups:
            age_groups = ["0-3", "3-6"]

        return {
            "id": f"abic-{pid}",
            "title": cand["title"],
            "kind": cand["kind"],
            "category": cat,
            "city": city,
            "district": district,
            "address": addr or f"{city}{district}",
            "lat": round(lat, 5),
            "lng": round(lng, 5),
            "exact_coords": exact_coords,
            "age_groups": age_groups,
            "is_indoor": ("室內" in text_all) or infer_indoor(text_all, cat),
            "is_free": is_free,
            "price_text": (price_raw[:85] if price_raw else ("免費入場" if is_free else "依現場公告")),
            "hours": (hours[:90] if hours else "依場館官網公告"),
            "start_date": "",
            "end_date": "",
            "is_recurring_weekend": False,
            "summary": cand["msg"] or f"{city}{district}推薦親子去處。",
            "url": detail_url,
            "image_url": img_final,
            "source": "全台親子景點公開彙整",
        }

    if to_enrich:
        with concurrent.futures.ThreadPoolExecutor(max_workers=16) as ex:
            for enriched in ex.map(_enrich_one, to_enrich):
                if enriched:
                    items.append(enriched)

    return items


def load_previous_state() -> tuple[dict[str, list[dict[str, Any]]], dict[str, str], dict[str, dict[str, Any]]]:
    if not OUT_PATH.exists():
        return {}, {}, {}
    try:
        prev = json.loads(OUT_PATH.read_text(encoding="utf-8"))
        by_src: dict[str, list[dict[str, Any]]] = {}
        img_cache: dict[str, str] = {}
        item_cache: dict[str, dict[str, Any]] = {}
        for item in prev.get("items", []):
            by_src.setdefault(item.get("source", "unknown"), []).append(item)
            if item.get("id"):
                item_cache[item["id"]] = item
                if item.get("image_url"):
                    cleaned = clean_image_url(item["image_url"])
                    if cleaned:
                        img_cache[item["id"]] = cleaned
        return by_src, img_cache, item_cache
    except Exception:
        return {}, {}, {}


def normalize_title_key(title: str, city: str) -> str:
    t = (title or "").replace("台", "臺").strip()
    # 移除括號附註（如「(基隆安樂親子館)」、「(特色遊戲場)」）以利跨來源同名場館自動合併補圖與座標
    stripped = re.sub(r"[（(][^）)]*[）)]", "", t).strip()
    if len(stripped) >= 3:
        t = stripped
    clean = re.sub(r"[\s\-－—（）()【】\[\]・‧·]", "", t).lower()
    city_short = city.replace("市", "").replace("縣", "").lower()
    for prefix in (city.lower(), city_short):
        if prefix and clean.startswith(prefix) and len(clean) > len(prefix) + 1:
            clean = clean[len(prefix):]
            break
    return f"{city}:{clean}"


def main() -> int:
    now_tw = dt.datetime.now(TZ_TW)
    prev_by_src, prev_img_cache, prev_item_cache = load_previous_state()

    # 1. 讀取全台 22 縣市常設精選場館基準庫
    curated: list[dict[str, Any]] = json.loads(CURATED_PATH.read_text(encoding="utf-8"))
    for item in curated:
        item.setdefault("exact_coords", True)
        item.setdefault("start_date", "")
        item.setdefault("end_date", "")
        item.setdefault("is_recurring_weekend", False)
        item["image_url"] = clean_image_url(item.get("image_url") or "")

    # 2. 平行執行所有即時公開資料爬蟲
    sources = {
        "文化部全國藝文活動": (lambda: scrape_iculture(prev_img_cache), 15),
        "臺北旅遊網 Open API (活動)": (lambda: scrape_taipei_events(prev_img_cache), 1),
        "臺北旅遊網 Open API (場館)": (lambda: scrape_taipei_venues(prev_img_cache), 10),
        "臺南旅遊網 Open Data": (lambda: scrape_tainan_venues(prev_img_cache), 10),
        "基隆市政府兒少處": (scrape_keelung_official, 10),
        "高雄市政府兒福中心": (scrape_kaohsiung_official, 10),
        "全台親子景點公開彙整": (lambda: scrape_abic_nationwide(prev_item_cache), 200),
    }

    source_results: dict[str, list[dict[str, Any]]] = {}
    source_health: list[dict[str, Any]] = []

    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as ex:
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

    # 3. 合併去重（若精選庫本身缺照片、而即時 API 同名點位有真實照片，自動把真實照片補進精選項目！）
    merged_by_key: dict[str, dict[str, Any]] = {}
    merged: list[dict[str, Any]] = []
    seen_ids: set[str] = set()

    ordered_batches = [
        curated,
        source_results.get("基隆市政府兒少處", []),
        source_results.get("高雄市政府兒福中心", []),
        source_results.get("文化部全國藝文活動", []),
        source_results.get("臺北旅遊網 Open API (活動)", []),
        source_results.get("全台親子景點公開彙整", []),
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
            item["image_url"] = clean_image_url(item.get("image_url") or "")
            key = normalize_title_key(title, city)
            if key in merged_by_key:
                existing = merged_by_key[key]
                if not existing.get("image_url") and item.get("image_url"):
                    existing["image_url"] = item["image_url"]
                if not existing.get("exact_coords") and item.get("exact_coords"):
                    existing["lat"] = item["lat"]
                    existing["lng"] = item["lng"]
                    existing["exact_coords"] = True
                if not existing.get("district") and item.get("district"):
                    existing["district"] = item["district"]
                continue
            if cid in seen_ids:
                continue
            seen_ids.add(cid)
            merged_by_key[key] = item
            merged.append(item)

    cat_counts: dict[str, int] = {}
    city_counts: dict[str, int] = {}
    kind_counts = {"venue": 0, "event": 0}
    with_image_count = 0
    for it in merged:
        cat_counts[it["category"]] = cat_counts.get(it["category"], 0) + 1
        city_counts[it["city"]] = city_counts.get(it["city"], 0) + 1
        kind_counts[it["kind"]] = kind_counts.get(it["kind"], 0) + 1
        if it.get("image_url"):
            with_image_count += 1

    payload = {
        "updated_at": now_tw.isoformat(timespec="seconds"),
        "updated_date": now_tw.strftime("%Y-%m-%d %H:%M (台灣時間)"),
        "total": len(merged),
        "with_image_count": with_image_count,
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
        "with_image_count": with_image_count,
        "kind_counts": kind_counts,
        "category_counts": cat_counts,
        "cities_covered": len(city_counts),
        "sources": source_health,
    }
    META_PATH.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    print(
        f"✅ Synced {len(merged)} items across {len(city_counts)}/22 cities "
        f"(venues={kind_counts['venue']}, events={kind_counts['event']}, with_real_images={with_image_count})."
    )
    print(f"   Categories: {json.dumps(cat_counts, ensure_ascii=False)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
