// The editor: the document, the view, the current tool and selection, the map canvas and its
// mouse/keyboard handling, and the commands the menus call.
//
// The map can be shown on its own (the whole canvas) or on the printed page (Page view): then the
// map fills the page's map frame and the page sits on a grey pasteboard, as in the desktop app.
// `view` is always the view of the map inside its frame; `fx, fy` is the frame's top-left corner on
// the canvas and `k` how many canvas pixels a reference (96 dpi) pixel is (1 without the page).

import {
  MapDocument, Item, Layer, annotationLayer, areaItem, basemapLayer, colorLayer, dataLayer, itemTitle, lineItem,
  moveItem, newId, serviceLayer, symbolItem, textItem, textStyle, WEB_KINDS,
} from './doc';
import { View, lonlatToMerc, scaleDenominator } from './geo';
import { TileCache } from './tiles';
import { DataCacheEntry, Hit, distToPolyline, onSymbolLoaded, pointInPoly, renderMap, sourceFor } from './render';
import { BASEMAPS } from './shared/basemaps';
import { THEMES, DESIGN_DEFAULTS, SERIF } from './shared/styles';
import { downloadBlob, loadAutosave, loadTable, pickFile, pickFiles, pruneTables, safeName, saveAutosave, saveTable } from './storage';
import { DATA_ACCEPT, readDataFiles } from './importers';
import { featureAt, highlightFeature, highlightMask } from './datadraw';
import { POINT, Table, combine, countMask } from './table';
import { PageGeometry, Rect, anchorFor, drawPage, frameView, inRect, onPictureLoaded, pageGeometry, paperMm, pictureRect } from './page';
import { confirmDialog, dialog, el, message, row, select } from './ui/dom';
import type { GpkgLayer } from './gpkg';

