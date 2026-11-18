(() => {
  "use strict";

  const SAVED_KEY = "tw_kids_hub_saved_v1";
  const PAGE_SIZE = 24;

  const CATEGORY_META = {
    amusement: { label: "兒童遊樂園", icon: "🎡", kind: "venue", color: "#ff7a59" },
    kids_museum: { label: "兒童館／科博館", icon: "🏛️", kind: "venue", color: "#4aa3f5" },
    parent_center: { label: "親子館", icon: "🧸", kind: "venue", color: "#f2729a" },
    park: { label: "特色公園", icon: "🛝", kind: "venue", color: "#2ec49c" },
    festival: { label: "大型節慶／燈會煙火", icon: "🎆", kind: "event", color: "#e62e6b" },
    activity: { label: "親子活動", icon: "🎪", kind: "event", color: "#e5a000" },
    contest: { label: "兒童比賽", icon: "🏆", kind: "event", color: "#9b82f3" },
    market: { label: "親子市集", icon: "🛍️", kind: "event", color: "#e87e22" },
    course: { label: "親子課程", icon: "🎨", kind: "event", color: "#19a59f" },
  };

  const state = {
    allItems: [],
    filtered: [],
    visibleCount: PAGE_SIZE,
    region: "",
    city: "",
    district: "",
    keyword: "",
    kind: "all", // all | venue | event
    category: "",
    ages: new Set(),
    indoorOnly: false,
    freeOnly: false,
    weekendOnly: false,
    sort: "smart",
    view: "list", // list | map
    savedIds: new Set(),
    map: null,
    markerLayer: null,
  };

  const els = {
    statTotal: document.getElementById("stat-total"),
    statVenues: document.getElementById("stat-venues"),
    statEvents: document.getElementById("stat-events"),
    statUpdated: document.getElementById("stat-updated"),
    regionPills: document.getElementById("region-pills"),
    selectCity: document.getElementById("select-city"),
    selectDistrict: document.getElementById("select-district"),
    inputKeyword: document.getElementById("input-keyword"),
    btnClearSearch: document.getElementById("btn-clear-search"),
    kindTabs: document.querySelectorAll(".kind-tab"),
    categoryPills: document.getElementById("category-pills"),
    ageChips: document.querySelectorAll(".tag-chip[data-age]"),
    toggleIndoor: document.getElementById("toggle-indoor"),
    toggleFree: document.getElementById("toggle-free"),
    toggleWeekend: document.getElementById("toggle-weekend"),
    btnResetFilters: document.getElementById("btn-reset-filters"),
    btnEmptyReset: document.getElementById("btn-empty-reset"),
    resultSummary: document.getElementById("result-summary"),
    selectSort: document.getElementById("select-sort"),
    btnViewList: document.getElementById("btn-view-list"),
    btnViewMap: document.getElementById("btn-view-map"),
    mapSection: document.getElementById("map-section"),
    cardsSection: document.getElementById("cards-section"),
    cardsGrid: document.getElementById("cards-grid"),
    emptyState: document.getElementById("empty-state"),
    loadMoreWrap: document.getElementById("load-more-wrap"),
    btnLoadMore: document.getElementById("btn-load-more"),
    sourceBadges: document.getElementById("source-badges"),
    btnVersion: document.getElementById("btn-version"),
    footerVersion: document.getElementById("footer-version"),
    releaseModal: document.getElementById("release-modal"),
    releaseModalClose: document.getElementById("release-modal-close"),
    releaseList: document.getElementById("release-list"),
    localBadge: document.getElementById("local-mode-badge"),
    btnLocalSync: document.getElementById("btn-local-sync"),
    btnRandom: document.getElementById("btn-random"),
    btnSaved: document.getElementById("btn-saved"),
    savedCount: document.getElementById("saved-count"),
    detailModal: document.getElementById("detail-modal"),
    modalClose: document.getElementById("modal-close"),
    modalBody: document.getElementById("modal-body"),
    savedDrawer: document.getElementById("saved-drawer"),
    drawerClose: document.getElementById("drawer-close"),
    savedList: document.getElementById("saved-list"),
    btnClearSaved: document.getElementById("btn-clear-saved"),
  };

  function esc(str) {
    return String(str ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function loadSaved() {
    try {
      const raw = localStorage.getItem(SAVED_KEY);
      const arr = raw ? JSON.parse(raw) : [];
      state.savedIds = new Set(Array.isArray(arr) ? arr : []);
    } catch {
      state.savedIds = new Set();
    }
    updateSavedBadge();
  }

  function persistSaved() {
    try {
      localStorage.setItem(SAVED_KEY, JSON.stringify([...state.savedIds]));
    } catch {
      // ignore storage quota errors
    }
    updateSavedBadge();
  }

  function updateSavedBadge() {
    els.savedCount.textContent = String(state.savedIds.size);
  }

  function fmtLocalDate(d) {
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
  }

  function getActiveWindowDates() {
    const now = new Date();
    const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
    const horizon = new Date(today.getFullYear(), today.getMonth(), today.getDate() + 92);
    const day = today.getDay(); // 0 Sun .. 6 Sat
    const satOffset = day === 0 ? -1 : 6 - day;
    const sat = new Date(today.getFullYear(), today.getMonth(), today.getDate() + satOffset);
    const sun = new Date(sat.getFullYear(), sat.getMonth(), sat.getDate() + 1);
    return {
      todayIso: fmtLocalDate(today),
      horizonIso: fmtLocalDate(horizon),
      satIso: fmtLocalDate(sat),
      sunIso: fmtLocalDate(sun),
    };
  }

  const DATE_WIN = getActiveWindowDates();

  function isEventActiveWithinThreeMonths(item) {
    if (item.kind !== "event") return true;
    const s = item.start_date || item.end_date || "";
    const e = item.end_date || item.start_date || "";
    if (!s || !e) return false;
    return e >= DATE_WIN.todayIso && s <= DATE_WIN.horizonIso;
  }

  function isWeekendAvailable(item) {
    if (item.kind === "venue") return true;
    const s = item.start_date || "";
    const e = item.end_date || s;
    if (!s || !e) return false;
    return s <= DATE_WIN.sunIso && e >= DATE_WIN.satIso;
  }

  function populateCities() {
    const cities = state.region
      ? (window.TW_REGIONS[state.region] || [])
      : Object.keys(window.TW_DISTRICTS || {});

    const counts = {};
    for (const it of state.allItems) {
      counts[it.city] = (counts[it.city] || 0) + 1;
    }

    const opts = [`<option value="">${state.region ? `${state.region}全部縣市` : "全台灣（22 縣市）"}</option>`];
    for (const c of cities) {
      const cnt = counts[c] || 0;
      opts.push(`<option value="${esc(c)}">${esc(c)} (${cnt})</option>`);
    }
    els.selectCity.innerHTML = opts.join("");
    if (cities.includes(state.city)) {
      els.selectCity.value = state.city;
    } else {
      state.city = "";
      state.district = "";
    }
    populateDistricts();
  }

  function populateDistricts() {
    if (!state.city) {
      els.selectDistrict.disabled = true;
      els.selectDistrict.innerHTML = `<option value="">請先選擇縣市</option>`;
      state.district = "";
      return;
    }
    const districts = (window.TW_DISTRICTS && window.TW_DISTRICTS[state.city]) || [];
    const counts = {};
    for (const it of state.allItems) {
      if (it.city === state.city && it.district) {
        counts[it.district] = (counts[it.district] || 0) + 1;
      }
    }
    const opts = [`<option value="">${esc(state.city)}全區</option>`];
    for (const d of districts) {
      const cnt = counts[d] || 0;
      const suffix = cnt > 0 ? ` (${cnt})` : "";
      opts.push(`<option value="${esc(d)}">${esc(d)}${suffix}</option>`);
    }
    els.selectDistrict.disabled = false;
    els.selectDistrict.innerHTML = opts.join("");
    els.selectDistrict.value = districts.includes(state.district) ? state.district : "";
  }

  function renderCategoryPills() {
    // 計算目前地點與關鍵字條件下，各分類的筆數
    const baseItems = filterItems({ ignoreCategory: true });
    const counts = {};
    for (const it of baseItems) {
      counts[it.category] = (counts[it.category] || 0) + 1;
    }

    const entries = Object.entries(CATEGORY_META).filter(([, meta]) => {
      if (state.kind === "all") return true;
      return meta.kind === state.kind;
    });

    const html = [
      `<button type="button" class="cat-pill ${state.category === "" ? "active" : ""}" data-cat="">
        🌟 全部分類 <span class="cnt">${baseItems.length}</span>
      </button>`,
      ...entries.map(([key, meta]) => {
        const cnt = counts[key] || 0;
        const active = state.category === key ? "active" : "";
        return `<button type="button" class="cat-pill ${active}" data-cat="${key}">
          ${meta.icon} ${esc(meta.label)} <span class="cnt">${cnt}</span>
        </button>`;
      }),
    ];
    els.categoryPills.innerHTML = html.join("");
  }

  function filterItems({ ignoreCategory = false } = {}) {
    const kw = state.keyword.trim().toLowerCase();
    const regionCities = state.region ? new Set(window.TW_REGIONS[state.region] || []) : null;

    return state.allItems.filter((it) => {
      if (regionCities && !regionCities.has(it.city)) return false;
      if (state.city && it.city !== state.city) return false;
      if (state.district && it.district !== state.district) return false;
      if (state.kind !== "all" && it.kind !== state.kind) return false;
      if (!ignoreCategory && state.category && it.category !== state.category) return false;

      if (state.ages.size > 0) {
        const itemAges = it.age_groups || [];
        let matched = false;
        for (const a of state.ages) {
          if (itemAges.includes(a)) {
            matched = true;
            break;
          }
        }
        if (!matched) return false;
      }

      if (state.indoorOnly && !it.is_indoor) return false;
      if (state.freeOnly && !it.is_free) return false;
      if (state.weekendOnly && !isWeekendAvailable(it)) return false;

      if (kw) {
        const hay = `${it.title} ${it.city} ${it.district} ${it.address} ${it.summary} ${it.price_text} ${it.source}`.toLowerCase();
        if (!hay.includes(kw)) return false;
      }
      return true;
    });
  }

  function sortItems(list) {
    const arr = [...list];
    if (state.sort === "date") {
      arr.sort((a, b) => {
        const da = a.start_date || "9999-12-31";
        const db = b.start_date || "9999-12-31";
        return da.localeCompare(db);
      });
    } else if (state.sort === "city") {
      const order = Object.keys(window.TW_DISTRICTS || {});
      arr.sort((a, b) => order.indexOf(a.city) - order.indexOf(b.city));
    } else {
      // 綜合推薦：大型節慶與精選場館庫優先，再依近期週末可去與精準座標排序
      arr.sort((a, b) => {
        const score = (x) =>
          (x.category === "festival" ? 2.4 : 0) +
          (x.source === "全台精選親子場館庫" || x.source === "全台大型節慶精選" ? 2 : 0) +
          (x.kind === "event" && isWeekendAvailable(x) ? 1.5 : 0) +
          (x.exact_coords ? 0.5 : 0);
        return score(b) - score(a);
      });
    }
    return arr;
  }

  function buildCardHtml(item) {
    const meta = CATEGORY_META[item.category] || { label: "親子推薦", icon: "✨" };
    const isSaved = state.savedIds.has(item.id);
    const locLabel = `${item.city}${item.district ? "・" + item.district : ""}`;
    const mapsUrl = `https://www.google.com/maps/search/?api=1&query=${encodeURIComponent(item.title + " " + (item.address || item.city))}`;

    const tags = [];
    if (item.is_indoor) tags.push(`<span class="mini-tag indoor">☔ 室內</span>`);
    if (item.is_free) tags.push(`<span class="mini-tag free">🆓 免費</span>`);
    if (item.kind === "event" && isWeekendAvailable(item)) {
      tags.push(`<span class="mini-tag weekend">📅 本週末</span>`);
    }
    if (item.category === "festival" || item.all_ages) {
      tags.push(`<span class="mini-tag all-ages">👨‍👩‍👧 全齡皆宜</span>`);
    } else {
      for (const a of (item.age_groups || []).slice(0, 3)) {
        tags.push(`<span class="mini-tag">${esc(a)}歲</span>`);
      }
    }

    const coverHtml = item.image_url
      ? `<div class="card-cover" data-open-id="${esc(item.id)}">
           <img src="${esc(item.image_url)}" alt="${esc(item.title)}" loading="lazy" referrerpolicy="no-referrer" onerror="this.closest('.card-cover')?.remove()" />
         </div>`
      : "";

    return `
      <article class="card" data-id="${esc(item.id)}">
        ${coverHtml}
        <div class="card-head">
          <span class="cat-badge cat-${esc(item.category)}">${meta.icon} ${esc(meta.label)}</span>
          <button type="button" class="save-btn ${isSaved ? "saved" : ""}" data-save-id="${esc(item.id)}" aria-label="${isSaved ? "取消收藏" : "加入口袋名單"}" title="${isSaved ? "取消收藏" : "加入口袋名單"}">
            ${isSaved ? "❤️" : "🤍"}
          </button>
        </div>
        <h3 class="card-title" data-open-id="${esc(item.id)}">${esc(item.title)}</h3>
        <div class="card-meta">
          <div>📍 ${esc(locLabel)}</div>
          <div>🕒 ${esc(item.hours || "依官網公告")}</div>
          <div>🎟️ ${esc(item.price_text || "依現場公告")}</div>
        </div>
        <p class="card-summary">${esc(item.summary)}</p>
        <div class="card-tags">${tags.join("")}</div>
        <div class="card-actions">
          <button type="button" class="btn btn-outline" data-open-id="${esc(item.id)}">詳細資訊</button>
          <a class="btn btn-coral" href="${mapsUrl}" target="_blank" rel="noopener noreferrer">🧭 導航</a>
        </div>
      </article>
    `;
  }

  function renderCards() {
    const total = state.filtered.length;
    const locDesc = state.city
      ? `${state.city}${state.district ? " " + state.district : ""}`
      : (state.region || "全台灣");
    els.resultSummary.textContent = `在「${locDesc}」找到 ${total} 筆親子場館、兒童活動與大型節慶`;

    if (total === 0) {
      els.cardsGrid.innerHTML = "";
      els.emptyState.hidden = false;
      els.loadMoreWrap.hidden = true;
      return;
    }

    els.emptyState.hidden = true;
    const slice = state.filtered.slice(0, state.visibleCount);
    els.cardsGrid.innerHTML = slice.map(buildCardHtml).join("");

    if (state.visibleCount < total) {
      els.loadMoreWrap.hidden = false;
      els.btnLoadMore.textContent = `顯示更多項目（剩餘 ${total - state.visibleCount} 筆）`;
    } else {
      els.loadMoreWrap.hidden = true;
    }
  }

  function ensureMap() {
    if (state.map || typeof window.L === "undefined") return;
    state.map = window.L.map("kids-map").setView([23.7, 120.95], 8);
    window.L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
      attribution: "&copy; OpenStreetMap contributors",
      maxZoom: 18,
    }).addTo(state.map);
    state.markerLayer = window.L.layerGroup().addTo(state.map);
  }

  function renderMap() {
    if (state.view !== "map") return;
    ensureMap();
    if (!state.map || !state.markerLayer) return;

    state.markerLayer.clearLayers();
    const markers = [];

    for (const item of state.filtered.slice(0, 300)) {
      if (typeof item.lat !== "number" || typeof item.lng !== "number") continue;
      const meta = CATEGORY_META[item.category] || { icon: "📍", color: "#ff7a59", label: "親子" };
      const marker = window.L.circleMarker([item.lat, item.lng], {
        radius: 8,
        fillColor: meta.color,
        color: "#ffffff",
        weight: 2,
        opacity: 1,
        fillOpacity: 0.92,
      });

      const popupImg = item.image_url
        ? `<img src="${esc(item.image_url)}" alt="" referrerpolicy="no-referrer" onerror="this.remove()" style="width:100%;height:105px;object-fit:cover;border-radius:8px;margin-bottom:6px;display:block;" />`
        : "";
      const popupHtml = `
        <div style="min-width:200px;max-width:230px;font-family:'Noto Sans TC',sans-serif;">
          ${popupImg}
          <div style="font-size:12px;color:#666;margin-bottom:3px;">${meta.icon} ${esc(meta.label)} · ${esc(item.city)}${esc(item.district)}</div>
          <div style="font-weight:800;font-size:14px;margin-bottom:6px;">${esc(item.title)}</div>
          <div style="font-size:12px;color:#555;margin-bottom:8px;">${esc(item.hours || "")}</div>
          <button type="button" onclick="window.__openKidItem('${esc(item.id)}')" style="background:#ff7a59;color:#fff;border:none;border-radius:99px;padding:5px 12px;font-weight:700;cursor:pointer;">查看詳情</button>
        </div>
      `;
      marker.bindPopup(popupHtml);
      marker.addTo(state.markerLayer);
      markers.push([item.lat, item.lng]);
    }

    setTimeout(() => {
      state.map.invalidateSize();
      if (state.city && window.TW_CITY_COORDS && window.TW_CITY_COORDS[state.city]) {
        const [lat, lng, zoom] = window.TW_CITY_COORDS[state.city];
        state.map.setView([lat, lng], zoom);
      } else if (markers.length > 0 && markers.length <= 60) {
        state.map.fitBounds(markers, { padding: [30, 30], maxZoom: 13 });
      } else {
        state.map.setView([23.75, 120.95], 8);
      }
    }, 60);
  }

  function applyFiltersAndRender({ resetPage = true } = {}) {
    if (resetPage) state.visibleCount = PAGE_SIZE;
    renderCategoryPills();
    state.filtered = sortItems(filterItems());
    renderCards();
    renderMap();
  }

  function openDetailModal(id) {
    const item = state.allItems.find((x) => x.id === id);
    if (!item) return;
    const meta = CATEGORY_META[item.category] || { label: "親子推薦", icon: "✨" };
    const isSaved = state.savedIds.has(item.id);
    const mapsUrl = `https://www.google.com/maps/search/?api=1&query=${encodeURIComponent(item.title + " " + (item.address || item.city))}`;

    const modalCover = item.image_url
      ? `<img class="modal-cover" src="${esc(item.image_url)}" alt="${esc(item.title)}" referrerpolicy="no-referrer" onerror="this.remove()" />`
      : "";

    els.modalBody.innerHTML = `
      ${modalCover}
      <span class="cat-badge cat-${esc(item.category)}">${meta.icon} ${esc(meta.label)}</span>
      <h2 id="modal-title" style="margin:10px 0 6px;font-size:1.35rem;">${esc(item.title)}</h2>
      <p style="margin:0;color:#555;">${esc(item.summary)}</p>
      <div class="modal-detail-list">
        <div><strong>📍 縣市鄉鎮：</strong>${esc(item.city)} ${esc(item.district || "（全區）")}</div>
        <div><strong>🏠 詳細地址：</strong>${esc(item.address || "依主辦單位公告")}</div>
        <div><strong>🕒 時間／檔期：</strong>${esc(item.hours || "依官網公告")}</div>
        <div><strong>🎟️ 費用資訊：</strong>${esc(item.price_text || "依現場公告")}</div>
        <div><strong>👶 適合年齡：</strong>${item.category === "festival" || item.all_ages ? "全齡皆宜（不限年齡，適合全家大小同遊）" : esc((item.age_groups || []).map((a) => a + "歲").join("、") || "12歲以下")}</div>
        <div><strong>🌤️ 場地環境：</strong>${item.is_indoor ? "室內空間（雨天友善）" : "戶外／半戶外空間"}</div>
        <div><strong>📡 資料來源：</strong>${esc(item.source || "公開資料")}</div>
      </div>
      <div class="modal-actions">
        <a class="btn btn-coral" href="${mapsUrl}" target="_blank" rel="noopener noreferrer">🧭 Google 地圖導航</a>
        ${item.url ? `<a class="btn btn-mint" href="${esc(item.url)}" target="_blank" rel="noopener noreferrer">🔗 前往官方網站</a>` : ""}
        <button type="button" class="btn btn-outline" id="modal-save-btn" data-save-id="${esc(item.id)}">
          ${isSaved ? "❤️ 已在口袋名單" : "🤍 加入口袋名單"}
        </button>
      </div>
    `;
    els.detailModal.hidden = false;
  }

  window.__openKidItem = openDetailModal;

  function renderSavedDrawer() {
    const savedItems = state.allItems.filter((it) => state.savedIds.has(it.id));
    if (savedItems.length === 0) {
      els.savedList.innerHTML = `<div class="empty-state" style="padding:28px 14px;"><p>還沒有收藏任何場館或活動喔！<br/>點卡片右上角的 🤍 就能加入口袋名單。</p></div>`;
      return;
    }
    els.savedList.innerHTML = savedItems
      .map((it) => {
        const meta = CATEGORY_META[it.category] || { icon: "✨", label: "" };
        const thumb = it.image_url
          ? `<img class="saved-thumb" src="${esc(it.image_url)}" alt="" loading="lazy" referrerpolicy="no-referrer" onerror="this.remove()" />`
          : "";
        return `
          <div class="saved-item">
            ${thumb}
            <div style="flex:1;min-width:0;">
              <h4 data-open-id="${esc(it.id)}">${meta.icon} ${esc(it.title)}</h4>
              <p>📍 ${esc(it.city)}${esc(it.district)} · ${esc(it.hours || "")}</p>
            </div>
            <button type="button" class="save-btn saved" data-save-id="${esc(it.id)}" title="移除收藏">✕</button>
          </div>
        `;
      })
      .join("");
  }

  function toggleSave(id) {
    if (state.savedIds.has(id)) {
      state.savedIds.delete(id);
    } else {
      state.savedIds.add(id);
    }
    persistSaved();
    renderCards();
    if (!els.savedDrawer.hidden) renderSavedDrawer();
    const modalBtn = document.getElementById("modal-save-btn");
    if (modalBtn && modalBtn.dataset.saveId === id) {
      modalBtn.textContent = state.savedIds.has(id) ? "❤️ 已在口袋名單" : "🤍 加入口袋名單";
    }
  }

  function resetAllFilters() {
    state.region = "";
    state.city = "";
    state.district = "";
    state.keyword = "";
    state.kind = "all";
    state.category = "";
    state.ages.clear();
    state.indoorOnly = false;
    state.freeOnly = false;
    state.weekendOnly = false;
    state.sort = "smart";

    els.inputKeyword.value = "";
    els.btnClearSearch.hidden = true;
    els.selectSort.value = "smart";
    els.regionPills.querySelectorAll(".region-pill").forEach((b) => b.classList.toggle("active", b.dataset.region === ""));
    els.kindTabs.forEach((b) => {
      const act = b.dataset.kind === "all";
      b.classList.toggle("active", act);
      b.setAttribute("aria-selected", String(act));
    });
    els.ageChips.forEach((b) => b.classList.remove("active"));
    els.toggleIndoor.classList.remove("active");
    els.toggleFree.classList.remove("active");
    els.toggleWeekend.classList.remove("active");

    populateCities();
    applyFiltersAndRender();
  }

  function bindEvents() {
    els.regionPills.addEventListener("click", (e) => {
      const btn = e.target.closest(".region-pill");
      if (!btn) return;
      state.region = btn.dataset.region || "";
      state.city = "";
      state.district = "";
      els.regionPills.querySelectorAll(".region-pill").forEach((b) => b.classList.toggle("active", b === btn));
      populateCities();
      applyFiltersAndRender();
    });

    els.selectCity.addEventListener("change", () => {
      state.city = els.selectCity.value;
      state.district = "";
      populateDistricts();
      applyFiltersAndRender();
    });

    els.selectDistrict.addEventListener("change", () => {
      state.district = els.selectDistrict.value;
      applyFiltersAndRender();
    });

    els.inputKeyword.addEventListener("input", () => {
      state.keyword = els.inputKeyword.value;
      els.btnClearSearch.hidden = !state.keyword;
      applyFiltersAndRender();
    });

    els.btnClearSearch.addEventListener("click", () => {
      state.keyword = "";
      els.inputKeyword.value = "";
      els.btnClearSearch.hidden = true;
      applyFiltersAndRender();
    });

    els.kindTabs.forEach((btn) => {
      btn.addEventListener("click", () => {
        state.kind = btn.dataset.kind;
        state.category = "";
        els.kindTabs.forEach((b) => {
          const act = b === btn;
          b.classList.toggle("active", act);
          b.setAttribute("aria-selected", String(act));
        });
        applyFiltersAndRender();
      });
    });

    els.categoryPills.addEventListener("click", (e) => {
      const btn = e.target.closest(".cat-pill");
      if (!btn) return;
      state.category = btn.dataset.cat || "";
      applyFiltersAndRender();
    });

    els.ageChips.forEach((btn) => {
      btn.addEventListener("click", () => {
        const age = btn.dataset.age;
        if (state.ages.has(age)) {
          state.ages.delete(age);
          btn.classList.remove("active");
        } else {
          state.ages.add(age);
          btn.classList.add("active");
        }
        applyFiltersAndRender();
      });
    });

    els.toggleIndoor.addEventListener("click", () => {
      state.indoorOnly = !state.indoorOnly;
      els.toggleIndoor.classList.toggle("active", state.indoorOnly);
      applyFiltersAndRender();
    });

    els.toggleFree.addEventListener("click", () => {
      state.freeOnly = !state.freeOnly;
      els.toggleFree.classList.toggle("active", state.freeOnly);
      applyFiltersAndRender();
    });

    els.toggleWeekend.addEventListener("click", () => {
      state.weekendOnly = !state.weekendOnly;
      els.toggleWeekend.classList.toggle("active", state.weekendOnly);
      applyFiltersAndRender();
    });

    els.btnResetFilters.addEventListener("click", resetAllFilters);
    els.btnEmptyReset.addEventListener("click", resetAllFilters);

    els.selectSort.addEventListener("change", () => {
      state.sort = els.selectSort.value;
      applyFiltersAndRender({ resetPage: false });
    });

    els.btnViewList.addEventListener("click", () => {
      state.view = "list";
      els.btnViewList.classList.add("active");
      els.btnViewMap.classList.remove("active");
      els.mapSection.hidden = true;
      els.cardsSection.hidden = false;
    });

    els.btnViewMap.addEventListener("click", () => {
      state.view = "map";
      els.btnViewMap.classList.add("active");
      els.btnViewList.classList.remove("active");
      els.mapSection.hidden = false;
      els.cardsSection.hidden = true;
      renderMap();
    });

    els.btnLoadMore.addEventListener("click", () => {
      state.visibleCount += PAGE_SIZE;
      renderCards();
    });

    // Event delegation for cards, modal, drawer
    document.addEventListener("click", (e) => {
      const saveBtn = e.target.closest("[data-save-id]");
      if (saveBtn) {
        e.stopPropagation();
        toggleSave(saveBtn.dataset.saveId);
        return;
      }
      const openBtn = e.target.closest("[data-open-id]");
      if (openBtn) {
        openDetailModal(openBtn.dataset.openId);
      }
    });

    els.btnRandom.addEventListener("click", () => {
      const pool = state.filtered.length > 0 ? state.filtered : state.allItems;
      if (pool.length === 0) return;
      const pick = pool[Math.floor(Math.random() * pool.length)];
      openDetailModal(pick.id);
    });

    els.btnSaved.addEventListener("click", () => {
      renderSavedDrawer();
      els.savedDrawer.hidden = false;
    });

    els.drawerClose.addEventListener("click", () => {
      els.savedDrawer.hidden = true;
    });

    els.savedDrawer.addEventListener("click", (e) => {
      if (e.target === els.savedDrawer) els.savedDrawer.hidden = true;
    });

    els.btnClearSaved.addEventListener("click", () => {
      state.savedIds.clear();
      persistSaved();
      renderSavedDrawer();
      renderCards();
    });

    els.modalClose.addEventListener("click", () => {
      els.detailModal.hidden = true;
    });

    els.detailModal.addEventListener("click", (e) => {
      if (e.target === els.detailModal) els.detailModal.hidden = true;
    });

    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape") {
        els.detailModal.hidden = true;
        els.savedDrawer.hidden = true;
        if (els.releaseModal) els.releaseModal.hidden = true;
      }
    });

    const openReleaseModal = () => {
      if (!els.releaseModal) return;
      const relData = window.TW_KIDS_RELEASES || { current_version: "1.1.0", releases: [] };
      const list = relData.releases || [];
      els.releaseList.innerHTML = list
        .map((r, idx) => {
          const items = (r.changes || []).map((c) => `<li>${esc(c)}</li>`).join("");
          return `
            <div class="release-item ${idx === 0 ? "latest" : ""}">
              <div class="release-item-head">
                <span class="release-ver-tag">v${esc(r.version)}${idx === 0 ? " · 最新版" : ""}</span>
                <span class="release-date">${esc(r.date || "")}</span>
              </div>
              <h3 class="release-title">${esc(r.title || "")}</h3>
              <ul class="release-changes">${items}</ul>
            </div>
          `;
        })
        .join("");
      els.releaseModal.hidden = false;
    };

    if (els.btnVersion) els.btnVersion.addEventListener("click", openReleaseModal);
    if (els.footerVersion) els.footerVersion.addEventListener("click", openReleaseModal);
    if (els.releaseModalClose) {
      els.releaseModalClose.addEventListener("click", () => {
        els.releaseModal.hidden = true;
      });
    }
    if (els.releaseModal) {
      els.releaseModal.addEventListener("click", (e) => {
        if (e.target === els.releaseModal) els.releaseModal.hidden = true;
      });
    }
  }

  function initVersionBadge() {
    const ver = window.TW_KIDS_RELEASES?.current_version;
    if (!ver) return;
    if (els.btnVersion) els.btnVersion.textContent = `v${ver}`;
    if (els.footerVersion) els.footerVersion.textContent = `v${ver}`;
  }

  async function loadActivitiesPayload() {
    const resp = await fetch(`data/activities.json?t=${Date.now()}`, { cache: "no-store" });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const payload = await resp.json();
    const rawItems = Array.isArray(payload.items) ? payload.items : [];
    // 前端即時二次把關：只顯示未結束且未來 3 個月內開始之活動
    state.allItems = rawItems.filter(isEventActiveWithinThreeMonths);

    const venueCnt = state.allItems.filter((x) => x.kind === "venue").length;
    const eventCnt = state.allItems.filter((x) => x.kind === "event").length;
    els.statTotal.textContent = String(state.allItems.length);
    els.statVenues.textContent = String(venueCnt);
    els.statEvents.textContent = String(eventCnt);
    els.statUpdated.textContent = payload.updated_date || "剛剛";

    const srcList = payload.sources || [];
    els.sourceBadges.innerHTML = [
      `<span class="source-badge">🟢 全台精選親子場館庫（22 縣市）</span>`,
      ...srcList.map((s) => {
        const icon = s.status === "ok" ? "🟢" : "🟡";
        return `<span class="source-badge">${icon} ${esc(s.source)}：${s.used ?? 0} 筆</span>`;
      }),
    ].join("");

    populateCities();
    applyFiltersAndRender({ resetPage: false });
  }

  async function initLocalServerMode() {
    const host = window.location.hostname || "";
    if (!window.location.protocol.startsWith("http") || host.endsWith(".github.io")) return;
    try {
      const r = await fetch("/api/health", { cache: "no-store" });
      if (!r.ok) return;
      const data = await r.json();
      if (!data || !data.ok) return;
      if (els.localBadge) els.localBadge.hidden = false;
      if (els.btnLocalSync) {
        els.btnLocalSync.hidden = false;
        els.btnLocalSync.addEventListener("click", async () => {
          if (els.btnLocalSync.disabled) return;
          els.btnLocalSync.disabled = true;
          const orig = els.btnLocalSync.textContent;
          els.btnLocalSync.textContent = "⏳ 同步中…";
          try {
            const sr = await fetch("/api/sync", { method: "POST" });
            const sj = await sr.json();
            if (!sr.ok || !sj.ok) throw new Error(sj.error || `HTTP ${sr.status}`);
            await loadActivitiesPayload();
            els.btnLocalSync.textContent = "✅ 已更新";
            setTimeout(() => { els.btnLocalSync.textContent = orig; }, 1800);
          } catch (e) {
            alert(`同步失敗：${e.message}`);
            els.btnLocalSync.textContent = orig;
          } finally {
            els.btnLocalSync.disabled = false;
          }
        });
      }
    } catch {
      // Static server without /api/health — keep Local controls hidden
    }
  }

  async function init() {
    initVersionBadge();
    loadSaved();
    bindEvents();
    initLocalServerMode();
    try {
      await loadActivitiesPayload();
    } catch (err) {
      els.resultSummary.textContent = `資料載入失敗：${err.message}（若以 file:// 直接開啟，請改用本機伺服器預覽）`;
    }
  }

  init();
})();
