// The printed page: paper, map frame, title, legend, scale bar, north arrow, sources line and
// pictures – the browser counterpart of the page part of gui/painter.py, with the same layout rules
// and the same page settings in the document, so a page looks the same in both apps.

import type { MapDocument } from './doc';
import { View } from './geo';
import { PT, cssFont, drawSymbol, drawText, layoutText, sourceFor } from './render';
import { DESIGN_DEFAULTS, HEAT_RAMPS } from './shared/styles';
import { SYMBOLS } from './shared/symbols';

export const PX_PER_MM = 96 / 25.4;
export const MM_PER_PT = 25.4 / 72;
export const PAPERS: Record<string, [number, number]> = {
  A4: [210, 297], A3: [297, 420], A2: [420, 594], Letter: [215.9, 279.4], Square: [250, 250], 'Screen 16:9': [180, 320],
};
export const ELEMENTS: Record<string, string> = { title: 'Title', legend: 'Legend', scalebar: 'Scale bar', north: 'North arrow', credits: 'Sources and credits' };

export function paperMm(page: Record<string, any>): [number, number] {
  const [w, h] = PAPERS[page.paper] || PAPERS.A4;
  return page.landscape !== false ? [Math.max(w, h), Math.min(w, h)] : [Math.min(w, h), Math.max(w, h)];
}

export const design = (page: Record<string, any>) => ({ ...DESIGN_DEFAULTS, ...(page.design || {}) });

export interface Rect { x: number; y: number; w: number; h: number }
export const inRect = (r: Rect, x: number, y: number) => x >= r.x && x <= r.x + r.w && y >= r.y && y <= r.y + r.h;

export interface PageGeometry {
  page: Rect; frame: Rect;
  titlePt: [number, number] | null; subtitlePt: [number, number] | null; credits: Rect | null;
  s: number;      // canvas units per millimetre
  k: number;      // canvas units per reference pixel
  titleInside: boolean;
}

export function titleStyle(page: Record<string, any>) {
  const ts = page.title_style || {};
  return { font: ts.font || 'Georgia', size: Number(ts.size ?? 22), bold: ts.bold ?? true, italic: !!ts.italic,
    color: ts.color || '#1d1d1d', halo: '', align: page.title_align || 'left' };
}
const subtitleStyle = (d: any) => ({ font: d.font, size: 11, color: d.muted, halo: '', align: 'left' });
const creditsStyle = (d: any) => ({ font: d.font, size: Number(d.credits_size), color: d.credits_color, halo: '', align: 'left' });

export function attributions(doc: MapDocument): string[] {
  const out: string[] = [];
  for (const L of doc.layers) {
    if (!L.visible) continue;
    const a = (sourceFor(L) as any)?.attribution;
    if (a && !out.includes(a)) out.push(a);
  }
  return out;
}

export function creditsText(doc: MapDocument): string {
  const c = doc.page.credits || {};
  const parts = [String(c.text || '').trim()];
  if (c.auto !== false) { const a = attributions(doc); if (a.length) parts.push('Sources: ' + a.join('; ')); }
  return parts.filter(Boolean).join('   ·   ');
}

export function pageGeometry(doc: MapDocument, ox: number, oy: number, s: number): PageGeometry {
  const page = doc.page, d = design(page);
  const [W, H] = paperMm(page);
  const m = Number(page.margin_mm ?? 12);
  const k = s / PX_PER_MM;
  const inside = d.title_position === 'inside';
  let top = m;
  let titlePt: number | null = null, subPt: number | null = null;
  const ts = titleStyle(page);
  if (!inside) {
    if (page.title) { const h = ts.size * MM_PER_PT * 1.18; titlePt = top + h / 2; top += h; }
    if (page.subtitle) { const h = 11 * MM_PER_PT * 1.35; subPt = top + h / 2 + 0.5; top += h + 0.5; }
    if (titlePt !== null || subPt !== null) top += 3.5;
  }
  let bottom = m;
  let credits: Rect | null = null;
  if ((page.credits || {}).show !== false && creditsText(doc)) {
    const h = Number(d.credits_size) * MM_PER_PT * 1.5;
    credits = { x: ox + m * s, y: oy + (H - m - h) * s, w: (W - 2 * m) * s, h: h * s };
    bottom += h + 1.5;
  }
  const frame = { x: ox + m * s, y: oy + top * s, w: (W - 2 * m) * s, h: Math.max(10, H - top - bottom) * s };
  const tx = ox + (page.title_align === 'center' ? W / 2 : m) * s;
  return {
    page: { x: ox, y: oy, w: W * s, h: H * s }, frame,
    titlePt: titlePt !== null ? [tx, oy + titlePt * s] : null, subtitlePt: subPt !== null ? [tx, oy + subPt * s] : null,
    credits, s, k, titleInside: inside,
  };
}

