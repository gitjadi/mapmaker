// Drawing the map onto a canvas – the browser counterpart of gui/painter.py, used for the screen
// and for export. Every visible layer is drawn bottom to top, each through its own offscreen
// canvas so the layer's opacity and blend mode apply to it as a whole.

import { MapDocument, Item, Layer } from './doc';
import { View, TILE, lonlatToMerc } from './geo';
import { TileCache, TileSourceDef, tileUrl } from './tiles';
import { BASEMAPS } from './shared/basemaps';
import { drawDataLayer } from './datadraw';
import { SYMBOLS } from './shared/symbols';

export const PT = 96 / 72;                 // reference pixels per point
export const DASHES: Record<string, number[] | null> = {
  solid: null, dash: [5, 3], dot: [0.8, 2.2], dashdot: [5, 2.5, 0.8, 2.5],
};
const COMPOSITE: Record<string, GlobalCompositeOperation> = {
  normal: 'source-over', multiply: 'multiply', screen: 'screen', overlay: 'overlay', darken: 'darken',
};

/** Font names saved in documents are Windows fonts; give each a stack that works everywhere. */
const FONT_STACKS: Record<string, string> = {
  'Segoe UI': '"Segoe UI", "Helvetica Neue", Arial, system-ui, sans-serif',
  Arial: 'Arial, "Helvetica Neue", Helvetica, sans-serif',
  Calibri: 'Calibri, Carlito, "Segoe UI", Arial, sans-serif',
  Verdana: 'Verdana, Geneva, sans-serif',
  Tahoma: 'Tahoma, Verdana, sans-serif',
  'Franklin Gothic Book': '"Franklin Gothic Book", "Libre Franklin", Arial, sans-serif',
  Georgia: 'Georgia, "Times New Roman", serif',
  Cambria: 'Cambria, Caladea, Georgia, serif',
  'Times New Roman': '"Times New Roman", Times, serif',
  'Palatino Linotype': '"Palatino Linotype", Palatino, "Book Antiqua", Georgia, serif',
  Garamond: 'Garamond, "EB Garamond", Georgia, serif',
  'Book Antiqua': '"Book Antiqua", Palatino, Georgia, serif',
};
export const fontStack = (name: string) => FONT_STACKS[name] || `"${name}", sans-serif`;

export interface Hit { layerId: string; itemId: string; kind: string; rect?: DOMRect; poly?: number[][]; quad?: number[][] }

export interface RenderOptions {
  /** device pixels per CSS pixel (the canvas is width*dpr wide) */
  dpr: number;
  /** size of map symbols and text relative to the reference 96 dpi (1 on screen) */
  k?: number;
  hits?: Hit[];
  skip?: Set<string>;
  /** layers kinds this renderer can't draw yet are shown as nothing; collect their names here */
  unsupported?: Set<string>;
  /** screen only: finished pictures of big data layers, reused (moved and scaled) while the view changes */
  dataCache?: Map<string, DataCacheEntry>;
  /** the view is being dragged or zoomed: reuse cached data pictures instead of drawing afresh */
  moving?: boolean;
}

export interface DataCacheEntry { key: string; view: View; canvas: HTMLCanvasElement; dpr: number }
const BIG_LAYER = 3000;

// ---------------------------------------------------------------- tiles
export function sourceFor(L: Layer): (Partial<TileSourceDef> & { urls: string[] }) | null {
  if (L.kind === 'basemap') return BASEMAPS.find(b => b.id === L.source) || BASEMAPS[0];
  if (L.kind === 'service') return L.source?.urls?.length ? L.source : null;
  return null;
}

/** The tile zoom to use for a view: sharp on high-dpi screens, within the source's range. */
function tileZoom(view: View, dpr: number, src: Partial<TileSourceDef>) {
  const z = Math.round(view.zoom + Math.log2(dpr) - 0.2);
  return Math.max(src.min_zoom ?? 0, Math.min(src.max_zoom ?? 19, z));
}