export type Tool = 'select' | 'features' | 'pan' | 'text' | 'symbol' | 'line' | 'area';
export const TOOLS: { id: Tool; label: string; key: string; hint: string }[] = [
  { id: 'select', label: 'Select and move', key: 'V', hint: 'Click to select, drag to move. Click a feature of a data layer to see its attributes.' },
  { id: 'features', label: 'Select features', key: 'M', hint: 'Drag a rectangle to select features of the chosen data layer. Shift adds, Alt removes.' },
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
const PASTEBOARD = '#2a2a2a';

export class Editor {
  doc: MapDocument;
  view: View;
  tool: Tool = 'select';
  selLayer: string | null = null;
  selItem: string | null = null;
  /** a page element picked in Page view: title, legend, scalebar, north, credits, image:<id>, or page */
  selElement: string | null = null;
  /** a feature of a data layer picked with the select tool (shown in Properties) */
  selFeature: { layerId: string; index: number } | null = null;
  /** selected features per data layer (Select menu, rectangle tool, attribute table) */
  selections = new Map<string, Uint8Array>();
  opts: ToolOptions = {
    preset: 'place', symbol: 'pin', symbolColor: '#b03a2e', symbolSize: 22, badge: false,
    lineColor: '#b03a2e', lineWidth: 2.5, lineDash: 'solid', areaFill: '#b03a2e',
  };
  tiles = new TileCache();
  canvas: HTMLCanvasElement;
  ctx: CanvasRenderingContext2D;
  hits: Hit[] = [];
  drawing: number[][] | null = null;           // lon/lat points of a line or area being drawn
  // layout
  cw = 1; ch = 1;                               // canvas size in CSS px
  fx = 0; fy = 0; k = 1;
  geo: PageGeometry | null = null;
  pageScale: number | null = null;              // px per mm on screen; null = fit the window
  pageOff: [number, number] = [0, 0];
  elementRects: Record<string, Rect> = {};
  private dataCache = new Map<string, DataCacheEntry>();
  private mapCanvas = document.createElement('canvas');
  private moving = false;
  private movingTimer = 0;
  private hover: [number, number] | null = null;   // canvas coordinates
  private rubber: { x0: number; y0: number; x1: number; y1: number } | null = null;
  private spaceDown = false;
  private frame = 0;
  private listeners: Record<string, Listener[]> = {};
  private autosaveTimer = 0;
  private lastUnsupported = '';
  statusMsg = '';

  constructor(canvas: HTMLCanvasElement) {
    this.canvas = canvas;
    this.ctx = canvas.getContext('2d')!;
    this.doc = MapDocument.create();
    this.view = new View(...this.doc.view, 1, 1);
    this.selLayer = this.doc.annotationLayers()[0]?.id || null;
    this.tiles.onLoad = () => this.redraw();
    onSymbolLoaded(() => this.redraw());
    onPictureLoaded(() => this.redraw());
    this.bindDoc();
    new ResizeObserver(() => this.resize()).observe(canvas.parentElement!);
    this.bindPointer();
    this.resize();
  }

  // ---------------------------------------------------------------- events
  on(ev: string, f: Listener) { (this.listeners[ev] ||= []).push(f); }
  emit(ev: string) { for (const f of this.listeners[ev] || []) f(); }
  status(msg: string) { this.statusMsg = msg; this.emit('status'); }

  private bindDoc() {
    this.doc.onChange = () => {
      this.layout();
      this.redraw();
      this.emit('change');
      this.scheduleAutosave();
    };
  }

  /** Replace the document (new / open / restore). */
  setDoc(d: MapDocument, warn = false) {
    this.doc = d;
    this.bindDoc();
    this.selItem = null; this.selFeature = null; this.selElement = null;
    this.selections.clear();
    this.dataCache.clear();
    this.pageScale = null; this.pageOff = [0, 0];
    this.selLayer = d.annotationLayers().slice(-1)[0]?.id || d.layers[d.layers.length - 1]?.id || null;
    this.drawing = null;
    this.layout();
    this.redraw();
    this.emit('change'); this.emit('selection'); this.emit('view'); this.emit('page'); this.emit('data-selection');
    const unsupported = d.layers.filter(L => !WEB_KINDS.includes(L.kind) || (L.kind === 'data' && !d.table(L)));
    if (warn && unsupported.length) {
      message('Some layers aren’t shown yet',
        `The browser version can’t draw ${unsupported.map(L => `“${L.name}”`).join(', ')} yet (terrain and grid layers come in a later version). ` +
        'They stay in the map and are saved back with it, so nothing is lost.');
    }
  }

  // ---------------------------------------------------------------- selection
  select(layerId: string | null, itemId: string | null = null) {
    this.selLayer = layerId;
    this.selItem = itemId;
    this.selFeature = null;
    this.selElement = null;
    this.redraw();
    this.emit('selection');
  }

  selectElement(el: string | null) {
    this.selElement = el;
    this.selItem = null; this.selFeature = null;
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

  /** The data layer the Select menu and the rectangle tool work on: the selected one, else the top visible one. */
  dataTarget(): Layer | null {
    const sel = this.selectedLayer();
    if (sel?.kind === 'data' && this.doc.table(sel)) return sel;
    for (let i = this.doc.layers.length - 1; i >= 0; i--) {
      const L = this.doc.layers[i];
      if (L.kind === 'data' && L.visible && this.doc.table(L)) return L;
    }
    return null;
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

  // ---------------------------------------------------------------- layout
  get pageMode() { return !!this.doc.page.show; }

  fitScale() {
    const [W, H] = paperMm(this.doc.page);
    return Math.max(0.25, Math.min((this.cw - 56) / W, (this.ch - 56) / H));
  }
  pageScaleNow() { return this.pageScale || this.fitScale(); }

  /** Work out where the map frame is and the view inside it, from the document. */
  layout() {
    if (this.pageMode) {
      const s = this.pageScaleNow();
      const [W, H] = paperMm(this.doc.page);
      const ox = (this.cw - W * s) / 2 + this.pageOff[0], oy = (this.ch - H * s) / 2 + this.pageOff[1];
      this.geo = pageGeometry(this.doc, ox, oy, s);
      const v = frameView(this.doc, this.geo);
      this.view.cx = v.cx; this.view.cy = v.cy; this.view.zoom = v.zoom; this.view.width = v.width; this.view.height = v.height;
      this.fx = this.geo.frame.x; this.fy = this.geo.frame.y; this.k = this.geo.k;
    } else {
      this.geo = null;
      const [cx, cy, z] = this.doc.view;
      this.view.cx = cx; this.view.cy = cy; this.view.zoom = z; this.view.width = this.cw; this.view.height = this.ch;
      this.fx = 0; this.fy = 0; this.k = 1;
    }
  }

  setPageMode(on: boolean) {
    this.doc.page.show = on;
    this.selElement = null;
    this.dataCache.clear();
    this.layout(); this.redraw();
    this.emit('page'); this.emit('view'); this.emit('selection');
    this.scheduleAutosave();
  }

  fitPage() { this.pageScale = null; this.pageOff = [0, 0]; this.layout(); this.redraw(); this.emit('view'); }

  /** Zoom the page (not the map) around a canvas point. */
  zoomPage(f: number, x = this.cw / 2, y = this.ch / 2) {
    const s0 = this.pageScaleNow();
    const s1 = Math.max(0.3, Math.min(40, s0 * f));
    const [W, H] = paperMm(this.doc.page);
    const ox0 = (this.cw - W * s0) / 2 + this.pageOff[0], oy0 = (this.ch - H * s0) / 2 + this.pageOff[1];
    const pmx = (x - ox0) / s0, pmy = (y - oy0) / s0;            // the page point under the pointer stays put
    this.pageScale = s1;
    this.pageOff = [x - pmx * s1 - (this.cw - W * s1) / 2, y - pmy * s1 - (this.ch - H * s1) / 2];
    this.layout(); this.redraw(); this.emit('view');
  }

  pagePercent() { return Math.round(this.pageScaleNow() / (96 / 25.4) * 100); }

  // ---------------------------------------------------------------- drawing
  resize() {
    const r = this.canvas.parentElement!.getBoundingClientRect();
    const dpr = window.devicePixelRatio || 1;
    this.cw = Math.max(1, r.width);
    this.ch = Math.max(1, r.height);
    this.canvas.width = Math.round(this.cw * dpr);
    this.canvas.height = Math.round(this.ch * dpr);
    this.layout();
    this.redraw();
  }

  redraw() {
    if (this.frame) return;
    this.frame = requestAnimationFrame(() => { this.frame = 0; this.paint(); });
  }

  private paint() {
    this.layout();
    const dpr = this.canvas.width / this.cw;
    const ctx = this.ctx;
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    this.hits = [];
    const unsupported = new Set<string>();
    if (!this.geo) {
      ctx.fillStyle = '#e8e6e1';
      ctx.fillRect(0, 0, this.canvas.width, this.canvas.height);
      renderMap(ctx, this.view, this.doc, this.tiles, { dpr, hits: this.hits, unsupported, dataCache: this.dataCache, moving: this.moving });
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      this.paintOverlay(ctx);
      this.elementRects = {};
    } else {
      const geo = this.geo;
      ctx.fillStyle = PASTEBOARD;
      ctx.fillRect(0, 0, this.canvas.width, this.canvas.height);
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.fillStyle = 'rgba(0,0,0,0.35)';
      ctx.fillRect(geo.page.x + 3, geo.page.y + 4, geo.page.w, geo.page.h);
      const mc = this.mapCanvas;
      const fw = Math.max(1, Math.round(geo.frame.w * dpr)), fh = Math.max(1, Math.round(geo.frame.h * dpr));
      if (mc.width !== fw || mc.height !== fh) { mc.width = fw; mc.height = fh; }
      const mctx = mc.getContext('2d')!;
      this.elementRects = drawPage(ctx, this.doc, geo, () => {
        mctx.setTransform(1, 0, 0, 1, 0, 0);
        mctx.clearRect(0, 0, fw, fh);
        renderMap(mctx, this.view, this.doc, this.tiles, { dpr, k: this.k, hits: this.hits, unsupported, dataCache: this.dataCache, moving: this.moving });
        ctx.drawImage(mc, geo.frame.x, geo.frame.y, geo.frame.w, geo.frame.h);
      }, { placeholders: true });
      ctx.save();
      ctx.beginPath(); ctx.rect(geo.frame.x, geo.frame.y, geo.frame.w, geo.frame.h); ctx.clip();
      ctx.translate(this.fx, this.fy);
      this.paintOverlay(ctx);
      ctx.restore();
      const er = this.selElement && this.selElement !== 'page' ? this.elementRects[this.selElement] : null;
      if (er) {
        ctx.setLineDash([4, 3]); ctx.lineWidth = 1.2; ctx.strokeStyle = '#2f8cff';
        ctx.strokeRect(er.x - 3, er.y - 3, er.w + 6, er.h + 6); ctx.setLineDash([]);
      }
    }
    if (this.rubber) {
      const r = this.rubber;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.fillStyle = 'rgba(47,140,255,0.12)'; ctx.strokeStyle = '#2f8cff'; ctx.lineWidth = 1; ctx.setLineDash([4, 3]);
      ctx.fillRect(Math.min(r.x0, r.x1), Math.min(r.y0, r.y1), Math.abs(r.x1 - r.x0), Math.abs(r.y1 - r.y0));
      ctx.strokeRect(Math.min(r.x0, r.x1), Math.min(r.y0, r.y1), Math.abs(r.x1 - r.x0), Math.abs(r.y1 - r.y0));
      ctx.setLineDash([]);
    }
    const u = [...unsupported].join(', ');
    if (u !== this.lastUnsupported) { this.lastUnsupported = u; this.emit('status'); }
    this.updateAttribution();
  }

  unsupportedNames() { return this.lastUnsupported; }

  /** The view is changing (drag, wheel): big data layers reuse their last picture until it settles. */
  private markMoving() {
    this.moving = true;
    clearTimeout(this.movingTimer);
    this.movingTimer = window.setTimeout(() => { this.moving = false; this.redraw(); }, 160);
  }

  /** Selection outlines, drawing in progress (frame coordinates). */
  private paintOverlay(ctx: CanvasRenderingContext2D) {
    for (const [lid, mask] of this.selections) {
      const L = this.doc.layer(lid), t = this.doc.table(L);
      if (L && t && L.visible && mask.length === t.n && !['heatmap', 'clusters'].includes(L.style?.render)) highlightMask(ctx, this.view, L, t, mask);
    }
    if (this.selFeature) {
      const L = this.doc.layer(this.selFeature.layerId), t = this.doc.table(L);
      if (L && t && L.visible && this.selFeature.index < t.n) highlightFeature(ctx, this.view, L, t, this.selFeature.index);
    }
    const sel = this.selItem ? this.hits.find(h => h.itemId === this.selItem) : null;
    ctx.lineWidth = 1.2;
    ctx.strokeStyle = '#2f8cff';
    ctx.setLineDash([]);
    if (sel) {
      if (sel.quad) {
        ctx.beginPath();
        sel.quad.forEach(([x, y], i) => i ? ctx.lineTo(x, y) : ctx.moveTo(x, y));
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
      if (this.hover) pts.push([this.hover[0] - this.fx, this.hover[1] - this.fy]);
      ctx.beginPath();
      pts.forEach(([x, y], i) => i ? ctx.lineTo(x, y) : ctx.moveTo(x, y));
      if (this.tool === 'area') {
        ctx.closePath();
        ctx.fillStyle = this.opts.areaFill; ctx.globalAlpha = 0.18; ctx.fill(); ctx.globalAlpha = 1;
      }
      ctx.strokeStyle = this.tool === 'area' ? this.opts.areaFill : this.opts.lineColor;
      ctx.lineWidth = this.tool === 'line' ? this.opts.lineWidth * this.k : 1.8;
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
      const a = (sourceFor(L) as any)?.attribution;
      if (a && !parts.includes(a)) parts.push(a);
    }
    return parts.join(' · ');
  }

  private updateAttribution() {
    const a = document.getElementById('attribution');
    if (a) { const t = this.pageMode ? '' : this.attribution(); a.textContent = t; a.style.display = t ? '' : 'none'; }
  }

  // ---------------------------------------------------------------- view
  saveView() {
    this.doc.view = [this.view.cx, this.view.cy, this.view.zoom - Math.log2(this.k)];
    this.scheduleAutosave();
  }

  /** Zoom the map by dz around a point given in canvas coordinates. */
  zoomBy(dz: number, px?: number, py?: number) {
    this.markMoving();
    const x = (px ?? this.fx + this.view.width / 2) - this.fx, y = (py ?? this.fy + this.view.height / 2) - this.fy;
    this.view.zoomAround(x, y, dz, 1 + Math.log2(this.k), 21 + Math.log2(this.k));
    this.saveView();
    this.redraw();
    this.emit('view');
  }

  zoomToSweden() { this.zoomToLonlats([[10.9, 55.3], [24.2, 69.1]], 0.92, 12); }

  /** Zoom to the selected item, feature, layer or features. */
  zoomToSelection() {
    const it = this.selectedItem();
    const L = this.selectedLayer();
    const t = !it ? this.doc.table(L) : null;
    if (t) {
      if (this.selFeature) return this.zoomToFeatures(t, [this.selFeature.index], 15);
      const b = t.bbox();
      if (b) this.zoomToLonlats([[b[0], b[1]], [b[2], b[3]]]);
      return;
    }
    const items: Item[] = it ? [it] : (L?.items || []);
    const pts: number[][] = [];
    for (const x of items) { if ('lon' in x) pts.push([x.lon, x.lat]); pts.push(...(x.coords || [])); }
    if (pts.length) this.zoomToLonlats(pts, pts.length > 1 ? 0.8 : 0, 15);
  }

  zoomToFeatures(t: Table, ids: ArrayLike<number>, maxZoom = 16) {
    const pts: number[][] = [];
    for (let j = 0; j < ids.length && j < 200000; j++) {
      const i = ids[j];
      if (t.kind[i] === POINT) pts.push([t.x[i], t.y[i]]);
      else for (const r of t.rings(i)) for (let v = 0; v < r.length; v += 2) pts.push([r[v], r[v + 1]]);
    }
    if (pts.length) this.zoomToLonlats(pts, pts.length > 1 ? 0.8 : 0, maxZoom);
  }

  zoomToLonlats(pts: number[][], fill = 0.8, maxZoom = 17) {
    let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
    for (const p of pts) {
      if (!Number.isFinite(p[0]) || !Number.isFinite(p[1])) continue;
      const [x, y] = lonlatToMerc(p[0], p[1]);
      if (x < x0) x0 = x; if (x > x1) x1 = x; if (y < y0) y0 = y; if (y > y1) y1 = y;
    }
    if (!(x0 <= x1)) return;
    this.layout();
    const lk = Math.log2(this.k);
    this.view.cx = (x0 + x1) / 2;
    this.view.cy = (y0 + y1) / 2;
    const z = fill ? Math.log2(Math.min(this.view.width * fill / (Math.max(x1 - x0, 1e-7) * 256), this.view.height * fill / (Math.max(y1 - y0, 1e-7) * 256))) : maxZoom + lk;
    this.view.zoom = Math.max(2 + lk, Math.min(maxZoom + lk, z));
    this.saveView(); this.redraw(); this.emit('view');
  }

  /** Pick the feature of a data layer under (x, y) in frame coordinates, top layer first. */
  pickFeature(x: number, y: number): boolean {
    for (let j = this.doc.layers.length - 1; j >= 0; j--) {
      const L = this.doc.layers[j];
      if (L.kind !== 'data' || !L.visible || L.locked || L.style?.render === 'heatmap') continue;
      const t = this.doc.table(L);
      if (!t) continue;
      const i = featureAt(this.view, L, t, x, y);
      if (i >= 0) {
        this.selLayer = L.id; this.selItem = null; this.selElement = null;
        this.selFeature = { layerId: L.id, index: i };
        this.redraw(); this.emit('selection'); this.emit('feature');
        return true;
      }
    }
    if (this.selFeature) { this.selFeature = null; this.redraw(); this.emit('selection'); }
    return false;
  }

  scaleText() {
    const d = scaleDenominator(new View(this.doc.view[0], this.doc.view[1], this.doc.view[2], 1, 1));
    const r = d > 1e5 ? Math.round(d / 1000) * 1000 : d > 1e4 ? Math.round(d / 100) * 100 : Math.round(d / 10) * 10;
    return `1 : ${r.toLocaleString('sv-SE')}`;
  }

  hoverLonlat(): [number, number] | null {
    if (!this.hover) return null;
    const x = this.hover[0] - this.fx, y = this.hover[1] - this.fy;
    if (x < 0 || y < 0 || x > this.view.width || y > this.view.height) return null;
    return this.view.toLonlat(x, y);
  }

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

  private inFrame(x: number, y: number) {
    return !this.geo || inRect(this.geo.frame, x, y);
  }

  /** The page element under a canvas point (pictures first, then legend, scale bar, north arrow, title, credits). */
  private elementAt(x: number, y: number): string | null {
    if (!this.geo) return null;
    const keys = Object.keys(this.elementRects);
    const order = [...keys.filter(k => k.startsWith('image:')).reverse(), 'legend', 'scalebar', 'north', 'title', 'credits'];
    for (const k of order) { const r = this.elementRects[k]; if (r && inRect(r, x, y)) return k; }
    return null;
  }

  private bindPointer() {
    const c = this.canvas;
    type Drag = {
      kind: 'pan' | 'move' | 'vertex' | 'page' | 'element' | 'rubber'; x: number; y: number; cx: number; cy: number;
      state?: string; vertex?: number; lastLL?: [number, number]; click?: boolean; el?: string; rect?: Rect; off?: [number, number];
      method?: string; resize?: boolean;
    };
    let drag: Drag | null = null;
    const pos = (e: PointerEvent | MouseEvent | WheelEvent) => {
      const r = c.getBoundingClientRect();
      return [e.clientX - r.left, e.clientY - r.top] as [number, number];
    };

    c.addEventListener('pointerdown', e => {
      c.setPointerCapture(e.pointerId);
      this.layout();
      const [X, Y] = pos(e);
      const x = X - this.fx, y = Y - this.fy;
      const inside = this.inFrame(X, Y);
      if (e.button === 1 || e.button === 0 && (this.tool === 'pan' || this.spaceDown)) {
        drag = inside || !this.geo ? { kind: 'pan', x: X, y: Y, cx: this.view.cx, cy: this.view.cy } : { kind: 'page', x: X, y: Y, cx: 0, cy: 0, off: [...this.pageOff] };
        c.style.cursor = 'grabbing';
        return;
      }
      if (e.button !== 0) return;
      // page elements (legend, scale bar …) can be picked with the select tool, or with any tool outside the map
      if (this.geo && (this.tool === 'select' || !inside)) {
        const elk = this.elementAt(X, Y);
        if (elk) {
          this.selectElement(elk);
          const r = this.elementRects[elk];
          const resize = elk.startsWith('image:') && X > r.x + r.w - 10 && Y > r.y + r.h - 10;
          drag = { kind: 'element', x: X, y: Y, cx: 0, cy: 0, el: elk, rect: { ...r }, state: this.doc.state(), resize };
          return;
        }
        if (!inside) {
          this.selectElement('page');
          drag = { kind: 'page', x: X, y: Y, cx: 0, cy: 0, off: [...this.pageOff] };
          return;
        }
      }
      const ll = this.view.toLonlat(x, y);
      if (this.tool === 'select') {
        const v = this.vertexAt(x, y);
        if (v >= 0) {
          drag = { kind: 'vertex', x: X, y: Y, cx: 0, cy: 0, state: this.doc.state(), vertex: v };
          return;
        }
        const h = this.hitTest(x, y);
        if (h) {
          this.select(h.layerId, h.itemId);
          drag = { kind: 'move', x: X, y: Y, cx: 0, cy: 0, state: this.doc.state(), lastLL: ll };
        } else {
          if (this.selItem || this.selElement) this.select(this.selLayer, null);
          drag = { kind: 'pan', x: X, y: Y, cx: this.view.cx, cy: this.view.cy, click: true };   // dragging empty map pans; a click picks a feature
        }
      } else if (this.tool === 'features') {
        drag = { kind: 'rubber', x: X, y: Y, cx: 0, cy: 0, method: e.shiftKey ? 'add' : e.altKey ? 'remove' : 'new' };
        this.rubber = { x0: X, y0: Y, x1: X, y1: Y };
      } else if (this.tool === 'text') {
        e.preventDefault();                 // keep the click from taking the focus from the text field
        this.addItem(textItem(ll[0], ll[1], 'Text', this.opts.preset), 'Add text');
        this.emit('edit-text');
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
      const [X, Y] = pos(e);
      const x = X - this.fx, y = Y - this.fy;
      this.hover = [X, Y];
      this.emit('view');
      if (!drag) {
        if (this.drawing) this.redraw();
        if (this.tool === 'select' && !this.spaceDown) {
          const elk = this.geo ? this.elementAt(X, Y) : null;
          c.style.cursor = elk ? 'move' : !this.inFrame(X, Y) ? 'default' : this.vertexAt(x, y) >= 0 ? 'move' : this.hitTest(x, y) ? 'pointer' : 'default';
        }
        return;
      }
      if (drag.kind === 'pan') {
        const w = this.view.world;
        this.view.cx = drag.cx - (X - drag.x) / w;
        this.view.cy = drag.cy - (Y - drag.y) / w;
        if (Math.hypot(X - drag.x, Y - drag.y) > 3) { drag.click = false; this.markMoving(); }
        this.saveView();
        this.redraw();
      } else if (drag.kind === 'page') {
        this.pageOff = [drag.off![0] + X - drag.x, drag.off![1] + Y - drag.y];
        this.layout(); this.redraw();
      } else if (drag.kind === 'element') {
        this.moveElement(drag.el!, drag.rect!, X - drag.x, Y - drag.y, drag.resize!);
      } else if (drag.kind === 'rubber') {
        this.rubber = { x0: drag.x, y0: drag.y, x1: X, y1: Y };
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
      const [X, Y] = pos(e);
      if (drag.kind === 'pan') {
        this.saveView(); c.style.cursor = this.cursorFor();
        if (drag.click) this.pickFeature(X - this.fx, Y - this.fy);
      } else if (drag.kind === 'page') {
        c.style.cursor = this.cursorFor();
      } else if (drag.kind === 'rubber') {
        const r = this.rubber!;
        this.rubber = null;
        if (Math.abs(r.x1 - r.x0) < 3 && Math.abs(r.y1 - r.y0) < 3) this.selectInRect(r.x0 - 4, r.y0 - 4, r.x0 + 4, r.y0 + 4, drag.method!);
        else this.selectInRect(Math.min(r.x0, r.x1), Math.min(r.y0, r.y1), Math.max(r.x0, r.x1), Math.max(r.y0, r.y1), drag.method!);
      } else if (drag.state) {
        const moved = Math.hypot(X - drag.x, Y - drag.y) > 1;
        if (moved) this.doc.pushUndo(drag.kind === 'vertex' ? 'Move point' : drag.kind === 'element' ? 'Move' : 'Move', drag.state);
      }
      drag = null;
    };
    c.addEventListener('pointerup', end);
    c.addEventListener('pointercancel', end);
    c.addEventListener('pointerleave', () => { this.hover = null; this.emit('view'); if (this.drawing) this.redraw(); });

    c.addEventListener('dblclick', e => {
      if (this.drawing) { this.finishDrawing(); return; }
      const [X, Y] = pos(e);
      if (this.geo && this.elementAt(X, Y) === 'title') this.emit('edit-title');
    });

    c.addEventListener('wheel', e => {
      e.preventDefault();
      const [X, Y] = pos(e);
      const dz = -e.deltaY * (e.deltaMode === 1 ? 0.05 : 0.0022) * (e.ctrlKey ? 2 : 1);
      // in Page view: over the paper (or with Ctrl) the page zooms; over the map the map zooms
      if (this.geo && (e.ctrlKey || !this.inFrame(X, Y))) this.zoomPage(2 ** Math.max(-1, Math.min(1, dz)), X, Y);
      else this.zoomBy(Math.max(-1, Math.min(1, dz)), X, Y);
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
        this.zoomBy(pinch.z + Math.log2(d / pinch.d) - this.view.zoom, (a[0] + b[0]) / 2, (a[1] + b[1]) / 2);
      }
    });
    const tend = (e: PointerEvent) => { touches.delete(e.pointerId); if (touches.size < 2) pinch = null; };
    c.addEventListener('pointerup', tend);
    c.addEventListener('pointercancel', tend);
  }

  /** Drag a page element: legend / scale bar / north arrow keep an anchor corner; pictures move (or resize) in mm. */
  private moveElement(elk: string, r0: Rect, dx: number, dy: number, resize: boolean) {
    const geo = this.geo!;
    const page = this.doc.page;
    if (elk.startsWith('image:')) {
      const im = (page.images || []).find((x: any) => 'image:' + x.id === elk);
      if (!im) return;
      if (resize) im.w = Math.max(3, Math.round((r0.w + dx) / geo.s * 10) / 10);
      else { im.x = Math.round((r0.x + dx - geo.page.x) / geo.s * 10) / 10; im.y = Math.round((r0.y + dy - geo.page.y) / geo.s * 10) / 10; }
    } else if (elk === 'legend' || elk === 'scalebar' || elk === 'north') {
      const f = geo.frame;
      const r = { ...r0, x: Math.max(f.x, Math.min(f.x + f.w - r0.w, r0.x + dx)), y: Math.max(f.y, Math.min(f.y + f.h - r0.h, r0.y + dy)) };
      const [anchor, p] = anchorFor(f, r);
      page[elk] = { ...(page[elk] || {}), anchor, pos: p };
    } else return;
    this.doc.changed();
  }

  setSpace(down: boolean) {
    if (this.spaceDown === down) return;
    this.spaceDown = down;
    this.canvas.style.cursor = this.cursorFor();
  }

  // ---------------------------------------------------------------- selecting features
  selectionOf(layerId: string | null | undefined): Uint8Array | null {
    return layerId ? this.selections.get(layerId) || null : null;
  }

  selectedCount(layerId: string | null | undefined) { return countMask(this.selectionOf(layerId)); }

  setSelection(layerId: string, mask: Uint8Array | null, silent = false) {
    if (!mask || !countMask(mask)) this.selections.delete(layerId);
    else this.selections.set(layerId, mask);
    this.redraw();
    this.emit('data-selection');
    if (!silent) {
      const L = this.doc.layer(layerId);
      const n = this.selectedCount(layerId);
      this.status(n ? `${n.toLocaleString('sv-SE')} selected in “${L?.name}”` : 'Nothing selected');
    }
  }

  /** Rectangle (canvas coordinates) → select the features of the target data layer inside it. */
  selectInRect(x0: number, y0: number, x1: number, y1: number, method: string) {
    const L = this.dataTarget();
    const t = this.doc.table(L);
    if (!L || !t) { this.status('Choose a data layer first'); return; }
    const [mx0, my0] = this.view.toMerc(x0 - this.fx, y0 - this.fy), [mx1, my1] = this.view.toMerc(x1 - this.fx, y1 - this.fy);
    const prep = t.prepared();
    const ci = t.classes(L.style?.classify);
    const m = new Uint8Array(t.n);
    for (let i = 0; i < t.n; i++) {
      if (ci && ci[i] < 0) continue;
      if (t.kind[i] === POINT) { const x = prep.mx[i], y = prep.my[i]; m[i] = +(x >= mx0 && x <= mx1 && y >= my0 && y <= my1); continue; }
      const b = i * 4;
      if (prep.bbox[b + 2] < mx0 || prep.bbox[b] > mx1 || prep.bbox[b + 3] < my0 || prep.bbox[b + 1] > my1) continue;
      for (let r = t.featRing[i]; r < t.featRing[i + 1] && !m[i]; r++) {
        for (let v = t.ringStart[r]; v < t.ringStart[r + 1]; v++) {
          const x = prep.cm[v * 2], y = prep.cm[v * 2 + 1];
          if (x >= mx0 && x <= mx1 && y >= my0 && y <= my1) { m[i] = 1; break; }
        }
      }
    }
    if (this.selLayer !== L.id) { this.selLayer = L.id; this.selItem = null; this.emit('selection'); }
    this.setSelection(L.id, combine(this.selectionOf(L.id), m, method));
  }

  selectAll() { const L = this.dataTarget(), t = this.doc.table(L); if (L && t) this.setSelection(L.id, new Uint8Array(t.n).fill(1)); }
  clearSelection() { const L = this.dataTarget(); if (L) this.setSelection(L.id, null); }
  invertSelection() {
    const L = this.dataTarget(), t = this.doc.table(L);
    if (!L || !t) return;
    const cur = this.selectionOf(L.id), m = new Uint8Array(t.n);
    for (let i = 0; i < t.n; i++) m[i] = cur ? cur[i] ^ 1 : 1;
    this.setSelection(L.id, m);
  }

  selectedIds(L: Layer | null): number[] {
    const m = this.selectionOf(L?.id), ids: number[] = [];
    if (m) for (let i = 0; i < m.length; i++) if (m[i]) ids.push(i);
    return ids;
  }

  zoomToSelected() {
    const L = this.dataTarget(), t = this.doc.table(L);
    const ids = this.selectedIds(L);
    if (t && ids.length) this.zoomToFeatures(t, ids);
  }

  layerFromSelection() {
    const L = this.dataTarget(), t = this.doc.table(L);
    const ids = this.selectedIds(L);
    if (!L || !t || !ids.length) { this.status('Select some features first'); return; }
    const sub = t.take(ids);
    this.doc.checkpoint('New layer from selection');
    const id = this.doc.addTable(sub);
    const N = dataLayer(this.doc.uniqueName(`${L.name} (selection)`), sub.mainKind(), L.fields || [], { kind: 'selection', from: L.name }, id);
    N.style = JSON.parse(JSON.stringify(L.style || {}));
    N.label = JSON.parse(JSON.stringify(L.label || { field: null, style: textStyle('feature') }));
    N.count = sub.n;
    this.doc.addLayer(N, L.id);
    this.selLayer = N.id; this.selItem = null; this.selFeature = null;
    this.doc.changed(); this.emit('selection');
    this.status(`Made “${N.name}” with ${sub.n.toLocaleString('sv-SE')} features`);
  }

  removeSelectedFeatures() {
    const L = this.dataTarget(), t = this.doc.table(L);
    const m = this.selectionOf(L?.id);
    if (!L || !t || !m) { this.status('Select some features first'); return; }
    const keep: number[] = [];
    for (let i = 0; i < t.n; i++) if (!m[i]) keep.push(i);
    this.doc.checkpoint('Remove features');
    const id = this.doc.addTable(t.take(keep));
    L.table = id;
    L.count = keep.length;
    this.selections.delete(L.id);
    this.selFeature = null;
    this.doc.changed(); this.emit('data-selection'); this.emit('selection');
    this.status(`Removed ${(t.n - keep.length).toLocaleString('sv-SE')} features from “${L.name}”`);
  }

  // ---------------------------------------------------------------- editing commands
  addItem(it: Item, label: string) {
    this.doc.checkpoint(label);
    const L = this.targetLayer();
    L.items.push(it);
    if (!L.visible) L.visible = true;
    this.selLayer = L.id;
    this.selItem = it.id;
    this.selElement = null;
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
    if (this.selElement?.startsWith('image:')) {
      this.doc.checkpoint('Remove picture');
      this.doc.page.images = (this.doc.page.images || []).filter((x: any) => 'image:' + x.id !== this.selElement);
      this.selElement = 'page';
      this.doc.changed(); this.emit('selection');
      return;
    }
    if (this.selElement && ['legend', 'scalebar', 'north', 'credits'].includes(this.selElement)) {
      this.doc.checkpoint('Hide ' + this.selElement);
      this.doc.page[this.selElement] = { ...(this.doc.page[this.selElement] || {}), show: false };
      this.selElement = 'page';
      this.doc.changed(); this.emit('selection');
      return;
    }
    if (this.selItem) {
      const [, it] = this.doc.findItem(this.selItem);
      this.doc.checkpoint(`Delete ${it ? itemTitle(it) : 'item'}`);
      this.doc.removeItem(this.selItem);
      this.selItem = null;
      this.doc.changed();
      this.emit('selection');
    } else if (this.selLayer && !this.selElement) {
      this.deleteLayer(this.selLayer);
    }
  }

  deleteLayer(id: string) {
    const L = this.doc.layer(id);
    if (!L) return;
    const i = this.doc.index(id);
    this.doc.checkpoint(`Delete layer ${L.name}`);
    this.doc.removeLayer(id);
    this.selections.delete(id);
    const next = this.doc.layers[Math.min(i, this.doc.layers.length - 1)];
    this.selLayer = next?.id || null;
    this.selItem = null; this.selFeature = null;
    this.doc.changed();
    this.emit('selection'); this.emit('data-selection');
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
    if (this.selFeature && !this.doc.layer(this.selFeature.layerId)) this.selFeature = null;
    if (this.selLayer && !this.doc.layer(this.selLayer)) this.selLayer = this.doc.layers[this.doc.layers.length - 1]?.id || null;
    for (const [lid, m] of this.selections) {
      const t = this.doc.table(this.doc.layer(lid));
      if (!t || t.n !== m.length) this.selections.delete(lid);
    }
    this.emit('selection'); this.emit('data-selection'); this.emit('page');
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

  // ---------------------------------------------------------------- page
  /** Set a design theme on the page elements, and restyle labels and background maps (as the desktop app does). */
  applyTheme(key: string) {
    const th = THEMES[key];
    if (!th) return;
    this.doc.checkpoint('Theme');
    const page = this.doc.page;
    const d = { ...DESIGN_DEFAULTS, ...th.design, theme: key };
    page.design = d;
    page.background = th.page;
    page.title_style = { ...(page.title_style || {}), color: th.title };
    if (d.font === SERIF) page.title_style.font = SERIF;
    for (const L of this.doc.layers) {
      if (L.kind === 'basemap' && ['esri-light', 'esri-dark'].includes(L.source)) {
        const old = BASEMAPS.find(b => b.id === L.source);
        L.source = th.basemap;
        if (old && L.name === old.name) L.name = BASEMAPS.find(b => b.id === th.basemap)?.name || L.name;
        L.opacity = th.basemap_opacity ?? 1;
      }
    }
    const restyle = (st: any, preset: string | null) => {
      if (!st) return;
      const base = textStyle(preset || 'place');
      st.color = th.label_color == null ? base.color : preset === 'water' && th.water ? th.water : th.label_color;
      if (st.halo) st.halo = th.label_halo;
    };
    for (const L of this.doc.layers) {
      for (const it of L.items || []) {
        if (it.type === 'text' && !it.style?.box) restyle(it.style, it.preset);
        else if (it.type === 'symbol' && it.label_style) restyle(it.label_style, 'place');
      }
      if (L.kind === 'data' && L.label?.style) restyle(L.label.style, 'feature');
    }
    this.doc.changed();
  }

  async insertPicture() {
    const f = await pickFile('image/png,image/jpeg,image/svg+xml,.png,.jpg,.jpeg,.svg');
    if (!f) return;
    const data = new Uint8Array(await f.arrayBuffer());
    const id = newId();
    this.doc.checkpoint('Insert picture');
    this.doc.extra.push({ name: `images/${id}`, data });
    const [W] = paperMm(this.doc.page);
    const m = Number(this.doc.page.margin_mm ?? 12);
    const im = { id: newId(), img: id, x: W - m - 32, y: m + 2, w: 28, opacity: 1 };
    (this.doc.page.images ||= []).push(im);
    if (!this.pageMode) this.setPageMode(true);
    this.selElement = 'image:' + im.id;
    this.doc.changed(); this.emit('selection');
  }

  /** Where a picture sits (for the Properties panel). */
  pictureRect(im: any) { return this.geo ? pictureRect(this.doc, im, this.geo) : null; }

  // ---------------------------------------------------------------- data
  async importData(files?: File[] | null) {
    files ||= await pickFiles(DATA_ACCEPT);
    if (!files?.length) return;
    this.status('Reading ' + files.map(f => f.name).join(', ') + '…');
    await new Promise(r => setTimeout(r, 30));
    let got;
    try { got = await readDataFiles(files, (name, layers) => this.chooseLayers(name, layers)); }
    catch (e: any) { this.status(''); message('Couldn’t read the data', e?.message || String(e)); return; }
    if (!got.length) { this.status(''); return; }
    this.doc.checkpoint(got.length > 1 ? 'Add data layers' : 'Add data layer');
    const sel = this.selectedLayer();
    let above = sel && !['color', 'basemap', 'terrain', 'service'].includes(sel.kind) ? sel.id : null;
    let last = null;
    for (const g of got) {
      const id = this.doc.addTable(g.table);
      const L = dataLayer(this.doc.uniqueName(g.name), g.geometry, g.fields, { kind: 'file', name: g.name }, id);
      L.count = g.table.n;
      if (above) this.doc.addLayer(L, above);
      else {
        // just above the background layers, below labels
        let i = 0;
        this.doc.layers.forEach((x, j) => { if (['color', 'basemap', 'terrain', 'service', 'data'].includes(x.kind)) i = j + 1; });
        this.doc.layers.splice(i, 0, L);
      }
      above = L.id;
      last = L;
    }
    this.selLayer = last!.id; this.selItem = null; this.selFeature = null; this.selElement = null;
    this.doc.changed();
    this.emit('selection');
    const all = got.map(g => g.table.bbox()).filter(Boolean) as number[][];
    if (all.length) this.zoomToLonlats([[Math.min(...all.map(b => b[0])), Math.min(...all.map(b => b[1]))], [Math.max(...all.map(b => b[2])), Math.max(...all.map(b => b[3]))]], 0.85, 16);
    const n = got.reduce((s, g) => s + g.table.n, 0);
    this.status(`Added ${n.toLocaleString('sv-SE')} feature${n === 1 ? '' : 's'}`);
  }

  /** A GeoPackage with several tables: ask which to add. */
  private async chooseLayers(file: string, layers: GpkgLayer[]): Promise<GpkgLayer[] | null> {
    const boxes = layers.map((L, i) => el('input', { type: 'checkbox', checked: layers.length <= 4 || i === 0 }));
    const list = el('div', { class: 'choice-list' }, ...layers.map((L, i) => el('label', { class: 'choice', style: { gridTemplateColumns: '20px 1fr' } },
      boxes[i], el('span', {}, L.table, el('br'), el('small', {}, `${L.count.toLocaleString('sv-SE')} ${(L.geometryType || 'features').toLowerCase()}`)))));
    const r = await dialog(`Tables in ${file}`, [el('p', {}, 'Pick the tables to add as layers.'), list],
      [{ label: 'Cancel', value: null }, { label: 'Add', value: 'ok', primary: true }]);
    if (r !== 'ok') return null;
    this.status(`Reading ${file}…`);
    return layers.filter((_, i) => boxes[i].checked);
  }

  // ---------------------------------------------------------------- files
  private autosaveChain: Promise<void> = Promise.resolve();

  private scheduleAutosave() {
    clearTimeout(this.autosaveTimer);
    this.autosaveTimer = window.setTimeout(() => this.autosaveNow(), 700);
  }

  /** Keep the map in this browser now (one save at a time, so they never overlap). */
  autosaveNow(): Promise<void> {
    clearTimeout(this.autosaveTimer);
    this.autosaveChain = this.autosaveChain.then(async () => {
      const d = this.doc;
      for (const [id, t] of d.tables) await saveTable(id, t.toEntries(`data/${id}`));
      await saveAutosave({ body: d.body(), extra: d.extra, fileName: d.fileName, savedAt: Date.now(), tables: [...d.tables.keys()] } as any);
      await pruneTables(new Set(this.doc.tables.keys()));
      this.emit('saved');
    }).catch(e => console.warn('Autosave failed', e));
    return this.autosaveChain;
  }

  async restoreAutosave(): Promise<boolean> {
    const a = await loadAutosave();
    if (!a?.body) return false;
    try {
      const tableEntries = [];
      for (const id of (a as any).tables || []) { const e = await loadTable(id); if (e) tableEntries.push(...e); }
      const d = MapDocument.fromBody(a.body, [...(a.extra || []), ...tableEntries]);
      d.fileName = a.fileName;
      this.setDoc(d);
      return true;
    } catch (e) {
      console.warn('Could not restore the map kept in this browser', e);
      message('Couldn’t restore your last map', 'The map kept in this browser couldn’t be read, so a new map was started. If you saved it as a file, open that with File → Open.');
      return false;
    }
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
    const m = await import('./export');
    return m.exportDialog(this);
  }
}

export { drawPage };
