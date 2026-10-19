// The editor: the document, the view, the current tool and selection, the map canvas and its
// mouse/keyboard handling, and the commands the menus call.

import {
  MapDocument, Item, Layer, annotationLayer, areaItem, basemapLayer, colorLayer, itemTitle, lineItem, moveItem,
  serviceLayer, symbolItem, textItem, WEB_KINDS, newId,
} from './doc';
import { View, lonlatToMerc, mercToLonlat, scaleDenominator } from './geo';
import { TileCache } from './tiles';
import { Hit, distToPolyline, onSymbolLoaded, pointInPoly, renderMap, sourceFor, symbolsReady } from './render';
import { BASEMAPS } from './shared/basemaps';
import { downloadBlob, loadAutosave, pickFile, safeName, saveAutosave } from './storage';
import { confirmDialog, dialog, el, message, row, select } from './ui/dom';

export type Tool = 'select' | 'pan' | 'text' | 'symbol' | 'line' | 'area';
export const TOOLS: { id: Tool; label: string; key: string; hint: string }[] = [
  { id: 'select', label: 'Select and move', key: 'V', hint: 'Click to select, drag to move. Drag a corner point of a line or area to reshape it.' },
  { id: 'pan', label: 'Pan', key: 'H', hint: 'Drag to move the map. Hold Space with any tool to pan.' },
  { id: 'text', label: 'Text', key: 'T', hint: 'Click on the map to place a label.' },
  { id: 'symbol', label: 'Symbol', key: 'S', hint: 'Click on the map to place a symbol.' },
  { id: 'line', label: 'Line', key: 'L', hint: 'Click to add points, double-click or Enter to finish, Esc to cancel.' },
  { id: 'area', label: 'Area', key: 'A', hint: 'Click to add corners, double-click or Enter to finish, Esc to cancel.' },
];

export interface ToolOptions {
  preset: string; symbol: string; symbolColor: string; symbolSize: number; badge: boolean;
  lineColor: string; lineWidth: number; lineDash: string; areaFill: string;
}

type Listener = () => void;

export class Editor {
  doc: MapDocument;
  view: View;
  tool: Tool = 'select';
  selLayer: string | null = null;
  selItem: string | null = null;
  opts: ToolOptions = {
    preset: 'place', symbol: 'pin', symbolColor: '#b03a2e', symbolSize: 22, badge: false,
    lineColor: '#b03a2e', lineWidth: 2.5, lineDash: 'solid', areaFill: '#b03a2e',
  };
  tiles = new TileCache();
  canvas: HTMLCanvasElement;
  ctx: CanvasRenderingContext2D;
  hits: Hit[] = [];
  drawing: number[][] | null = null;           // lon/lat points of a line or area being drawn
  private hover: [number, number] | null = null;
  private spaceDown = false;
  private frame = 0;
  private listeners: Record<string, Listener[]> = {};
  private autosaveTimer = 0;
  private lastUnsupported = '';

  constructor(canvas: HTMLCanvasElement) {
    this.canvas = canvas;
    this.ctx = canvas.getContext('2d')!;
    this.doc = MapDocument.create();
    this.view = new View(...this.doc.view, 1, 1);
    this.selLayer = this.doc.annotationLayers()[0]?.id || null;
    this.tiles.onLoad = () => this.redraw();
    onSymbolLoaded(() => this.redraw());
    this.bindDoc();
    new ResizeObserver(() => this.resize()).observe(canvas.parentElement!);
    this.bindPointer();
    this.resize();
  }

  // ---------------------------------------------------------------- events
  on(ev: string, f: Listener) { (this.listeners[ev] ||= []).push(f); }
  emit(ev: string) { for (const f of this.listeners[ev] || []) f(); }
  status(msg: string) { this.statusMsg = msg; this.emit('status'); }
  statusMsg = '';

  private bindDoc() {
    this.doc.onChange = () => {
      this.redraw();
      this.emit('change');
      this.scheduleAutosave();
    };
  }