/** The map view inside the page's frame: the document's view at its reference zoom, scaled by k. */
export function frameView(doc: MapDocument, geo: PageGeometry): View {
  const [cx, cy, z] = doc.view;
  return new View(cx, cy, z + Math.log2(Math.max(geo.k, 1e-6)), Math.max(1, geo.frame.w), Math.max(1, geo.frame.h));
}

export function elementRect(frame: Rect, cfg: Record<string, any>, w: number, h: number): Rect {
  const [fx, fy] = cfg.pos || [0.5, 0.5];
  const a = cfg.anchor || 'tl';
  let x = frame.x + fx * frame.w, y = frame.y + fy * frame.h;
  if (a[1] === 'r') x -= w;
  if (a[0] === 'b') y -= h;
  x = Math.max(frame.x, Math.min(frame.x + frame.w - w, x));
  y = Math.max(frame.y, Math.min(frame.y + frame.h - h, y));
  return { x, y, w, h };
}

/** Anchor corner and position for an element placed at r (the corner nearest the frame's sides). */
export function anchorFor(frame: Rect, r: Rect): [string, [number, number]] {
  const v = r.y + r.h / 2 > frame.y + frame.h / 2 ? 'b' : 't';
  const h = r.x + r.w / 2 > frame.x + frame.w / 2 ? 'r' : 'l';
  const x = h === 'r' ? r.x + r.w : r.x, y = v === 'b' ? r.y + r.h : r.y;
  return [v + h, [Math.round((x - frame.x) / frame.w * 1e4) / 1e4, Math.round((y - frame.y) / frame.h * 1e4) / 1e4]];
}

// ---------------------------------------------------------------- legend
export interface LegendEntry { kind: string; text: string; style?: any; symbol?: string; color?: string; badge?: boolean; ramp?: string }

export function legendEntries(doc: MapDocument): LegendEntry[] {
  const out: LegendEntry[] = [];
  const seen = new Set<string>();
  for (const L of [...doc.layers].reverse()) {
    if (!L.visible) continue;
    if (L.kind === 'data' && L.legend !== false) {
      const st = L.style || {};
      const cls = st.classify || {};
      const geom = L.geometry || 'point';
      if (st.render === 'heatmap') out.push({ kind: 'heat', ramp: st.heat_ramp || 'heat', text: L.name });
      else if ((cls.mode || 'single') !== 'single' && cls.classes?.length) {
        out.push({ kind: 'heading', text: L.name });
        const rows = cls.classes.filter((c: any) => c.show !== false);
        if (cls.other?.show && cls.other?.count) rows.push(cls.other);
        for (const c of rows) {
          const col = c.color || '#b4b4b4';
          out.push({ kind: geom, style: { ...st, color: col, fill: col, stroke: col, line_color: col }, text: String(c.label ?? '') });
        }
      } else out.push({ kind: geom, style: st, text: L.name });
    } else if (L.kind === 'annotations') {
      for (const it of [...(L.items || [])].reverse()) {
        if (it.hidden || !it.legend) continue;
        let e: LegendEntry, sig: string;
        if (it.type === 'symbol') {
          const text = it.legend_text || SYMBOLS[it.symbol]?.label || 'Symbol';
          sig = ['symbol', it.symbol, it.color, !!it.badge, text].join('|');
          e = { kind: 'symbol', symbol: it.symbol, color: it.color, badge: !!it.badge, text };
        } else if (it.type === 'line' || it.type === 'area') {
          const text = it.legend_text || L.name;
          sig = [it.type, JSON.stringify(it.style), text].join('|');
          e = { kind: it.type === 'line' ? 'line_item' : 'area_item', style: it.style || {}, text };
        } else {
          const text = it.legend_text || String(it.text || '').slice(0, 30);
          sig = 'text|' + text;
          e = { kind: 'text', style: it.style || {}, text };
        }
        if (!seen.has(sig)) { seen.add(sig); out.push(e); }
      }
    }
  }
  return out;
}

const lt = (d: any) => ({ font: d.font, size: Number(d.legend_text_size), color: d.text, halo: '', align: 'left' });
const ltitle = (d: any) => ({ font: d.font, size: Number(d.legend_text_size) + 1, bold: true, color: d.text, halo: '', align: 'left' });

function textW(ctx: CanvasRenderingContext2D, text: string, st: any, k: number) { return layoutText(ctx, text, st, k).w; }
function textH(ctx: CanvasRenderingContext2D, text: string, st: any, k: number) { return layoutText(ctx, text, st, k).h; }

