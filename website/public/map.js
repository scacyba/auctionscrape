const monthInput = document.querySelector('#month');
const daySelect = document.querySelector('#day');
const loadButton = document.querySelector('#load');
const statusEl = document.querySelector('#status');
const itemsEl = document.querySelector('#items');
const map = L.map('map').setView([34.6618, 133.9350], 10);
const markers = L.layerGroup().addTo(map);

L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
  maxZoom: 19,
  attribution: '&copy; OpenStreetMap contributors',
}).addTo(map);

function pad(value) { return String(value).padStart(2, '0'); }
function selectedDate() { return `${monthInput.value}-${daySelect.value}`; }
function setStatus(message, isError = false) { statusEl.textContent = message; statusEl.classList.toggle('error', isError); }
function setDefaultMonth() { const now = new Date(); monthInput.value = `${now.getFullYear()}-${pad(now.getMonth() + 1)}`; }
function populateDays() {
  const [year, month] = monthInput.value.split('-').map(Number);
  const selected = daySelect.value;
  daySelect.textContent = '';
  if (!year || !month) return;
  const daysInMonth = new Date(year, month, 0).getDate();
  for (let day = 1; day <= daysInMonth; day += 1) {
    const option = document.createElement('option');
    option.value = pad(day);
    option.textContent = `${day}日`;
    daySelect.append(option);
  }
  const today = new Date();
  const defaultDay = year === today.getFullYear() && month === today.getMonth() + 1 ? pad(today.getDate()) : '01';
  daySelect.value = selected && Number(selected) <= daysInMonth ? selected : defaultDay;
}

function escapeHtml(value) {
  return String(value).replace(/[&<>'"]/g, char => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;' }[char]));
}
function popupHtml(item) {
  return `<div class="popup"><strong>${escapeHtml(item.title || '無題の物件')}</strong><dl>`
    + `<div class="info-row"><dt>所在地</dt><dd>${escapeHtml(item.location || '未取得')}</dd></div>`
    + `<div class="info-row"><dt>売却基準価額</dt><dd>${escapeHtml(item.salePrice || '未取得')}</dd></div>`
    + `<div class="info-row"><dt>入札期間</dt><dd>${escapeHtml(item.bidPeriod || '未取得')}</dd></div>`
    + `</dl></div>`;
}
function manifestPoint(item) {
  const lat = Number(item.lat);
  const lng = Number(item.lng);
  return Number.isFinite(lat) && Number.isFinite(lng) ? { lat, lng, source: 'manifest' } : null;
}
async function fallbackGeocode(item) {
  if (!item.location) return null;
  const cacheKey = `geocode:fallback:v1:${item.location}`;
  const cached = localStorage.getItem(cacheKey);
  if (cached) return JSON.parse(cached);
  const response = await fetch(`/api/geocode?location=${encodeURIComponent(item.location)}`);
  if (!response.ok) {
    localStorage.setItem(cacheKey, 'null');
    return null;
  }
  const point = await response.json();
  localStorage.setItem(cacheKey, JSON.stringify(point));
  return point;
}
async function itemPoint(item) {
  return manifestPoint(item) || fallbackGeocode(item);
}
function listItemHtml(item, point) {
  const status = point ? (point.source === 'manifest' ? 'manifest座標で表示' : 'fallback geocodingで表示') : '地図未取得';
  return `<strong>${escapeHtml(item.title || '無題の物件')}</strong><dl>`
    + `<div class="info-row"><dt>所在地</dt><dd>${escapeHtml(item.location || '未取得')}</dd></div>`
    + `<div class="info-row"><dt>売却基準価額</dt><dd>${escapeHtml(item.salePrice || '未取得')}</dd></div>`
    + `<div class="info-row"><dt>入札期間</dt><dd>${escapeHtml(item.bidPeriod || '未取得')}</dd></div>`
    + `<div class="info-row"><dt>地図</dt><dd>${status}</dd></div>`
    + `</dl>`
    + (item.pdf ? `<a class="download" href="/api/pdf?key=${encodeURIComponent(item.pdf)}">PDF</a>` : '');
}
function renderList(item, point, marker) {
  const li = document.createElement('li');
  li.className = 'item';
  li.innerHTML = listItemHtml(item, point);
  if (point) {
    li.addEventListener('mouseenter', () => {
      map.invalidateSize();
      map.setView([point.lat, point.lng], Math.max(map.getZoom(), 15));
      marker?.openPopup();
    });
    li.addEventListener('mouseleave', () => marker?.closePopup());
  }
  itemsEl.append(li);
}
function createMarker(item, point) {
  if (!point) return null;
  const marker = L.circleMarker([point.lat, point.lng], {
    title: item.location,
    radius: 8,
    color: '#0f172a',
    weight: 2,
    fillColor: '#ef4444',
    fillOpacity: 0.95,
  }).bindPopup(popupHtml(item));
  marker.on('mouseover', () => marker.openPopup());
  marker.on('mouseout', () => marker.closePopup());
  marker.addTo(markers);
  return marker;
}
async function loadItems() {
  if (!monthInput.value || !daySelect.value) return setStatus('年月と日付を選択してください。', true);
  loadButton.disabled = true;
  markers.clearLayers();
  itemsEl.textContent = '';
  setStatus('読み込み中...');
  requestAnimationFrame(() => map.invalidateSize());
  try {
    const response = await fetch(`/api/items?date=${encodeURIComponent(selectedDate())}`);
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(payload.error || `取得に失敗しました (${response.status})`);
    const items = (Array.isArray(payload.items) ? payload.items : []).filter(item => item.location || manifestPoint(item));
    if (!items.length) { setStatus('地図表示できる所在地または座標付き物件がありません。'); return; }
    const bounds = [];
    let fallbackCount = 0;
    for (const item of items) {
      const point = await itemPoint(item);
      if (point && point.source !== 'manifest') fallbackCount += 1;
      const marker = createMarker(item, point);
      if (point) bounds.push([point.lat, point.lng]);
      renderList(item, point, marker);
    }
    map.invalidateSize();
    if (bounds.length) map.fitBounds(bounds, { padding: [32, 32], maxZoom: 16 });
    const fallbackText = fallbackCount ? ` / fallback geocoding: ${fallbackCount}件` : '';
    setStatus(`${payload.date} の物件: ${items.length}件 / 地図表示: ${bounds.length}件${fallbackText}`);
  } catch (error) {
    setStatus(error.message, true);
  } finally {
    loadButton.disabled = false;
  }
}
monthInput.addEventListener('change', populateDays);
loadButton.addEventListener('click', loadItems);
window.addEventListener('load', () => setTimeout(() => map.invalidateSize(), 0));
window.addEventListener('resize', () => map.invalidateSize());
setDefaultMonth();
populateDays();