  /** Replace the document (new / open / restore). */
  setDoc(d: MapDocument, warn = false) {
    this.doc = d;
    this.bindDoc();
    this.view.cx = d.view[0]; this.view.cy = d.view[1]; this.view.zoom = d.view[2];
    this.selItem = null;
    this.selLayer = d.annotationLayers().slice(-1)[0]?.id || d.layers[d.layers.length - 1]?.id || null;
    this.drawing = null;
    this.redraw();
    this.emit('change'); this.emit('selection'); this.emit('view');
    const unsupported = d.layers.filter(L => !WEB_KINDS.includes(L.kind));
    if (warn && unsupported.length) {
      message('Some layers aren’t shown yet',
        `The browser version can’t draw ${unsupported.map(L => `“${L.name}”`).join(', ')} yet (data, terrain and grid layers come in later versions). ` +
        'They stay in the map and are saved back with it, so nothing is lost.');
    }
  }

  // ---------------------------------------------------------------- selection
  select(layerId: string | null, itemId: string | null = null) {
    this.selLayer = layerId;
    this.selItem = itemId;
    this.redraw();
    this.emit('selection');
  }

  selectedLayer(): Layer | null { return this.doc.layer(this.selLayer); }
  selectedItem(): Item | null { return this.selItem ? this.doc.findItem(this.selItem)[1] : null; }

  /** The annotation layer new labels and symbols go into (made if there is none). */
  targetLayer(): Layer {
    const sel = this.selectedLayer();
    if (sel && sel.kind === 'annotations' && !sel.locked) return sel;
    const ann = this.doc.annotationLayers().filter(L => !L.locked);
    if (ann.length) return ann[ann.length - 1];
    return this.doc.addLayer(annotationLayer(this.doc.uniqueName('Labels & symbols')));
  }

  setTool(t: Tool) {
    if (this.drawing && t !== this.tool) this.finishDrawing();
    this.tool = t;
    this.canvas.style.cursor = this.cursorFor();
    this.emit('tool');
    this.redraw();
  }

  private cursorFor() {
    if (this.spaceDown || this.tool === 'pan') return 'grab';
    if (this.tool === 'select') return 'default';
    return 'crosshair';
  }

  // ---------------------------------------------------------------- drawing
  resize() {
    const r = this.canvas.parentElement!.getBoundingClientRect();
    const dpr = window.devicePixelRatio || 1;
    this.view.width = Math.max(1, r.width);
    this.view.height = Math.max(1, r.height);
    this.canvas.width = Math.round(r.width * dpr);
    this.canvas.height = Math.round(r.height * dpr);
    this.redraw();
  }

  redraw() {
    if (this.frame) return;
    this.frame = requestAnimationFrame(() => { this.frame = 0; this.paint(); });
  }

  private paint() {
    const dpr = this.canvas.width / this.view.width;
    const ctx = this.ctx;
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.fillStyle = '#e8e6e1';
    ctx.fillRect(0, 0, this.canvas.width, this.canvas.height);
    this.hits = [];
    const unsupported = new Set<string>();
    renderMap(ctx, this.view, this.doc, this.tiles, { dpr, hits: this.hits, unsupported });
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    this.paintOverlay(ctx);
    const u = [...unsupported].join(', ');
    if (u !== this.lastUnsupported) { this.lastUnsupported = u; this.emit('status'); }
    this.updateAttribution();
  }

  unsupportedNames() { return this.lastUnsupported; }

  private paintOverlay(ctx: CanvasRenderingContext2D) {
    const sel = this.selItem ? this.hits.find(h => h.itemId === this.selItem) : null;
    ctx.lineWidth = 1.2;
    ctx.strokeStyle = '#2f8cff';
    ctx.setLineDash([]);
    if (sel) {
      if (sel.quad) {
        ctx.beginPath();
        sel.quad.forEach(([x, y], i) => i ? ctx.lineTo(x, y) : ctx.moveTo(x - 0, y));
        ctx.closePath();
        ctx.setLineDash([4, 3]); ctx.stroke(); ctx.setLineDash([]);
      }
      if (sel.poly) {
        ctx.beginPath();
        sel.poly.forEach(([x, y], i) => i ? ctx.lineTo(x, y) : ctx.moveTo(x, y));
        if (sel.kind === 'area') ctx.closePath();
        ctx.setLineDash([4, 3]); ctx.stroke(); ctx.setLineDash([]);
        for (const [x, y] of sel.poly) this.handle(ctx, x, y);
      }
    }
    if (this.drawing && this.drawing.length) {
      const pts = this.drawing.map(([lo, la]) => this.view.lonlatToPx(lo, la));
      if (this.hover) pts.push(this.hover);
      ctx.beginPath();
      pts.forEach(([x, y], i) => i ? ctx.lineTo(x, y) : ctx.moveTo(x, y));
      if (this.tool === 'area') {
        ctx.closePath();
        ctx.fillStyle = this.opts.areaFill; ctx.globalAlpha = 0.18; ctx.fill(); ctx.globalAlpha = 1;
      }
      ctx.strokeStyle = this.tool === 'area' ? this.opts.areaFill : this.opts.lineColor;
      ctx.lineWidth = this.tool === 'line' ? this.opts.lineWidth : 1.8;
      ctx.stroke();
      ctx.lineWidth = 1.2; ctx.strokeStyle = '#2f8cff';
      for (const [x, y] of pts.slice(0, this.drawing.length)) this.handle(ctx, x, y);
    }
  }