function legendGrid(ctx: CanvasRenderingContext2D, entries: LegendEntry[], title: string, k: number, d: any) {
  const sc = Number(d.legend_text_size) / 8.5;
  const pad = 8 * k, sw = Number(d.legend_swatch) * k, gap = 7 * k;
  const row = Math.max(17 * k * sc, sw * 0.72);
  let cols = Math.max(1, Number(d.legend_columns || 1));
  if (d.legend_style === 'strip') cols = Math.max(1, entries.length);
  cols = Math.min(cols, Math.max(1, entries.length));
  const rows = entries.length ? Math.ceil(entries.length / cols) : 0;
  const tw = Math.max(0, ...entries.map(e => textW(ctx, e.text, lt(d), k)));
  const colw = sw + gap + tw + (cols > 1 ? 12 * k : 0);
  let widths: number[] | null = null, w: number;
  if (d.legend_style === 'strip') {
    widths = entries.map(e => sw + gap + textW(ctx, e.text, lt(d), k) + 14 * k);
    w = widths.reduce((a, b) => a + b, 0) - 14 * k;
  } else w = colw * cols - (cols > 1 ? 12 * k : 0);
  let th = 0;
  if (title) { w = Math.max(w, textW(ctx, title, ltitle(d), k)); th = textH(ctx, title, ltitle(d), k) + 5 * k; }
  const h = th + row * rows;
  return { w: w + 2 * pad, h: h + 2 * pad - 2 * k, colw, rows, th, pad, sw, gap, row, widths };
}

function rrect(ctx: CanvasRenderingContext2D, r: Rect, rad: number) {
  ctx.beginPath();
  (ctx as any).roundRect ? ctx.roundRect(r.x, r.y, r.w, r.h, rad) : ctx.rect(r.x, r.y, r.w, r.h);
}

const DASH: Record<string, number[] | null> = { solid: null, dash: [5, 3], dot: [0.8, 2.2], dashdot: [5, 2.5, 0.8, 2.5] };
function stroke(ctx: CanvasRenderingContext2D, color: string | undefined, w: number, dash = 'solid', alpha = 1) {
  ctx.strokeStyle = color || '#555555'; ctx.lineWidth = w; ctx.globalAlpha = alpha;
  const p = DASH[dash] || null;
  ctx.setLineDash(p ? p.map(v => v * Math.max(w, 0.5)) : []);
  ctx.stroke();
  ctx.setLineDash([]); ctx.globalAlpha = 1;
}

function swatch(ctx: CanvasRenderingContext2D, e: LegendEntry, r: Rect, k: number) {
  const st = e.style || {};
  const cx = r.x + r.w / 2, cy = r.y + r.h / 2;
  ctx.save();
  if (e.kind === 'point') {
    const size = Math.min(r.h, Number(st.size ?? 7) * k);
    if ((st.marker || 'circle') === 'circle') {
      ctx.beginPath(); ctx.arc(cx, cy, size / 2, 0, Math.PI * 2);
      ctx.fillStyle = st.color || '#d95f02'; ctx.fill();
      const ow = Number(st.outline_width ?? 1);
      if (ow > 0 && st.outline) { ctx.lineWidth = ow * k; ctx.strokeStyle = st.outline; ctx.stroke(); }
    } else {
      const s = Math.min(r.h * 1.1, size * 1.5);
      const [ax, ay] = (SYMBOLS[st.marker] || SYMBOLS.circle).anchor;
      drawSymbol(ctx, cx + ((ax + 2) / 28 - 0.5) * s, cy + ((ay + 2) / 28 - 0.5) * s, st.marker, st.color, s, false, st.outline || null);
    }
  } else if (e.kind === 'line' || e.kind === 'line_item') {
    ctx.beginPath(); ctx.moveTo(r.x, cy); ctx.lineTo(r.x + r.w, cy); ctx.lineCap = 'round';
    if (e.kind === 'line') stroke(ctx, st.line_color, Number(st.line_width ?? 1.5) * k, st.dash);
    else stroke(ctx, st.color, Number(st.width ?? 2.5) * k, st.dash, Number(st.opacity ?? 1));
  } else if (e.kind === 'polygon' || e.kind === 'area_item') {
    const poly = e.kind === 'polygon';
    const fill = st.fill, fo = Number(poly ? st.fill_opacity ?? 0.25 : st.fill_opacity ?? 0.22);
    const sw = Number(poly ? st.stroke_width ?? 1.2 : st.width ?? 1.8);
    const rr = { x: r.x + k, y: r.y + 2 * k, w: r.w - 2 * k, h: r.h - 4 * k };
    ctx.globalAlpha = fo; ctx.fillStyle = fill || '#888888'; ctx.fillRect(rr.x, rr.y, rr.w, rr.h); ctx.globalAlpha = 1;
    if (sw > 0) { ctx.beginPath(); ctx.rect(rr.x, rr.y, rr.w, rr.h); stroke(ctx, st.stroke, sw * k, poly ? st.stroke_dash : st.dash); }
  } else if (e.kind === 'symbol') {
    const s = r.h * (e.badge ? 1.05 : 1.15);
    if (e.badge) drawSymbol(ctx, cx, cy, e.symbol!, e.color!, s, true);
    else {
      const [ax, ay] = (SYMBOLS[e.symbol!] || SYMBOLS.circle).anchor;
      drawSymbol(ctx, cx + ((ax + 2) / 28 - 0.5) * s, cy + ((ay + 2) / 28 - 0.5) * s, e.symbol!, e.color!, s, false);
    }
  } else if (e.kind === 'heat') {
    const ramp = HEAT_RAMPS[e.ramp || 'heat'] || HEAT_RAMPS.heat;
    const rr = { x: r.x, y: r.y + 3 * k, w: r.w, h: r.h - 6 * k };
    const g = ctx.createLinearGradient(rr.x, 0, rr.x + rr.w, 0);
    for (const [p, c] of ramp) g.addColorStop(p, `rgba(${c[0]},${c[1]},${c[2]},${(60 + 195 * p) / 255})`);
    ctx.fillStyle = g; rrect(ctx, rr, 2 * k); ctx.fill();
  } else if (e.kind === 'text') {
    drawText(ctx, cx, cy, 'Aa', { ...st, size: Math.min(Number(st.size ?? 10), 10), angle: 0, align: 'center' }, k);
  }
  ctx.restore();
}