export function visibleTiles(view: View, dpr: number, src: Partial<TileSourceDef>) {
  const z = tileZoom(view, dpr, src);
  const n = 2 ** z;
  const scale = view.world / (TILE * n);                 // CSS px per tile pixel
  const [mx0, my0] = view.toMerc(0, 0), [mx1, my1] = view.toMerc(view.width, view.height);
  const x0 = Math.floor(mx0 * n), x1 = Math.floor(mx1 * n), y0 = Math.max(0, Math.floor(my0 * n)), y1 = Math.min(n - 1, Math.floor(my1 * n));
  const out: { z: number; x: number; y: number; wx: number; px: number; py: number; size: number }[] = [];
  if ((x1 - x0 + 1) * (y1 - y0 + 1) > 900) return out;
  for (let y = y0; y <= y1; y++) for (let x = x0; x <= x1; x++) {
    const [px, py] = view.toPx(x / n, y / n);
    out.push({ z, x, y, wx: ((x % n) + n) % n, px, py, size: TILE * scale });
  }
  return out;
}

function drawTiles(ctx: CanvasRenderingContext2D, view: View, dpr: number, src: Partial<TileSourceDef> & { urls: string[] },
                   cache: TileCache, urlsUsed?: string[]) {
  for (const t of visibleTiles(view, dpr, src)) {
    const url = tileUrl(src, t.z, t.wx, t.y);
    urlsUsed?.push(url);
    const img = cache.get(url);
    // snap to whole device pixels so tiles don't show seams
    const l = Math.floor(t.px * dpr), tp = Math.floor(t.py * dpr);
    const r = Math.ceil((t.px + t.size) * dpr), b = Math.ceil((t.py + t.size) * dpr);
    if (img) { ctx.drawImage(img, l, tp, r - l, b - tp); continue; }
    // not loaded yet: stretch a loaded parent tile, if there is one
    for (let up = 1; up <= 4 && t.z - up >= 0; up++) {
      const pz = t.z - up, f = 2 ** up;
      const px = Math.floor(t.wx / f), py = Math.floor(t.y / f);
      const purl = tileUrl(src, pz, px, py);
      if (cache.has(purl)) {
        const pimg = cache.get(purl)!;
        const s = TILE / f;
        ctx.drawImage(pimg, (t.wx % f) * s, (t.y % f) * s, s, s, l, tp, r - l, b - tp);
        break;
      }
    }
  }
}

// ---------------------------------------------------------------- symbols
const symbolImages = new Map<string, HTMLImageElement | null>();
let symbolLoaded: (() => void) | null = null;
export function onSymbolLoaded(f: () => void) { symbolLoaded = f; }

export function symbolSvg(key: string, color: string, halo: string | null = '#ffffff', haloWidth = 2.6): string {
  const s = SYMBOLS[key] || SYMBOLS.circle;
  let out = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="-2 -2 28 28" width="28" height="28">';
  if (halo) out += `<g fill="${halo}" stroke="${halo}" stroke-width="${haloWidth}" stroke-linejoin="round">${s.body}</g>`;
  return out + `<g fill="${color}">${s.body}</g></svg>`;
}

export function symbolImage(key: string, color: string, halo: string | null): HTMLImageElement | null {
  const ck = `${key}|${color}|${halo}`;
  if (symbolImages.has(ck)) return symbolImages.get(ck)!;
  symbolImages.set(ck, null);
  const img = new Image();
  img.onload = () => { symbolImages.set(ck, img); symbolLoaded?.(); };
  img.src = 'data:image/svg+xml;charset=utf-8,' + encodeURIComponent(symbolSvg(key, color, halo));
  return null;
}

/** Wait until every symbol image the document uses has loaded (for export). */
export async function symbolsReady(): Promise<void> {
  for (let i = 0; i < 50 && [...symbolImages.values()].some(v => v === null); i++) await new Promise(r => setTimeout(r, 20));
}

export function symbolRect(x: number, y: number, key: string, size: number, badge: boolean) {
  if (badge) return { x: x - size / 2, y: y - size / 2, w: size, h: size };
  const [ax, ay] = (SYMBOLS[key] || SYMBOLS.circle).anchor;
  return { x: x - (ax + 2) / 28 * size, y: y - (ay + 2) / 28 * size, w: size, h: size };
}

function drawSymbol(ctx: CanvasRenderingContext2D, x: number, y: number, key: string, color: string, size: number,
                    badge: boolean, halo: string | null = '#ffffff') {
  const r = symbolRect(x, y, key, size, badge);
  if (badge) {
    const cx = r.x + r.w / 2, cy = r.y + r.h / 2;
    ctx.beginPath();
    ctx.arc(cx, cy, size * 0.46, 0, Math.PI * 2);
    ctx.fillStyle = color; ctx.fill();
    ctx.lineWidth = size * 0.07; ctx.strokeStyle = halo || '#ffffff'; ctx.stroke();
    const img = symbolImage(key, '#ffffff', null);
    const g = size * 0.66;
    if (img) ctx.drawImage(img, cx - g / 2, cy - g / 2, g, g);
  } else {
    const img = symbolImage(key, color, halo);
    if (img) ctx.drawImage(img, r.x, r.y, r.w, r.h);
  }
  return r;
}