  private handle(ctx: CanvasRenderingContext2D, x: number, y: number) {
    ctx.fillStyle = '#ffffff';
    ctx.fillRect(x - 3.5, y - 3.5, 7, 7);
    ctx.strokeRect(x - 3.5, y - 3.5, 7, 7);
  }

  attribution(): string {
    const parts: string[] = [];
    for (const L of this.doc.layers) {
      if (!L.visible) continue;
      const src = sourceFor(L) as any;
      const a = src?.attribution;
      if (a && !parts.includes(a)) parts.push(a);
    }
    return parts.join(' · ');
  }

  private updateAttribution() {
    const a = document.getElementById('attribution');
    if (a) { const t = this.attribution(); a.textContent = t; a.style.display = t ? '' : 'none'; }
  }

  // ---------------------------------------------------------------- view
  saveView() { this.doc.view = [this.view.cx, this.view.cy, this.view.zoom]; this.scheduleAutosave(); }

  zoomBy(dz: number, px = this.view.width / 2, py = this.view.height / 2) {
    this.view.zoomAround(px, py, dz);
    this.saveView();
    this.redraw();
    this.emit('view');
  }

  zoomToSweden() {
    this.view.cx = 0.5444; this.view.cy = 0.277;
    this.view.zoom = Math.log2(Math.min(this.view.width / (0.105 * 256), this.view.height / (0.135 * 256)));
    this.saveView(); this.redraw(); this.emit('view');
  }

  /** Zoom to fit the items of the selected annotation layer (or the selected item). */
  zoomToSelection() {
    const it = this.selectedItem();
    const L = this.selectedLayer();
    const items: Item[] = it ? [it] : (L?.items || []);
    const pts: number[][] = [];
    for (const x of items) { if ('lon' in x) pts.push([x.lon, x.lat]); pts.push(...(x.coords || [])); }
    if (!pts.length) return;
    const m = pts.map(p => lonlatToMerc(p[0], p[1]));
    const xs = m.map(p => p[0]), ys = m.map(p => p[1]);
    const w = Math.max(...xs) - Math.min(...xs), h = Math.max(...ys) - Math.min(...ys);
    this.view.cx = (Math.max(...xs) + Math.min(...xs)) / 2;
    this.view.cy = (Math.max(...ys) + Math.min(...ys)) / 2;
    const z = Math.log2(Math.min(this.view.width * 0.8 / (Math.max(w, 1e-7) * 256), this.view.height * 0.8 / (Math.max(h, 1e-7) * 256)));
    this.view.zoom = Math.max(2, Math.min(17, z));
    this.saveView(); this.redraw(); this.emit('view');
  }

  scaleText() {
    const d = scaleDenominator(this.view);
    const r = d > 1e5 ? Math.round(d / 1000) * 1000 : d > 1e4 ? Math.round(d / 100) * 100 : Math.round(d / 10) * 10;
    return `1 : ${r.toLocaleString('sv-SE')}`;
  }

  hoverLonlat(): [number, number] | null { return this.hover ? this.view.toLonlat(...this.hover) : null; }

  // ---------------------------------------------------------------- pointer
  private hitTest(x: number, y: number): Hit | null {
    for (let i = this.hits.length - 1; i >= 0; i--) {
      const h = this.hits[i];
      const L = this.doc.layer(h.layerId);
      if (!L || L.locked) continue;
      if (h.quad && pointInPoly(x, y, h.quad)) return h;
      if (h.kind === 'line' && h.poly && distToPolyline(x, y, h.poly) < 6) return h;
      if (h.kind === 'area' && h.poly && (pointInPoly(x, y, h.poly) || distToPolyline(x, y, h.poly, true) < 6)) return h;
    }
    return null;
  }