export function legendSize(ctx: CanvasRenderingContext2D, entries: LegendEntry[], title: string, k: number, d: any): [number, number] {
  const g = legendGrid(ctx, entries, title, k, d);
  return [g.w, g.h];
}

export function drawLegend(ctx: CanvasRenderingContext2D, r: Rect, entries: LegendEntry[], title: string, k: number, d: any) {
  const g = legendGrid(ctx, entries, title, k, d);
  const style = d.legend_style || 'panel';
  let l = lt(d), tt = ltitle(d);
  ctx.save();
  if (style === 'panel' || style === 'card' || style === 'strip') {
    if (style === 'card') {
      for (const [i, a] of [[3, 18], [1.6, 28]]) {
        ctx.fillStyle = `rgba(0,0,0,${a / 255})`;
        rrect(ctx, { x: r.x - i * k * 0.3, y: r.y + i * k * 0.6, w: r.w + i * k * 0.6, h: r.h }, 5 * k); ctx.fill();
      }
    }
    ctx.globalAlpha = Number(d.legend_opacity ?? 0.92);
    ctx.fillStyle = d.legend_bg;
    if (style === 'card') { rrect(ctx, r, 5 * k); ctx.fill(); } else ctx.fillRect(r.x, r.y, r.w, r.h);
    ctx.globalAlpha = 1;
    if (style !== 'card' && d.legend_border) { ctx.lineWidth = 0.8 * k; ctx.strokeStyle = d.legend_border; ctx.strokeRect(r.x, r.y, r.w, r.h); }
  } else {
    l = { ...l, halo: d.halo, halo_width: 1.3 } as any;
    tt = { ...tt, halo: d.halo, halo_width: 1.3 } as any;
  }
  let y0 = r.y + g.pad;
  if (title) { drawText(ctx, r.x + g.pad, y0 + textH(ctx, title, tt, k) / 2, title, tt, k); y0 += g.th; }
  let x = r.x + g.pad;
  entries.forEach((e, i) => {
    let cx: number, cy: number;
    if (g.widths) { cx = x; cy = y0; x += g.widths[i]; }
    else { const col = g.rows ? Math.floor(i / g.rows) : 0, rw = g.rows ? i % g.rows : 0; cx = r.x + g.pad + col * g.colw; cy = y0 + rw * g.row; }
    if (e.kind === 'heading') { drawText(ctx, cx, cy + (g.row - 2 * k) / 2, e.text, { ...l, bold: true }, k); return; }
    const sr = { x: cx, y: cy + 1.5 * k, w: g.sw, h: g.row - 4 * k };
    swatch(ctx, e, sr, k);
    drawText(ctx, sr.x + sr.w + g.gap, cy + (g.row - 2 * k) / 2, e.text, l, k);
  });
  ctx.restore();
}

// ---------------------------------------------------------------- scale bar and north arrow
const scaleText = (d: any) => ({ font: d.font, size: Number(d.scale_text_size), color: d.scale_dark, halo: d.halo, halo_width: 1.3, align: 'center' });