// ---------------------------------------------------------------- text
export function cssFont(st: Record<string, any>, px: number) {
  return `${st.italic ? 'italic ' : ''}${st.bold ? 'bold ' : ''}${px}px ${fontStack(st.font || 'Segoe UI')}`;
}

/** Measure a label: its lines, their widths and the box around them, relative to the anchor. */
export function layoutText(ctx: CanvasRenderingContext2D, text: string, st: Record<string, any>, k: number) {
  const px = Number(st.size ?? 10) * PT * k;
  ctx.font = cssFont(st, px);
  const spacing = Number(st.spacing || 0) / 100 * px;
  (ctx as any).letterSpacing = `${spacing}px`;
  const lines = (st.caps ? text.toUpperCase() : text).split('\n');
  const lh = px * 1.2;
  const widths = lines.map(l => ctx.measureText(l).width);
  const w = Math.max(1, ...widths);
  const h = lh * lines.length;
  const align = st.align || 'center';
  const x0 = align === 'center' ? -w / 2 : align === 'right' ? -w : 0;
  const pad = st.box ? 4 * k : 0;
  return { px, lh, lines, widths, w, h, align, box: { x: x0 - pad, y: -h / 2 - pad * 0.8, w: w + 2 * pad, h: h + 1.6 * pad } };
}

/** Draw a label with its halo or box at (x, y); returns the corners of its (rotated) box. */
export function drawText(ctx: CanvasRenderingContext2D, x: number, y: number, text: string, st: Record<string, any>, k: number) {
  const L = layoutText(ctx, text || ' ', st, k);
  const angle = Number(st.angle || 0) * Math.PI / 180;
  ctx.save();
  ctx.translate(x, y);
  if (angle) ctx.rotate(-angle);
  if (st.box) {
    ctx.fillStyle = st.box;
    ctx.globalAlpha *= 0.94;
    ctx.beginPath();
    (ctx as any).roundRect ? ctx.roundRect(L.box.x, L.box.y, L.box.w, L.box.h, 3 * k) : ctx.rect(L.box.x, L.box.y, L.box.w, L.box.h);
    ctx.fill();
    ctx.globalAlpha /= 0.94;
    ctx.strokeStyle = 'rgba(0,0,0,0.27)'; ctx.lineWidth = 0.8 * k; ctx.stroke();
  }
  ctx.textBaseline = 'middle';
  ctx.textAlign = L.align === 'center' ? 'center' : L.align === 'right' ? 'right' : 'left';
  L.lines.forEach((line, i) => {
    const ly = -L.h / 2 + L.lh * (i + 0.5);
    if (!st.box && st.halo) {
      ctx.strokeStyle = st.halo;
      ctx.globalAlpha *= 0.92;
      ctx.lineWidth = 2 * Number(st.halo_width ?? 1.4) * PT * k;
      ctx.lineJoin = 'round';
      ctx.strokeText(line, 0, ly);
      ctx.globalAlpha /= 0.92;
    }
    ctx.fillStyle = st.color || '#222222';
    ctx.fillText(line, 0, ly);
  });
  ctx.restore();
  (ctx as any).letterSpacing = '0px';
  const c = Math.cos(-angle), s = Math.sin(-angle);
  const b = L.box;
  return [[b.x, b.y], [b.x + b.w, b.y], [b.x + b.w, b.y + b.h], [b.x, b.y + b.h]]
    .map(([u, v]) => [x + u * c - v * s, y + u * s + v * c]);
}

// ---------------------------------------------------------------- annotations
function itemPoints(view: View, it: Item) { return (it.coords || []).map((c: number[]) => view.lonlatToPx(c[0], c[1])); }

function setDash(ctx: CanvasRenderingContext2D, dash: string, width: number) {
  const d = DASHES[dash] || null;
  ctx.setLineDash(d ? d.map(v => v * Math.max(width, 0.5)) : []);
}