  private vertexAt(x: number, y: number): number {
    const sel = this.selItem ? this.hits.find(h => h.itemId === this.selItem) : null;
    if (!sel?.poly) return -1;
    return sel.poly.findIndex(([px, py]) => Math.abs(px - x) <= 6 && Math.abs(py - y) <= 6);
  }

  private bindPointer() {
    const c = this.canvas;
    let drag: null | { kind: 'pan' | 'move' | 'vertex'; x: number; y: number; cx: number; cy: number; state?: string; vertex?: number; lastLL?: [number, number] } = null;
    const pos = (e: PointerEvent | MouseEvent | WheelEvent) => {
      const r = c.getBoundingClientRect();
      return [e.clientX - r.left, e.clientY - r.top] as [number, number];
    };

    c.addEventListener('pointerdown', e => {
      c.setPointerCapture(e.pointerId);
      const [x, y] = pos(e);
      if (e.button === 1 || e.button === 0 && (this.tool === 'pan' || this.spaceDown)) {
        drag = { kind: 'pan', x, y, cx: this.view.cx, cy: this.view.cy };
        c.style.cursor = 'grabbing';
        return;
      }
      if (e.button !== 0) return;
      const ll = this.view.toLonlat(x, y);
      if (this.tool === 'select') {
        const v = this.vertexAt(x, y);
        if (v >= 0) {
          drag = { kind: 'vertex', x, y, cx: 0, cy: 0, state: this.doc.state(), vertex: v };
          return;
        }
        const h = this.hitTest(x, y);
        if (h) {
          this.select(h.layerId, h.itemId);
          drag = { kind: 'move', x, y, cx: 0, cy: 0, state: this.doc.state(), lastLL: ll };
        } else {
          if (this.selItem) this.select(this.selLayer, null);
          drag = { kind: 'pan', x, y, cx: this.view.cx, cy: this.view.cy };   // dragging empty map pans
        }
      } else if (this.tool === 'text') {
        this.addItem(textItem(ll[0], ll[1], 'Text', this.opts.preset), 'Add text');
        this.emit('edit-text' as any);
      } else if (this.tool === 'symbol') {
        const o = this.opts;
        this.addItem(symbolItem(ll[0], ll[1], o.symbol, o.symbolColor, o.symbolSize, o.badge), 'Add symbol');
      } else if (this.tool === 'line' || this.tool === 'area') {
        if (e.detail >= 2) return;                       // the double-click that finishes
        (this.drawing ||= []).push(ll);
        this.redraw();
      }
    });

    c.addEventListener('pointermove', e => {
      const [x, y] = pos(e);
      this.hover = [x, y];
      this.emit('view');
      if (!drag) {
        if (this.drawing) this.redraw();
        if (this.tool === 'select' && !this.spaceDown) {
          c.style.cursor = this.vertexAt(x, y) >= 0 ? 'move' : this.hitTest(x, y) ? 'pointer' : 'default';
        }
        return;
      }
      if (drag.kind === 'pan') {
        const w = this.view.world;
        this.view.cx = drag.cx - (x - drag.x) / w;
        this.view.cy = drag.cy - (y - drag.y) / w;
        this.redraw();
      } else if (drag.kind === 'move') {
        const it = this.selectedItem();
        if (!it) return;
        const ll = this.view.toLonlat(x, y);
        moveItem(it, ll[0] - drag.lastLL![0], ll[1] - drag.lastLL![1]);
        drag.lastLL = ll;
        this.doc.changed();
      } else if (drag.kind === 'vertex') {
        const it = this.selectedItem();
        if (!it?.coords) return;
        it.coords[drag.vertex!] = this.view.toLonlat(x, y);
        this.doc.changed();
      }
    });

    const end = (e: PointerEvent) => {
      if (!drag) return;
      if (drag.kind === 'pan') { this.saveView(); c.style.cursor = this.cursorFor(); }
      else if (drag.state) {
        const moved = Math.hypot(pos(e)[0] - drag.x, pos(e)[1] - drag.y) > 1;
        if (moved) this.doc.pushUndo(drag.kind === 'vertex' ? 'Move point' : 'Move', drag.state);
      }
      drag = null;
    };
    c.addEventListener('pointerup', end);
    c.addEventListener('pointercancel', end);
    c.addEventListener('pointerleave', () => { this.hover = null; this.emit('view'); if (this.drawing) this.redraw(); });

    c.addEventListener('dblclick', () => { if (this.drawing) this.finishDrawing(); });

    c.addEventListener('wheel', e => {
      e.preventDefault();
      const [x, y] = pos(e);
      const dz = -e.deltaY * (e.deltaMode === 1 ? 0.05 : 0.0022) * (e.ctrlKey ? 2 : 1);
      this.zoomBy(Math.max(-1, Math.min(1, dz)), x, y);
    }, { passive: false });

    // two-finger pinch on touch screens
    const touches = new Map<number, [number, number]>();
    let pinch: { d: number; z: number } | null = null;
    c.addEventListener('pointerdown', e => {
      if (e.pointerType !== 'touch') return;
      touches.set(e.pointerId, pos(e));
      if (touches.size === 2) {
        const [a, b] = [...touches.values()];
        pinch = { d: Math.hypot(a[0] - b[0], a[1] - b[1]), z: this.view.zoom };
        drag = null;
      }
    });
    c.addEventListener('pointermove', e => {
      if (!touches.has(e.pointerId)) return;
      touches.set(e.pointerId, pos(e));
      if (pinch && touches.size === 2) {
        const [a, b] = [...touches.values()];
        const d = Math.hypot(a[0] - b[0], a[1] - b[1]);
        const target = pinch.z + Math.log2(d / pinch.d);
        this.zoomBy(target - this.view.zoom, (a[0] + b[0]) / 2, (a[1] + b[1]) / 2);
      }
    });
    const tend = (e: PointerEvent) => { touches.delete(e.pointerId); if (touches.size < 2) pinch = null; };
    c.addEventListener('pointerup', tend);
    c.addEventListener('pointercancel', tend);
  }