function niceLength(target: number) {
  if (target <= 0) return 1;
  const e = 10 ** Math.floor(Math.log10(target));
  for (const m of [5, 2, 1]) if (m * e <= target) return m * e;
  return e;
}

/** Metres per reference pixel of the map in the frame (the scale on paper). */
function metersPerRefPx(doc: MapDocument) {
  const [, cy, z] = doc.view;
  const lat = Math.atan(Math.sinh(Math.PI * (1 - 2 * cy)));
  return 40075016.686 * Math.cos(lat) / (256 * 2 ** z);
}

function scalebarLayout(doc: MapDocument, frame: Rect, k: number, units = 'metric', maxFrac = 0.22): [number, number, string] {
  const mPerUnit = metersPerRefPx(doc) / k;
  if (units === 'imperial') {
    const ft = mPerUnit / 0.3048, target = frame.w * maxFrac * ft;
    if (target >= 5280 * 0.5) { const v = niceLength(target / 5280); return [v * 5280 / ft, v, 'mi']; }
    const v = niceLength(target); return [v / ft, v, 'ft'];
  }
  const v = niceLength(frame.w * maxFrac * mPerUnit);
  return v >= 1000 ? [v / mPerUnit, v / 1000, 'km'] : [v / mPerUnit, v, 'm'];
}

const fmt = (v: number) => String(+v.toPrecision(6));

export function ratioText(doc: MapDocument) {
  const d = metersPerRefPx(doc) * PX_PER_MM * 1000;
  if (!Number.isFinite(d) || d <= 0) return '';
  const e = 10 ** Math.max(0, Math.floor(Math.log10(d)) - 1);
  return '1 : ' + Math.round(Math.round(d / e) * e).toLocaleString('sv-SE');
}

function scalebarSize(ctx: CanvasRenderingContext2D, doc: MapDocument, frame: Rect, cfg: any, k: number, d: any) {
  const st = scaleText(d);
  const [Lp, v, unit] = scalebarLayout(doc, frame, k, cfg.units || 'metric');
  const end = `${fmt(v)} ${unit}`;
  const w0 = textW(ctx, '0', st, k), wv = textW(ctx, fmt(v), st, k), we = textW(ctx, end, st, k), lh = textH(ctx, end, st, k);
  let w = 2 * k + w0 / 2 + Lp - wv / 2 + we + 2 * k;
  let h = 2 * k + lh + 2.5 * k + 4.2 * k + 3 * k;
  if (d.scale_ratio) { const rt = ratioText(doc); h += textH(ctx, rt, st, k) + 2 * k; w = Math.max(w, textW(ctx, rt, st, k) + 4 * k); }
  return { w, h, Lp, v, unit };
}

function drawScalebar(ctx: CanvasRenderingContext2D, r: Rect, doc: MapDocument, frame: Rect, cfg: any, k: number, d: any) {
  const st = scaleText(d);
  const dark = d.scale_dark, light = d.scale_light;
  const { Lp, v, unit } = scalebarSize(ctx, doc, frame, cfg, k, d);
  const x0 = r.x + 2 * k + textW(ctx, '0', st, k) / 2;
  const bh = 4.2 * k;
  let bottom = r.y + r.h;
  if (d.scale_ratio) {
    const rt = ratioText(doc), rh = textH(ctx, rt, st, k);
    drawText(ctx, r.x + 2 * k, bottom - k - rh / 2, rt, { ...st, align: 'left' }, k);
    bottom -= rh + 2 * k;
  }
  const yb = bottom - 3 * k - bh;
  const style = cfg.style || 'bar';
  ctx.save();
  ctx.lineCap = 'square'; ctx.lineJoin = 'miter';
  if (style === 'line') {
    for (const [c, wd] of [[light, 3.4], [dark, 1.3]] as [string, number][]) {
      ctx.strokeStyle = c; ctx.lineWidth = wd * k;
      ctx.beginPath();
      ctx.moveTo(x0, yb + bh); ctx.lineTo(x0 + Lp, yb + bh);
      ctx.moveTo(x0, yb); ctx.lineTo(x0, yb + bh);
      ctx.moveTo(x0 + Lp, yb); ctx.lineTo(x0 + Lp, yb + bh);
      ctx.moveTo(x0 + Lp / 2, yb + bh * 0.45); ctx.lineTo(x0 + Lp / 2, yb + bh);
      ctx.stroke();
    }
  } else if (style === 'hollow') {
    ctx.lineWidth = 0.9 * k; ctx.strokeStyle = dark;
    ctx.fillStyle = light; ctx.fillRect(x0, yb + bh * 0.3, Lp, bh * 0.7); ctx.strokeRect(x0, yb + bh * 0.3, Lp, bh * 0.7);
    ctx.fillStyle = dark; ctx.fillRect(x0, yb + bh * 0.3, Lp / 2, bh * 0.7); ctx.strokeRect(x0, yb + bh * 0.3, Lp / 2, bh * 0.7);
  } else {
    ctx.lineWidth = 0.9 * k; ctx.strokeStyle = dark;
    for (let i = 0; i < 4; i++) {
      ctx.fillStyle = i % 2 === 0 ? dark : light;
      ctx.fillRect(x0 + i * Lp / 4, yb, Lp / 4, bh); ctx.strokeRect(x0 + i * Lp / 4, yb, Lp / 4, bh);
    }
  }
  ctx.restore();
  const end = `${fmt(v)} ${unit}`;
  const ty = yb - 2.5 * k - textH(ctx, end, st, k) / 2;
  drawText(ctx, x0, ty, '0', st, k);
  drawText(ctx, x0 + Lp / 2, ty, fmt(v / 2), st, k);
  drawText(ctx, x0 + Lp - textW(ctx, fmt(v), st, k) / 2, ty, end, { ...st, align: 'left' }, k);
}

