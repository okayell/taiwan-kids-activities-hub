# 🧸 台灣親子趣 — 全台灣 12 歲以下兒童活動＆場館地圖

整合全台灣 **22 縣市 × 368 鄉鎮市區** 的兒童（12 歲以下）常設場館與最新限期活動，支援 GitHub Actions **每週一、週四凌晨 04:00（台灣時間）自動更新**並部署至 GitHub Pages。

## 主要功能

- **縣市 → 鄉鎮市區二級聯動搜尋**：涵蓋全台灣 22 縣市與 368 鄉鎮區，搭配北／中／南／東／離島快選與即時關鍵字搜尋。
- **常設場館 vs 限期活動雙視角**：
  - 🏛️ **常設場館**：兒童遊樂園、兒童館／科博館、公辦親子館、特色共融公園。
  - 🎪 **限期活動**：親子活動、兒童比賽／徵件、親子市集、親子課程／DIY 工作坊。
- **親子實用篩選**：適齡（0–3 / 3–6 / 6–12 歲）、☔ 室內雨天備案、🆓 免費入場、📅 本週末可去。
- **互動地圖模式**：整合免費 Leaflet + OpenStreetMap，篩選條件即時同步地圖點位，並提供一鍵 Google Maps 導航。
- **我的口袋名單**：免登入即可於瀏覽器收藏（`localStorage`），並內建「🎲 週末靈感」隨機推薦。
- **高可靠自動更新機制**：單一公開來源異常時自動觸發 Keep-Previous-On-Failure 保底機制，並透過 `tools/check_data.py` 驗證資料品質。

## 本機預覽與手動同步

```bash
# 1. 手動抓取最新公開資料並驗證品質
bash scripts/sync_local.sh

# 2. 執行完整驗證
bash tools/verify.sh

# 3. 啟動本機預覽伺服器
python3 -m http.server 8080
# 開啟瀏覽器訪問 http://localhost:8080
```

## 發佈到 GitHub Pages

1. 在 GitHub 建立一個新的公開 Repository（例如 `taiwan-kids-activities-hub`）。
2. 將本專案推送至 GitHub：
   ```bash
   git remote add origin https://github.com/<你的帳號>/taiwan-kids-activities-hub.git
   git push -u origin main
   ```
3. 到 GitHub Repo 的 **Settings → Pages → Build and deployment → Source** 選擇 **GitHub Actions**。
4. 到 **Actions** 分頁可隨時手動點選 **Run workflow** 立即同步更新；之後系統將於每週一、週四 04:00（台灣時間）自動更新資料並重新發佈。
