/* Local Leaflet-compatible runtime for the map features used by this app. */
(function () {
  const TILE_SIZE = 256;
  const clamp = (value, min, max) => Math.max(min, Math.min(max, value));

  function project(lat, lng, zoom) {
    const safeLat = clamp(lat, -85.05112878, 85.05112878);
    const sin = Math.sin(safeLat * Math.PI / 180);
    const scale = TILE_SIZE * 2 ** zoom;
    return {
      x: (lng + 180) / 360 * scale,
      y: (0.5 - Math.log((1 + sin) / (1 - sin)) / (4 * Math.PI)) * scale,
    };
  }

  function unproject(x, y, zoom) {
    const scale = TILE_SIZE * 2 ** zoom;
    const lng = x / scale * 360 - 180;
    const n = Math.PI - 2 * Math.PI * y / scale;
    const lat = 180 / Math.PI * Math.atan(0.5 * (Math.exp(n) - Math.exp(-n)));
    return [lat, lng];
  }

  class Map {
    constructor(id) {
      this.el = typeof id === 'string' ? document.getElementById(id) : id;
      this.el.classList.add('leaflet-container');
      this.zoom = 10;
      this.center = [0, 0];
      this.layers = [];
      this.drag = null;
      this.tilePane = document.createElement('div');
      this.tilePane.className = 'leaflet-pane leaflet-tile-pane';
      this.overlayPane = document.createElement('div');
      this.overlayPane.className = 'leaflet-pane leaflet-overlay-pane';
      this.popupPane = document.createElement('div');
      this.popupPane.className = 'leaflet-pane leaflet-popup-pane';
      this.el.append(this.tilePane, this.overlayPane, this.popupPane);
      this.bindInteractions();
    }

    bindInteractions() {
      this.el.addEventListener('wheel', (event) => {
        event.preventDefault();
        const nextZoom = clamp(this.zoom + (event.deltaY < 0 ? 1 : -1), 3, 19);
        if (nextZoom !== this.zoom) this.setView(this.center, nextZoom);
      }, { passive: false });
      this.el.addEventListener('pointerdown', (event) => {
        if (event.button !== 0) return;
        this.el.setPointerCapture(event.pointerId);
        this.drag = { x: event.clientX, y: event.clientY, center: [...this.center] };
      });
      this.el.addEventListener('pointermove', (event) => {
        if (!this.drag) return;
        const start = project(this.drag.center[0], this.drag.center[1], this.zoom);
        const next = unproject(start.x - (event.clientX - this.drag.x), start.y - (event.clientY - this.drag.y), this.zoom);
        this.center = [clamp(next[0], -85, 85), next[1]];
        this.render();
      });
      this.el.addEventListener('pointerup', () => { this.drag = null; });
      this.el.addEventListener('pointercancel', () => { this.drag = null; });
    }

    setView(center, zoom) {
      this.center = [Number(center[0]), Number(center[1])];
      this.zoom = zoom ?? this.zoom;
      this.render();
      return this;
    }

    getZoom() { return this.zoom; }
    invalidateSize() { this.render(); return this; }

    fitBounds(bounds, options = {}) {
      if (!bounds.length) return this;
      const lats = bounds.map((bound) => bound[0]);
      const lngs = bounds.map((bound) => bound[1]);
      this.center = [(Math.min(...lats) + Math.max(...lats)) / 2, (Math.min(...lngs) + Math.max(...lngs)) / 2];
      this.zoom = options.maxZoom || this.zoom;
      return this.render();
    }

    addLayer(layer) {
      this.layers.push(layer);
      layer._map = this;
      layer._addTo(this);
      this.render();
      return this;
    }

    render() {
      this.layers.forEach((layer) => layer._render?.());
      return this;
    }

    point(lat, lng) {
      const rect = this.el.getBoundingClientRect();
      const center = project(this.center[0], this.center[1], this.zoom);
      const point = project(lat, lng, this.zoom);
      return { x: rect.width / 2 + point.x - center.x, y: rect.height / 2 + point.y - center.y };
    }
  }

  class TileLayer {
    constructor(url, options) { this.url = url; this.options = options || {}; }
    addTo(map) { map.addLayer(this); return this; }
    _addTo(map) {
      this.el = document.createElement('div');
      this.el.className = 'leaflet-layer';
      map.tilePane.append(this.el);
    }
    _render() {
      const map = this._map;
      const rect = map.el.getBoundingClientRect();
      if (!rect.width || !rect.height) return;
      const zoom = map.zoom;
      this.el.textContent = '';
      const center = project(map.center[0], map.center[1], zoom);
      const left = center.x - rect.width / 2;
      const top = center.y - rect.height / 2;
      const minX = Math.floor(left / TILE_SIZE);
      const maxX = Math.floor((left + rect.width) / TILE_SIZE);
      const minY = Math.floor(top / TILE_SIZE);
      const maxY = Math.floor((top + rect.height) / TILE_SIZE);
      const maxTile = 2 ** zoom;
      for (let x = minX; x <= maxX; x += 1) {
        for (let y = minY; y <= maxY; y += 1) {
          if (y < 0 || y >= maxTile) continue;
          const tileX = ((x % maxTile) + maxTile) % maxTile;
          const img = document.createElement('img');
          img.className = 'leaflet-tile leaflet-tile-loaded';
          img.src = this.url.replace('{s}', 'a').replace('{z}', zoom).replace('{x}', tileX).replace('{y}', y);
          img.draggable = false;
          img.style.cssText = `width:${TILE_SIZE}px;height:${TILE_SIZE}px;transform:translate(${x * TILE_SIZE - left}px,${y * TILE_SIZE - top}px);`;
          this.el.append(img);
        }
      }
    }
  }

  class LayerGroup {
    constructor() { this.layers = []; }
    addTo(map) { map.addLayer(this); return this; }
    clearLayers() { this.layers.forEach((layer) => layer.el?.remove()); this.layers = []; }
    addLayer(layer) { this.layers.push(layer); if (this._map) layer.addTo(this._map); }
    _addTo(map) { this._map = map; this.layers.forEach((layer) => layer.addTo(map)); }
    _render() { this.layers.forEach((layer) => layer._render?.()); }
  }

  class CircleMarker {
    constructor(latlng, options) { this.latlng = latlng; this.options = options || {}; this.handlers = {}; }
    addTo(groupOrMap) { groupOrMap.addLayer(this); return this; }
    bindPopup(html) { this.popupHtml = html; return this; }
    on(name, handler) { this.handlers[name] = handler; return this; }
    openPopup() {
      if (!this.popupHtml) return;
      const map = this._map;
      map.popupPane.textContent = '';
      const point = map.point(this.latlng[0], this.latlng[1]);
      const popup = document.createElement('div');
      popup.className = 'leaflet-popup';
      popup.style.transform = `translate(${point.x - 150}px,${point.y - 10}px)`;
      popup.innerHTML = `<div class="leaflet-popup-content-wrapper"><div class="leaflet-popup-content">${this.popupHtml}</div></div><div class="leaflet-popup-tip-container"><div class="leaflet-popup-tip"></div></div>`;
      map.popupPane.append(popup);
    }
    closePopup() { this._map.popupPane.textContent = ''; }
    _addTo(map) {
      this._map = map;
      this.el = document.createElement('div');
      this.el.className = 'leaflet-interactive';
      this.el.title = this.options.title || '';
      this.el.style.cssText = `position:absolute;width:${this.options.radius * 2}px;height:${this.options.radius * 2}px;border-radius:50%;border:${this.options.weight}px solid ${this.options.color};background:${this.options.fillColor};opacity:${this.options.fillOpacity};box-sizing:border-box;`;
      this.el.addEventListener('mouseover', () => this.handlers.mouseover?.());
      this.el.addEventListener('mouseout', () => this.handlers.mouseout?.());
      map.overlayPane.append(this.el);
    }
    _render() {
      const point = this._map.point(this.latlng[0], this.latlng[1]);
      const radius = this.options.radius || 8;
      this.el.style.transform = `translate(${point.x - radius}px,${point.y - radius}px)`;
    }
  }

  window.L = {
    map: (id) => new Map(id),
    tileLayer: (url, options) => new TileLayer(url, options),
    layerGroup: () => new LayerGroup(),
    circleMarker: (latlng, options) => new CircleMarker(latlng, options),
  };
}());