function northSize(k: number, d: any): [number, number] {
  const s = d.north_style || 'simple';
  return s === 'star' ? [34 * k, 40 * k] : s === 'letter' ? [16 * k, 30 * k] : s === 'classic' ? [22 * k, 40 * k] : [16 * k, 34 * k];
}

function poly(ctx: CanvasRenderingContext2D, pts: number[][]) { ctx.beginPath(); pts.forEach(([x, y], i) => i ? ctx.lineTo(x, y) : ctx.moveTo(x, y)); ctx.closePath(); }

function drawNorth(ctx: CanvasRenderingContext2D, r: Rect, k: number, d: any) {
  const col = d.north_color, halo = d.halo;
  const style = d.north_style || 'simple';
  const nfont = { font: 'Georgia', size: 10, bold: true, color: col, halo, halo_width: 1.3, align: 'center' };
  const c = r.x + r.w / 2;
  ctx.save();
  ctx.lineJoin = 'round'; ctx.lineCap = 'round';
  if (style === 'letter') {
    drawText(ctx, c, r.y + 7 * k, 'N', nfont, k);
    for (const [cc, w] of [[halo, 3.2], [col, 1.4]] as [string, number][]) {
      ctx.beginPath(); ctx.moveTo(c, r.y + 14 * k); ctx.lineTo(c, r.y + r.h - k); ctx.strokeStyle = cc; ctx.lineWidth = w * k; ctx.stroke();
    }
  } else if (style === 'star') {
    const cy = r.y + 23 * k, R = 15 * k, rr = 3.6 * k;
    const pts: number[][] = [];
    for (let i = 0; i < 8; i++) {
      const ang = (-90 + i * 45) * Math.PI / 180;
      let rad = i % 2 === 0 ? R : rr * 1.6;
      if (i % 2 === 0 && i !== 0) rad = R * 0.62;
      pts.push([c + rad * Math.cos(ang), cy + rad * Math.sin(ang)]);
    }
    poly(ctx, pts); ctx.strokeStyle = halo; ctx.lineWidth = 2.2 * k; ctx.stroke();
    for (let i = 0; i < 8; i += 2) {
      const a = pts[i], l = pts[(i + 7) % 8], rt = pts[(i + 1) % 8];
      poly(ctx, [[c, cy], a, l]); ctx.fillStyle = col; ctx.fill(); ctx.strokeStyle = col; ctx.lineWidth = 0.8 * k; ctx.stroke();
      poly(ctx, [[c, cy], a, rt]); ctx.fillStyle = halo; ctx.fill(); ctx.stroke();
    }
    drawText(ctx, c, r.y + 4.5 * k, 'N', { ...nfont, size: 8.5 }, k);
  } else {
    const top = r.y + 13 * k, bottom = r.y + r.h - k;
    const w = (style === 'classic' ? 8 : 6) * k;
    const notch = bottom - (style === 'classic' ? 9 : 6) * k;
    poly(ctx, [[c, top], [c - w, bottom], [c, notch], [c + w, bottom]]); ctx.strokeStyle = halo; ctx.lineWidth = 2.4 * k; ctx.stroke();
    poly(ctx, [[c, top], [c - w, bottom], [c, notch]]); ctx.fillStyle = col; ctx.fill(); ctx.strokeStyle = col; ctx.lineWidth = 0.9 * k; ctx.stroke();
    poly(ctx, [[c, top], [c + w, bottom], [c, notch]]); ctx.fillStyle = halo; ctx.fill(); ctx.stroke();
    if (style === 'classic') { ctx.beginPath(); ctx.arc(c, notch - 2 * k, 8.5 * k, 0, Math.PI * 2); ctx.strokeStyle = col; ctx.lineWidth = 0.9 * k; ctx.stroke(); }
    drawText(ctx, c, r.y + 6 * k, 'N', nfont, k);
  }
  ctx.restore();
}