export function drawItem(ctx: CanvasRenderingContext2D, view: View, it: Item, k: number, hits?: Hit[], layerId = '') {
  const st = it.style || {};
  if (it.type === 'area') {
    const pts = itemPoints(view, it);
    if (pts.length < 3) return;
    ctx.beginPath();
    pts.forEach(([x, y]: number[], i: number) => i ? ctx.lineTo(x, y) : ctx.moveTo(x, y));
    ctx.closePath();
    ctx.globalAlpha = Number(st.fill_opacity ?? 0.22);
    ctx.fillStyle = st.fill || '#888888'; ctx.fill();
    ctx.globalAlpha = 1;
    const w = Number(st.width ?? 1.8) * k;
    if (w > 0) {
      ctx.lineWidth = w; ctx.strokeStyle = st.stroke || '#555555'; ctx.lineJoin = 'round'; ctx.lineCap = 'round';
      setDash(ctx, st.dash || 'solid', w); ctx.stroke(); ctx.setLineDash([]);
    }
    hits?.push({ layerId, itemId: it.id, kind: 'area', poly: pts });
  } else if (it.type === 'line') {
    const pts = itemPoints(view, it);
    if (pts.length < 2) return;
    const w = Number(st.width ?? 2.5) * k;
    ctx.globalAlpha = Number(st.opacity ?? 1);
    ctx.beginPath();
    pts.forEach(([x, y]: number[], i: number) => i ? ctx.lineTo(x, y) : ctx.moveTo(x, y));
    ctx.lineWidth = w; ctx.strokeStyle = st.color || '#333333'; ctx.lineJoin = 'round'; ctx.lineCap = 'round';
    setDash(ctx, st.dash || 'solid', w); ctx.stroke(); ctx.setLineDash([]);
    if (st.arrow) {
      const [ax, ay] = pts[pts.length - 2], [bx, by] = pts[pts.length - 1];
      const ang = Math.atan2(by - ay, bx - ax), s = Math.max(7, w * 3.2);
      ctx.beginPath();
      ctx.moveTo(bx, by);
      ctx.lineTo(bx - s * Math.cos(ang - 0.42), by - s * Math.sin(ang - 0.42));
      ctx.lineTo(bx - s * Math.cos(ang + 0.42), by - s * Math.sin(ang + 0.42));
      ctx.closePath(); ctx.fillStyle = st.color || '#333333'; ctx.fill();
    }
    ctx.globalAlpha = 1;
    hits?.push({ layerId, itemId: it.id, kind: 'line', poly: pts });
  } else if (it.type === 'symbol') {
    const [x, y] = view.lonlatToPx(it.lon, it.lat);
    const size = Number(it.size ?? 22) * k;
    const r = drawSymbol(ctx, x, y, it.symbol || 'circle', it.color || '#b03a2e', size, !!it.badge);
    let quad = [[r.x, r.y], [r.x + r.w, r.y], [r.x + r.w, r.y + r.h], [r.x, r.y + r.h]];
    if (it.label) {
      const lst = { ...(it.label_style || {}), align: 'left' };
      const q = drawText(ctx, r.x + r.w + 2 * k, r.y + r.h * 0.22, it.label, lst, k);
      const xs = [...quad, ...q].map(p => p[0]), ys = [...quad, ...q].map(p => p[1]);
      quad = [[Math.min(...xs), Math.min(...ys)], [Math.max(...xs), Math.min(...ys)], [Math.max(...xs), Math.max(...ys)], [Math.min(...xs), Math.max(...ys)]];
    }
    hits?.push({ layerId, itemId: it.id, kind: 'symbol', quad });
  } else if (it.type === 'text') {
    const [x, y] = view.lonlatToPx(it.lon, it.lat);
    const quad = drawText(ctx, x, y, it.text || ' ', st, k);
    hits?.push({ layerId, itemId: it.id, kind: 'text', quad });
  }
}

// ---------------------------------------------------------------- the whole map
let scratch: HTMLCanvasElement | null = null;
function scratchCanvas(w: number, h: number) {
  if (!scratch) scratch = document.createElement('canvas');
  if (scratch.width !== w || scratch.height !== h) { scratch.width = w; scratch.height = h; }
  const c = scratch.getContext('2d')!;
  c.setTransform(1, 0, 0, 1, 0, 0);
  c.clearRect(0, 0, w, h);
  return { canvas: scratch, ctx: c };
}