  setSpace(down: boolean) {
    if (this.spaceDown === down) return;
    this.spaceDown = down;
    this.canvas.style.cursor = this.cursorFor();
  }

  // ---------------------------------------------------------------- editing commands
  addItem(it: Item, label: string) {
    this.doc.checkpoint(label);
    const L = this.targetLayer();
    L.items.push(it);
    if (!L.visible) L.visible = true;
    this.selLayer = L.id;
    this.selItem = it.id;
    this.doc.changed();
    this.emit('selection');
  }

  finishDrawing() {
    const pts = this.drawing;
    this.drawing = null;
    if (!pts) return;
    // drop a duplicate last point from the finishing double-click
    while (pts.length > 1 && Math.hypot(pts[pts.length - 1][0] - pts[pts.length - 2][0], pts[pts.length - 1][1] - pts[pts.length - 2][1]) < 1e-9) pts.pop();
    if (this.tool === 'line' && pts.length >= 2) {
      this.addItem(lineItem(pts, this.opts.lineColor, this.opts.lineWidth, this.opts.lineDash), 'Add line');
    } else if (this.tool === 'area' && pts.length >= 3) {
      this.addItem(areaItem(pts, this.opts.areaFill), 'Add area');
    }
    this.redraw();
  }

  cancelDrawing() { this.drawing = null; this.redraw(); }

  deleteSelection() {
    if (this.selItem) {
      const [, it] = this.doc.findItem(this.selItem);
      this.doc.checkpoint(`Delete ${it ? itemTitle(it) : 'item'}`);
      this.doc.removeItem(this.selItem);
      this.selItem = null;
      this.doc.changed();
      this.emit('selection');
    } else if (this.selLayer) {
      this.deleteLayer(this.selLayer);
    }
  }

  deleteLayer(id: string) {
    const L = this.doc.layer(id);
    if (!L) return;
    const i = this.doc.index(id);
    this.doc.checkpoint(`Delete layer ${L.name}`);
    this.doc.removeLayer(id);
    const next = this.doc.layers[Math.min(i, this.doc.layers.length - 1)];
    this.selLayer = next?.id || null;
    this.selItem = null;
    this.doc.changed();
    this.emit('selection');
  }