// ---------------------------------------------------------------- pictures
const pictures = new Map<string, { img: HTMLImageElement; ok: boolean }>();
let pictureLoaded: (() => void) | null = null;
export const onPictureLoaded = (f: () => void) => { pictureLoaded = f; };

export function picture(doc: MapDocument, id: string): HTMLImageElement | null {
  let p = pictures.get(id);
  if (!p) {
    const e = doc.extra.find(x => x.name === `images/${id}`);
    if (!e) return null;
    const head = new TextDecoder().decode(e.data.subarray(0, 300));
    const type = head.includes('<svg') ? 'image/svg+xml' : e.data[0] === 0x89 ? 'image/png' : 'image/jpeg';
    const img = new Image();
    p = { img, ok: false };
    pictures.set(id, p);
    const entry = p;
    img.onload = () => { entry.ok = true; pictureLoaded?.(); };
    img.src = URL.createObjectURL(new Blob([e.data as BlobPart], { type }));
  }
  return p.ok ? p.img : null;
}

export function pictureRect(doc: MapDocument, im: any, geo: PageGeometry): Rect | null {
  const img = picture(doc, im.img);
  if (!img) return null;
  const w = Number(im.w ?? 30) * geo.s;
  const aspect = img.naturalWidth ? img.naturalHeight / img.naturalWidth : 1;
  return { x: geo.page.x + Number(im.x ?? 10) * geo.s, y: geo.page.y + Number(im.y ?? 10) * geo.s, w, h: w * aspect };
}

/** Wait until the page's pictures have loaded (for export). */
export async function picturesReady(doc: MapDocument) {
  for (const im of doc.page.images || []) picture(doc, im.img);
  for (let i = 0; i < 50 && [...pictures.values()].some(p => !p.ok); i++) await new Promise(r => setTimeout(r, 20));
}

// ---------------------------------------------------------------- the page
export interface PageOptions { placeholders?: boolean }

