/* Local Leaflet-compatible runtime shim for the map features used by this app. */
(function () {
  function project(lat, lng, zoom) {
    const sin = Math.sin(lat * Math.PI / 180);
    const scale = 256 * 2 ** zoom;
    return { x: (lng + 180) / 360 * scale, y: (0.5 - Math.log((1 + sin) / (1 - sin)) / (4 * Math.PI)) * scale };
  }
  function unproject(x, y, zoom) {
    const scale = 256 * 2 ** zoom;
    const lng = x / scale * 360 - 180;
    const n = Math.PI - 2 * Math.PI * y / scale;
    const lat = 180 / Math.PI * Math.atan(0.5 * (Math.exp(n) - Math.exp(-n)));
    return [lat, lng];
  }
  class Map {
    constructor(id) {
      this.el = typeof id === 'string' ? document.getElementById(id) : id;
      this.el.classList.add('leaflet-container');
      this.zoom = 10; this.center = [0, 0]; this.layers = [];
      this.tilePane = document.createElement('div'); this.tilePane.className = 'leaflet-pane leaflet-tile-pane';
      this.overlayPane = document.createElement('div'); this.overlayPane.className = 'leaflet-pane leaflet-overlay-pane';
      this.popupPane = document.createElement('div'); this.popupPane.className = 'leaflet-pane leaflet-popup-pane';
      this.el.append(this.tilePane, this.overlayPane, this.popupPane);
    }
    setView(center, zoom) { this.center = center; this.zoom = zoom ?? this.zoom; this.render(); return this; }
    getZoom() { return this.zoom; }
    invalidateSize() { this.render(); return this; }
    fitBounds(bounds, options = {}) {
      if (!bounds.length) return this;
      const lats = bounds.map(b => b[0]), lngs = bounds.map(b => b[1]);
      this.center = [(Math.min(...lats) + Math.max(...lats)) / 2, (Math.min(...lngs) + Math.max(...lngs)) / 2];
      this.zoom = options.maxZoom || this.zoom;
      return this.render();
    }
    addLayer(layer) { this.layers.push(layer); layer._map = this; layer._addTo(this); this.render(); return this; }
    render() { this.layers.forEach(layer => layer._render?.()); return this; }
    point(lat, lng) {
      const rect = this.el.getBoundingClientRect();
      const center = project(this.center[0], this.center[1], this.zoom);
      const p = project(lat, lng, this.zoom);
      return { x: rect.width / 2 + p.x - center.x, y: rect.height / 2 + p.y - center.y };
    }
  }
  class TileLayer {
    constructor(url, options) { this.url = url; this.options = options || {}; }
    addTo(map) { map.addLayer(this); return this; }
    _addTo(map) { this.el = document.createElement('div'); this.el.className = 'leaflet-layer'; map.tilePane.append(this.el); }
    _render() {
      const map = this._map, rect = map.el.getBoundingClientRect(), z = map.zoom;
      this.el.textContent = '';
      const c = project(map.center[0], map.center[1], z), left = c.x - rect.width / 2, top = c.y - rect.height / 2;
      const minX = Math.floor(left / 256), maxX = Math.floor((left + rect.width) / 256);
      const minY = Math.floor(top / 256), maxY = Math.floor((top + rect.height) / 256), maxTile = 2 ** z;
      for (let x = minX; x <= maxX; x++) for (let y = minY; y <= maxY; y++) if (y >= 0 && y < maxTile) {
        const img = document.createElement('img'); img.className = 'leaflet-tile leaflet-tile-loaded';
        const tx = ((x % maxTile) + maxTile) % maxTile;
        img.src = this.url.replace('{s}', 'a').replace('{z}', z).replace('{x}', tx).replace('{y}', y);
        img.style.cssText = `width:256px;height:256px;transform:translate(${x * 256 - left}px,${y * 256 - top}px);`;
        this.el.append(img);
      }
    }
  }
  class LayerGroup {
    constructor() { this.layers = []; }
    addTo(map) { map.addLayer(this); return this; }
    clearLayers() { this.layers.forEach(l => l.el?.remove()); this.layers = []; }
    addLayer(layer) { this.layers.push(layer); if (this._map) layer.addTo(this._map); }
    _addTo(map) { this._map = map; this.layers.forEach(l => l.addTo(map)); }
    _render() { this.layers.forEach(l => l._render?.()); }
  }
  class CircleMarker {
    constructor(latlng, options) { this.latlng = latlng; this.options = options || {}; this.handlers = {}; }
    addTo(groupOrMap) { groupOrMap.addLayer ? groupOrMap.addLayer(this) : groupOrMap.addLayer(this); return this; }
    bindPopup(html) { this.popupHtml = html; return this; }
    on(name, handler) { this.handlers[name] = handler; return this; }
    openPopup() { if (!this.popupHtml) return; const map = this._map; map.popupPane.textContent = ''; const p = map.point(this.latlng[0], this.latlng[1]); const pop = document.createElement('div'); pop.className = 'leaflet-popup'; pop.style.transform = `translate(${p.x - 150}px,${p.y - 10}px)`; pop.innerHTML = `<div class="leaflet-popup-content-wrapper"><div class="leaflet-popup-content">${this.popupHtml}</div></div><div class="leaflet-popup-tip-container"><div class="leaflet-popup-tip"></div></div>`; map.popupPane.append(pop); }
    closePopup() { this._map.popupPane.textContent = ''; }
    _addTo(map) { this._map = map; this.el = document.createElement('div'); this.el.className = 'leaflet-interactive'; this.el.title = this.options.title || ''; this.el.style.cssText = `position:absolute;width:${this.options.radius * 2}px;height:${this.options.radius * 2}px;border-radius:50%;border:${this.options.weight}px solid ${this.options.color};background:${this.options.fillColor};opacity:${this.options.fillOpacity};box-sizing:border-box;`; this.el.addEventListener('mouseover', () => this.handlers.mouseover?.()); this.el.addEventListener('mouseout', () => this.handlers.mouseout?.()); map.overlayPane.append(this.el); }
    _render() { const p = this._map.point(this.latlng[0], this.latlng[1]); const r = this.options.radius || 8; this.el.style.transform = `translate(${p.x - r}px,${p.y - r}px)`; }
  }
  window.L = { map: id => new Map(id), tileLayer: (u, o) => new TileLayer(u, o), layerGroup: () => new LayerGroup(), circleMarker: (ll, o) => new CircleMarker(ll, o) };
}());