  duplicate() {
    if (this.selItem) {
      const [L, it] = this.doc.findItem(this.selItem);
      if (!L || !it) return;
      this.doc.checkpoint('Duplicate');
      const c = JSON.parse(JSON.stringify(it));
      c.id = newId();
      const d = 12 / this.view.world * 360;          // offset a little so the copy is visible
      moveItem(c, d, -d * 0.6);
      L.items.splice(L.items.indexOf(it) + 1, 0, c);
      this.selItem = c.id;
      this.doc.changed(); this.emit('selection');
    } else if (this.selLayer) {
      this.doc.checkpoint('Duplicate layer');
      const c = this.doc.duplicateLayer(this.selLayer);
      if (c) this.selLayer = c.id;
      this.doc.changed(); this.emit('selection');
    }
  }

  undo() { const l = this.doc.undo(); this.validateSelection(); if (l) this.status(`Undid ${l}`); }
  redo() { const l = this.doc.redo(); this.validateSelection(); if (l) this.status(`Redid ${l}`); }

  private validateSelection() {
    if (this.selItem && !this.doc.findItem(this.selItem)[1]) this.selItem = null;
    if (this.selLayer && !this.doc.layer(this.selLayer)) this.selLayer = this.doc.layers[this.doc.layers.length - 1]?.id || null;
    this.emit('selection');
  }

  addBasemap(id: string) {
    this.doc.checkpoint('Add background map');
    const src = BASEMAPS.find(b => b.id === id);
    const L = this.doc.addBackground(basemapLayer(id, this.doc.uniqueName(src?.name || id)));
    this.select(L.id);
    this.doc.changed();
  }

  addColorLayer() {
    this.doc.checkpoint('Add colour');
    const L = this.doc.addBackground(colorLayer());
    this.select(L.id);
    this.doc.changed();
  }

  addAnnotationLayer() {
    this.doc.checkpoint('Add layer');
    const sel = this.selectedLayer();
    const L = this.doc.addLayer(annotationLayer(this.doc.uniqueName('Labels & symbols')), sel && sel.kind === 'annotations' ? sel.id : null);
    this.select(L.id);
    this.doc.changed();
  }

  async addServiceDialog() {
    const kind = select([['xyz', 'Tiles (XYZ) – {z}/{x}/{y}'], ['wms', 'WMS (GetMap)']], 'xyz', () => {
      layersRow.style.display = kind.value === 'wms' ? '' : 'none';
    });
    const url = el('input', { type: 'url', placeholder: 'https://…/{z}/{x}/{y}.png' });
    const name = el('input', { type: 'text', placeholder: 'Map service' });
    const layers = el('input', { type: 'text', placeholder: 'layer1,layer2' });
    const attribution = el('input', { type: 'text', placeholder: '© …' });
    const layersRow = row('WMS layers', layers);
    layersRow.style.display = 'none';
    const r = await dialog('Add map service', [
      el('p', {}, 'Any tile or WMS service in Web Mercator. The server has to allow use from other websites (CORS) for the map to be exported as a picture.'),
      row('Type', kind), row('Address', url), layersRow, row('Name', name), row('Credits', attribution),
    ], [{ label: 'Cancel', value: null }, { label: 'Add', value: 'ok', primary: true }]);
    if (r !== 'ok' || !url.value.trim()) return;
    const src = {
      id: 'user-' + newId(), name: name.value.trim() || 'Map service', urls: [url.value.trim()], kind: kind.value,
      max_zoom: 19, min_zoom: 0, attribution: attribution.value.trim(), wms_layers: layers.value.trim(), note: '',
      role: 'overlay', user_defined: true, labels: [], crs: 3857, tile_size: 256,
    };
    this.doc.checkpoint('Add map service');
    const L = this.doc.addBackground(serviceLayer(src));
    this.select(L.id);
    this.doc.changed();
  }

  // ---------------------------------------------------------------- files
  private scheduleAutosave() {
    clearTimeout(this.autosaveTimer);
    this.autosaveTimer = window.setTimeout(() => {
      saveAutosave({ body: this.doc.body(), extra: this.doc.extra, fileName: this.doc.fileName, savedAt: Date.now() })
        .then(() => this.emit('saved' as any));
    }, 700);
  }

  async restoreAutosave(): Promise<boolean> {
    const a = await loadAutosave();
    if (!a?.body) return false;
    try {
      const d = MapDocument.fromBody(a.body, a.extra || []);
      d.fileName = a.fileName;
      this.setDoc(d);
      return true;
    } catch { return false; }
  }