/** Draw the document's visible layers onto ctx (a canvas of view.width*dpr x view.height*dpr). */
export function renderMap(ctx: CanvasRenderingContext2D, view: View, doc: MapDocument, cache: TileCache,
                          opts: RenderOptions, urlsUsed?: string[]) {
  const { dpr } = opts;
  const k = opts.k ?? 1;
  const W = ctx.canvas.width, H = ctx.canvas.height;
  for (const L of doc.layers) {
    if (!L.visible) continue;
    const { canvas, ctx: c } = scratchCanvas(W, H);
    c.imageSmoothingQuality = 'high';
    let drew = true;
    if (L.kind === 'color') {
      c.fillStyle = L.color || '#f4f1ea';
      c.fillRect(0, 0, W, H);
    } else if (L.kind === 'basemap' || L.kind === 'service') {
      const src = sourceFor(L);
      if (src) {
        drawTiles(c, view, dpr, src, cache, urlsUsed);
        const labels = L.kind === 'basemap' && L.labels && (src as TileSourceDef).labels?.length
          ? { ...src, urls: (src as TileSourceDef).labels } : null;
        if (labels) drawTiles(c, view, dpr, labels, cache, urlsUsed);
      }
    } else if (L.kind === 'data') {
      const t = doc.table(L);
      if (!t) { opts.unsupported?.add(L.name); continue; }
      const cacheable = opts.dataCache && t.n > BIG_LAYER;
      if (!cacheable) {
        c.setTransform(dpr, 0, 0, dpr, 0, 0);
        drawDataLayer(c, view, L, t, k, dpr);
      } else {
        const styleKey = JSON.stringify([L.table, L.style, L.label]);
        const viewKey = [view.cx, view.cy, view.zoom, view.width, view.height, dpr].join(',');
        let e = opts.dataCache!.get(L.id);
        const fresh = e && e.key === styleKey + '|' + viewKey;
        if (!fresh && !(opts.moving && e && e.key.startsWith(styleKey + '|'))) {
          const cv = e?.canvas || document.createElement('canvas');
          cv.width = W; cv.height = H;
          const x = cv.getContext('2d')!;
          x.setTransform(dpr, 0, 0, dpr, 0, 0);
          x.clearRect(0, 0, W, H);
          drawDataLayer(x, view, L, t, k, dpr);
          e = { key: styleKey + '|' + viewKey, view: view.clone(), canvas: cv, dpr };
          opts.dataCache!.set(L.id, e);
        }
        if (e) {
          // where the cached picture lands in the current view
          const s = view.world / e.view.world;
          const dx = (view.width / 2 + (e.view.cx - view.cx) * view.world - e.view.width / 2 * s) * dpr;
          const dy = (view.height / 2 + (e.view.cy - view.cy) * view.world - e.view.height / 2 * s) * dpr;
          c.drawImage(e.canvas, dx, dy, e.canvas.width * s * dpr / e.dpr, e.canvas.height * s * dpr / e.dpr);
        }
      }
    } else if (L.kind === 'annotations') {
      c.setTransform(dpr, 0, 0, dpr, 0, 0);
      for (const it of L.items || []) {
        if (it.hidden || opts.skip?.has(it.id)) continue;
        drawItem(c, view, it, k, opts.hits, L.id);
      }
    } else {
      drew = false;
      opts.unsupported?.add(L.name);
    }
    if (!drew) continue;
    ctx.save();
    ctx.globalAlpha = Number(L.opacity ?? 1);
    ctx.globalCompositeOperation = COMPOSITE[L.blend] || 'source-over';
    ctx.drawImage(canvas, 0, 0);
    ctx.restore();
  }
}

/** Point-in-polygon and distance-to-segment helpers for selecting items. */
export function pointInPoly(x: number, y: number, poly: number[][]) {
  let inside = false;
  for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) {
    const [xi, yi] = poly[i], [xj, yj] = poly[j];
    if ((yi > y) !== (yj > y) && x < (xj - xi) * (y - yi) / (yj - yi) + xi) inside = !inside;
  }
  return inside;
}

export function distToPolyline(x: number, y: number, pts: number[][], closed = false) {
  let best = Infinity;
  const n = closed ? pts.length : pts.length - 1;
  for (let i = 0; i < n; i++) {
    const [ax, ay] = pts[i], [bx, by] = pts[(i + 1) % pts.length];
    const dx = bx - ax, dy = by - ay;
    const t = Math.max(0, Math.min(1, ((x - ax) * dx + (y - ay) * dy) / (dx * dx + dy * dy || 1)));
    best = Math.min(best, Math.hypot(x - (ax + t * dx), y - (ay + t * dy)));
  }
  return best;
}

export { lonlatToMerc };