/** Draw the page. `drawMap` draws the map into the frame (ctx is clipped to it). Returns element rectangles. */
export function drawPage(ctx: CanvasRenderingContext2D, doc: MapDocument, geo: PageGeometry, drawMap: () => void, opts: PageOptions = {}): Record<string, Rect> {
  const page = doc.page, d = design(page), k = geo.k;
  const rects: Record<string, Rect> = {};
  ctx.save();
  ctx.fillStyle = page.background || '#ffffff';
  ctx.fillRect(geo.page.x, geo.page.y, geo.page.w, geo.page.h);
  ctx.save();
  ctx.beginPath(); ctx.rect(geo.frame.x, geo.frame.y, geo.frame.w, geo.frame.h); ctx.clip();
  ctx.fillStyle = d.map_bg || '#ffffff';
  ctx.fillRect(geo.frame.x, geo.frame.y, geo.frame.w, geo.frame.h);
  drawMap();
  ctx.restore();
  if (page.neatline !== false) {
    const fw = Number(d.frame_width ?? 0.3) * geo.s;
    ctx.strokeStyle = d.frame_color; ctx.lineWidth = fw; ctx.lineJoin = 'miter';
    ctx.strokeRect(geo.frame.x, geo.frame.y, geo.frame.w, geo.frame.h);
    if (d.frame_style === 'double') {
      const g = 1.2 * geo.s;
      ctx.lineWidth = fw * 2.2;
      ctx.strokeRect(geo.frame.x - g, geo.frame.y - g, geo.frame.w + 2 * g, geo.frame.h + 2 * g);
    }
  }
  const quadRect = (q: number[][]): Rect => {
    const xs = q.map(p => p[0]), ys = q.map(p => p[1]);
    return { x: Math.min(...xs), y: Math.min(...ys), w: Math.max(...xs) - Math.min(...xs), h: Math.max(...ys) - Math.min(...ys) };
  };
  const unite = (a: Rect | undefined, b: Rect): Rect => !a ? b : { x: Math.min(a.x, b.x), y: Math.min(a.y, b.y), w: Math.max(a.x + a.w, b.x + b.w) - Math.min(a.x, b.x), h: Math.max(a.y + a.h, b.y + b.h) - Math.min(a.y, b.y) };
  if (geo.titlePt) rects.title = quadRect(drawText(ctx, geo.titlePt[0], geo.titlePt[1], page.title, titleStyle(page), k));
  if (geo.subtitlePt) rects.title = unite(rects.title, quadRect(drawText(ctx, geo.subtitlePt[0], geo.subtitlePt[1], page.subtitle, { ...subtitleStyle(d), align: page.title_align || 'left' }, k)));
  // map elements, clipped to the frame
  ctx.save();
  ctx.beginPath(); ctx.rect(geo.frame.x, geo.frame.y, geo.frame.w, geo.frame.h); ctx.clip();
  if (geo.titleInside && (page.title || page.subtitle)) rects.title = insideTitle(ctx, geo, page, d, k);
  const lg = page.legend || {};
  if (lg.show !== false) {
    const entries = legendEntries(doc);
    if (entries.length) {
      const [w, h] = legendSize(ctx, entries, lg.title || '', k, d);
      const r = elementRect(geo.frame, lg, w, h);
      drawLegend(ctx, r, entries, lg.title || '', k, d);
      rects.legend = r;
    } else if (opts.placeholders) {
      const r = elementRect(geo.frame, lg, 120 * k, 30 * k);
      ctx.fillStyle = 'rgba(255,255,255,0.47)'; ctx.fillRect(r.x, r.y, r.w, r.h);
      ctx.setLineDash([5 * k, 3 * k]); ctx.strokeStyle = '#9a9a9a'; ctx.lineWidth = k; ctx.strokeRect(r.x, r.y, r.w, r.h); ctx.setLineDash([]);
      drawText(ctx, r.x + r.w / 2, r.y + r.h / 2, 'Legend (empty)', { ...lt(d), align: 'center', color: '#8a8a8a' }, k);
      rects.legend = r;
    }
  }
  const sb = page.scalebar || {};
  if (sb.show !== false) {
    const { w, h } = scalebarSize(ctx, doc, geo.frame, sb, k, d);
    const r = elementRect(geo.frame, sb, w, h);
    drawScalebar(ctx, r, doc, geo.frame, sb, k, d);
    rects.scalebar = r;
  }
  const nr = page.north || {};
  if (nr.show !== false) {
    const [w, h] = northSize(k, d);
    const r = elementRect(geo.frame, nr, w, h);
    drawNorth(ctx, r, k, d);
    rects.north = r;
  }
  ctx.restore();
  if (geo.credits) {
    const cs = creditsStyle(d);
    let text = creditsText(doc);
    const r = geo.credits;
    ctx.font = cssFont(cs, cs.size * PT * k);
    if (ctx.measureText(text).width > r.w) {
      while (text.length > 3 && ctx.measureText(text + '…').width > r.w) text = text.slice(0, -1);
      text += '…';
    }
    drawText(ctx, r.x, r.y + r.h / 2, text, cs, k);
    rects.credits = r;
  }
  for (const im of page.images || []) {
    const r = pictureRect(doc, im, geo);
    if (!r) continue;
    ctx.save();
    ctx.globalAlpha = Number(im.opacity ?? 1);
    ctx.drawImage(picture(doc, im.img)!, r.x, r.y, r.w, r.h);
    ctx.restore();
    rects['image:' + im.id] = r;
  }
  ctx.restore();
  return rects;
}

function insideTitle(ctx: CanvasRenderingContext2D, geo: PageGeometry, page: any, d: any, k: number): Rect {
  const ts: any = { ...titleStyle(page), align: 'left' };
  const st: any = { ...subtitleStyle(d), align: 'left' };
  const pad = 9 * k;
  const tl = page.title ? layoutText(ctx, page.title, ts, k) : null;
  const sl = page.subtitle ? layoutText(ctx, page.subtitle, st, k) : null;
  const w = Math.max(tl?.w || 0, sl?.w || 0) + 2 * pad;
  const h = (tl?.h || 0) + (sl?.h || 0) + 2 * pad + (tl && sl ? 3 * k : 0);
  const r = { x: geo.frame.x + 10 * k, y: geo.frame.y + 10 * k, w, h };
  const style = d.legend_style || 'panel';
  if (style !== 'plain') {
    ctx.globalAlpha = Number(d.legend_opacity ?? 0.92); ctx.fillStyle = d.legend_bg;
    rrect(ctx, r, style === 'card' ? 5 * k : 0); ctx.fill(); ctx.globalAlpha = 1;
    if (style === 'panel' && d.legend_border) { ctx.lineWidth = 0.8 * k; ctx.strokeStyle = d.legend_border; ctx.strokeRect(r.x, r.y, r.w, r.h); }
  } else { ts.halo = st.halo = d.halo; }
  let y = r.y + pad;
  if (tl) { drawText(ctx, r.x + pad, y + tl.h / 2, page.title, ts, k); y += tl.h + 3 * k; }
  if (sl) drawText(ctx, r.x + pad, y + sl.h / 2, page.subtitle, st, k);
  return r;
}