  async newMap() {
    if (this.doc.dirty && !(await confirmDialog('New map', 'Start a new map? The current map is kept only until you start the new one – save it first if you want to keep it.', 'New map'))) return;
    const d = MapDocument.create();
    this.setDoc(d);
    this.zoomToSweden();
    this.status('New map');
  }

  async openMap(file?: File | null) {
    file ||= await pickFile('.mapmaker,.json,application/zip,application/json');
    if (!file) return;
    try {
      const d = await MapDocument.fromFile(file, file.name);
      this.setDoc(d, true);
      this.status(`Opened ${file.name}`);
    } catch (e: any) {
      message('Couldn’t open the map', e?.message || String(e));
    }
  }

  async saveMap() {
    this.saveView();
    const blob = await this.doc.toBlob();
    const name = safeName(this.doc.title) + '.mapmaker';
    downloadBlob(blob, name);
    this.doc.fileName = name;
    this.doc.dirty = false;
    this.emit('change');
    this.status(`Saved ${name} to your downloads`);
  }

  async exportDialog() {
    const scale = select([['1', 'Screen size (1×)'], ['2', 'Double (2×)'], ['3', 'Triple (3×)'], ['4', 'Print (4×)']], '2', () => upd());
    const fmt = select([['png', 'PNG'], ['jpeg', 'JPEG']], 'png', () => {});
    const credits = el('input', { type: 'checkbox', checked: true });
    const size = el('p', {});
    const upd = () => {
      const s = Number(scale.value);
      size.textContent = `${Math.round(this.view.width * s)} × ${Math.round(this.view.height * s)} pixels`;
    };
    upd();
    const r = await dialog('Export map view', [
      el('p', {}, 'Saves what you see in the map area as a picture. (The page layout with title, legend and scale bar, and PDF, come in a later version.)'),
      row('Size', scale), row('Format', fmt), row('Credits', el('label', { class: 'checks' }, credits, 'Write map credits in the corner')), size,
    ], [{ label: 'Cancel', value: null }, { label: 'Export', value: 'ok', primary: true }]);
    if (r !== 'ok') return;
    await this.exportImage(Number(scale.value), fmt.value as 'png' | 'jpeg', credits.checked);
  }

  async exportImage(scale: number, fmt: 'png' | 'jpeg' = 'png', credits = true) {
    this.status('Exporting…');
    const view = this.view.clone();
    const out = document.createElement('canvas');
    out.width = Math.round(view.width * scale);
    out.height = Math.round(view.height * scale);
    const ctx = out.getContext('2d')!;
    const urls: string[] = [];
    const pass = () => {
      ctx.setTransform(1, 0, 0, 1, 0, 0);
      ctx.fillStyle = '#ffffff';
      ctx.fillRect(0, 0, out.width, out.height);
      urls.length = 0;
      renderMap(ctx, view, this.doc, this.tiles, { dpr: scale }, urls);
    };
    pass();                                    // asks for the tiles
    await this.tiles.whenIdle();
    await symbolsReady();
    pass();
    if (credits) {
      const t = this.attribution();
      if (t) {
        ctx.setTransform(scale, 0, 0, scale, 0, 0);
        ctx.font = '10px "Segoe UI", Arial, sans-serif';
        const w = ctx.measureText(t).width + 12;
        ctx.fillStyle = 'rgba(255,255,255,0.75)';
        ctx.fillRect(view.width - w, view.height - 15, w, 15);
        ctx.fillStyle = '#333'; ctx.textBaseline = 'middle';
        ctx.fillText(t, view.width - w + 6, view.height - 7.5);
      }
    }
    if (this.tiles.isTainted(urls)) {
      message('Can’t export this map', 'One of the map services on this map doesn’t allow its tiles to be used in pictures made by other websites (CORS). Hide that layer and try again.');
      this.status('');
      return;
    }
    out.toBlob(b => {
      if (!b) { message('Export failed', 'The browser couldn’t make the picture – try a smaller size.'); return; }
      const name = `${safeName(this.doc.title)}.${fmt === 'jpeg' ? 'jpg' : 'png'}`;
      downloadBlob(b, name);
      this.status(`Exported ${name}`);
    }, fmt === 'jpeg' ? 'image/jpeg' : 'image/png', 0.92);
  }
}

export { mercToLonlat };
