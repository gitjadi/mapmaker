// Drawing data layers – the browser counterpart of gui/datadraw.py: points as circles or map
// symbols, lines, areas, colour by class, and labels from a field. Large point layers are thinned
// to one marker per small screen cell, so hundreds of thousands of features still draw quickly.

import type { Layer } from './doc';
import type { View } from './geo';
import { AREA, LINE, POINT, Table, classColors } from './table';
import { DASHES, drawText, layoutText, symbolImage, symbolRect } from './render';

const VECTOR_POINTS = 4000;

export interface DataDrawStats { drawn: number }

function dash(ctx: CanvasRenderingContext2D, d: string, w: number) {
  const p = DASHES[d] || null;
  ctx.setLineDash(p ? p.map(v => v * Math.max(w, 0.5)) : []);
}

/** A round marker with an outline, drawn once per colour and size and reused (stamped). */
const sprites = new Map<string, HTMLCanvasElement>();
function circleSprite(color: string, outline: string | null, ow: number, d: number): HTMLCanvasElement {
  const key = `${color}|${outline}|${ow.toFixed(2)}|${d.toFixed(2)}`;
  let c = sprites.get(key);
  if (c) return c;
  const s = Math.ceil(d + ow * 2 + 2);
  c = document.createElement('canvas');
  c.width = c.height = s;
  const x = c.getContext('2d')!;
  x.beginPath(); x.arc(s / 2, s / 2, d / 2, 0, Math.PI * 2);
  x.fillStyle = color; x.fill();
  if (outline && ow > 0) { x.lineWidth = ow; x.strokeStyle = outline; x.stroke(); }
  if (sprites.size > 400) sprites.clear();
  sprites.set(key, c);
  return c;
}

/** Draw a data layer into ctx (CSS-pixel transform already set, `dpr` device pixels per CSS pixel). */
export function drawDataLayer(ctx: CanvasRenderingContext2D, view: View, L: Layer, t: Table, k: number, dpr: number): number {
  const st = L.style || {};
  const prep = t.prepared();
  const ci = t.classes(st.classify);
  const colors = ci ? classColors(st.classify) : null;
  const w = view.world;
  const size = Number(st.size ?? 7) * k;
  const margin = (Math.max(size * 1.6, 12)) / w;
  const [x0, y0] = view.toMerc(0, 0), [x1, y1] = view.toMerc(view.width, view.height);
  const bx0 = x0 - margin, by0 = y0 - margin, bx1 = x1 + margin, by1 = y1 + margin;
  const px = (m: number) => (m - view.cx) * w + view.width / 2;
  const py = (m: number) => (m - view.cy) * w + view.height / 2;
  let drawn = 0;

  // ---- lines and areas
  const fo = Number(st.fill_opacity ?? 0.25), sw = Number(st.stroke_width ?? 1.2) * k, lw = Number(st.line_width ?? 1.6) * k;
  const tiny: number[] = [];
  const cm = prep.cm, rs = t.ringStart, fr = t.featRing, bb = prep.bbox;
  // group by colour so each colour is one path (fast), last class first like the desktop app
  const groups = new Map<string, { areas: number[]; lines: number[] }>();
  for (const i of prep.shapes) {
    if (bb[i * 4 + 2] < bx0 || bb[i * 4] > bx1 || bb[i * 4 + 3] < by0 || bb[i * 4 + 1] > by1) continue;
    if (ci && ci[i] < 0) continue;
    const ext = Math.max(bb[i * 4 + 2] - bb[i * 4], bb[i * 4 + 3] - bb[i * 4 + 1]) * w;
    if (ext < 1.5) { tiny.push(i); continue; }
    const color = colors ? colors[ci![i]] : '';
    let g = groups.get(color);
    if (!g) groups.set(color, g = { areas: [], lines: [] });
    (t.kind[i] === AREA ? g.areas : g.lines).push(i);
  }
  const keys = [...groups.keys()];
  if (colors) keys.sort((a, b) => colors.lastIndexOf(b) - colors.lastIndexOf(a));
  for (const color of keys) {
    const g = groups.get(color)!;
    if (g.areas.length) {
      const path = new Path2D();
      for (const i of g.areas) {
        for (let r = fr[i]; r < fr[i + 1]; r++) {
          for (let v = rs[r]; v < rs[r + 1]; v++) {
            const X = px(cm[v * 2]), Y = py(cm[v * 2 + 1]);
            v === rs[r] ? path.moveTo(X, Y) : path.lineTo(X, Y);
          }
          path.closePath();
        }
      }
      if (fo > 0) { ctx.globalAlpha = fo; ctx.fillStyle = color || st.fill || '#888888'; ctx.fill(path, 'evenodd'); ctx.globalAlpha = 1; }
      if (sw > 0) {
        ctx.lineWidth = sw; ctx.strokeStyle = color || st.stroke || '#555555'; ctx.lineJoin = 'round';
        dash(ctx, st.stroke_dash || 'solid', sw); ctx.stroke(path); ctx.setLineDash([]);
      }
      drawn += g.areas.length;
    }
    if (g.lines.length) {
      const path = new Path2D();
      for (const i of g.lines) {
        for (let r = fr[i]; r < fr[i + 1]; r++) {
          for (let v = rs[r]; v < rs[r + 1]; v++) {
            const X = px(cm[v * 2]), Y = py(cm[v * 2 + 1]);
            v === rs[r] ? path.moveTo(X, Y) : path.lineTo(X, Y);
          }
        }
      }
      ctx.lineWidth = lw; ctx.strokeStyle = color || st.line_color || '#555555'; ctx.lineJoin = 'round'; ctx.lineCap = 'round';
      dash(ctx, st.dash || 'solid', lw); ctx.stroke(path); ctx.setLineDash([]);
      drawn += g.lines.length;
    }
  }
  for (const i of tiny) {
    ctx.fillStyle = colors ? colors[ci![i]] : (t.kind[i] === AREA ? st.stroke : st.line_color) || '#555555';
    const d = Math.max(1.6, 2.2 * k);
    ctx.fillRect(px(prep.mx[i]) - d / 2, py(prep.my[i]) - d / 2, d, d);
  }
  drawn += tiny.length;

  // ---- points
  const ids: number[] = [];
  for (const i of prep.points) {
    const x = prep.mx[i], y = prep.my[i];
    if (x < bx0 || x > bx1 || y < by0 || y > by1) continue;
    if (ci && ci[i] < 0) continue;
    ids.push(i);
  }
  if (ids.length) {
    const marker = st.marker || 'circle';
    const outline = st.outline || null, ow = Number(st.outline_width ?? 1) * k;
    const thin = ids.length > VECTOR_POINTS;
    // one marker per cell of about half a marker, when there are many – they overlap anyway
    const cell = Math.max(1, size * 0.45);
    const gw = Math.ceil(view.width / cell) + 4;
    const gh = Math.ceil(view.height / cell) + 4;
    const cellOf = (i: number) => {
      const c = (Math.floor(px(prep.mx[i]) / cell) + 2) + (Math.floor(py(prep.my[i]) / cell) + 2) * gw;
      return c >= 0 && c < gw * gh ? c : -1;
    };
    // draw the last class first so the first class in the legend ends up on top
    let order = ids;
    if (ci) {                                   // group by class without sorting (counting sort)
      const nc = colors!.length;
      const cnt = new Int32Array(nc + 1);
      for (const i of ids) cnt[nc - 1 - ci[i]]++;
      for (let c = 1; c <= nc; c++) cnt[c] += cnt[c - 1];
      const out = new Array<number>(ids.length);
      for (let j = ids.length - 1; j >= 0; j--) { const i = ids[j]; out[--cnt[nc - 1 - ci[i]]] = i; }
      order = out;
    }
    // when thinning, only the marker that would end up on top of each cell is drawn
    const top = thin ? new Int32Array(gw * gh).fill(-1) : null;
    if (top) for (const i of order) { const c = cellOf(i); if (c >= 0) top[c] = i; }
    const symSize = size * 1.5;
    for (const i of order) {
      if (top) { const c = cellOf(i); if (c < 0 || top[c] !== i) continue; }
      const X = px(prep.mx[i]), Y = py(prep.my[i]);
      const color = colors ? colors[ci![i]] : st.color || '#d95f02';
      if (marker === 'circle') {
        const sp = circleSprite(color, outline, ow * dpr, size * dpr);
        const s = sp.width / dpr;
        ctx.drawImage(sp, X - s / 2, Y - s / 2, s, s);
      } else {
        const img = symbolImage(marker, color, outline || '#ffffff');
        if (img) { const r = symbolRect(X, Y, marker, symSize, false); ctx.drawImage(img, r.x, r.y, r.w, r.h); }
      }
    }
    drawn += ids.length;
  }

  // ---- labels from a field, placed greedily so they don't overlap
  const lab = L.label || {};
  const col = lab.field ? t.cols[lab.field] : null;
  if (col) {
    const lst = { ...(lab.style || {}) };
    const placed: [number, number, number, number][] = [];
    const cand: number[] = [];
    for (const i of [...ids, ...prep.shapes]) {
      const x = prep.mx[i], y = prep.my[i];
      if (!(x >= x0 && x <= x1 && y >= y0 && y <= y1)) continue;
      if (ci && ci[i] < 0) continue;
      cand.push(i);
      if (cand.length > 5000) break;
    }
    for (const i of cand) {
      const v = col.kind === 'num' ? col.values[i] : col.codes[i] < 0 ? null : col.cats[col.codes[i]];
      if (v === null || (typeof v === 'number' && !Number.isFinite(v))) continue;
      const text = typeof v === 'number' ? String(col.kind === 'num' && col.isInt ? Math.round(v) : +v.toPrecision(8)) : v;
      if (!text) continue;
      const pt = t.kind[i] === POINT;
      const s = { ...lst, align: pt ? 'left' : 'center' };
      const lay = layoutText(ctx, text, s, k);
      let X = px(prep.mx[i]), Y = py(prep.my[i]);
      if (pt) { X += size / 2 + 2 * k; Y -= size * 0.45; }
      const box: [number, number, number, number] = [X + lay.box.x - 2, Y + lay.box.y - 2, X + lay.box.x + lay.box.w + 2, Y + lay.box.y + lay.box.h + 2];
      if (placed.some(b => box[0] < b[2] && box[2] > b[0] && box[1] < b[3] && box[3] > b[1])) continue;
      placed.push(box);
      drawText(ctx, X, Y, text, s, k);
      if (placed.length > 1500) break;
    }
  }
  return drawn;
}

/** The feature under a screen point (CSS px), or -1: points by distance, areas by containment, lines by distance. */
export function featureAt(view: View, L: Layer, t: Table, x: number, y: number): number {
  const prep = t.prepared();
  const st = L.style || {};
  const ci = t.classes(st.classify);
  const w = view.world;
  const [mx, my] = view.toMerc(x, y);
  const r = Math.max(5, Number(st.size ?? 7) * (st.marker && st.marker !== 'circle' ? 0.75 : 0.5) + 3) / w;
  let best = -1, bestD = Infinity;
  for (const i of prep.points) {
    if (ci && ci[i] < 0) continue;
    const dx = prep.mx[i] - mx, dy = prep.my[i] - my;
    if (Math.abs(dx) > r || Math.abs(dy) > r) continue;
    const d = dx * dx + dy * dy;
    if (d < bestD) { bestD = d; best = i; }
  }
  if (best >= 0) return best;
  const tol = 5 / w;
  const cm = prep.cm, rs = t.ringStart, fr = t.featRing, bb = prep.bbox;
  for (let j = prep.shapes.length - 1; j >= 0; j--) {
    const i = prep.shapes[j];
    if (ci && ci[i] < 0) continue;
    if (mx < bb[i * 4] - tol || mx > bb[i * 4 + 2] + tol || my < bb[i * 4 + 1] - tol || my > bb[i * 4 + 3] + tol) continue;
    let inside = false;
    for (let rr = fr[i]; rr < fr[i + 1]; rr++) {
      const a = rs[rr], b = rs[rr + 1];
      for (let v = a; v < b - 1; v++) {
        const ax = cm[v * 2], ay = cm[v * 2 + 1], bx = cm[v * 2 + 2], by = cm[v * 2 + 3];
        if (t.kind[i] === AREA && (ay > my) !== (by > my) && mx < (bx - ax) * (my - ay) / (by - ay) + ax) inside = !inside;
        const dx = bx - ax, dy = by - ay;
        const u = Math.max(0, Math.min(1, ((mx - ax) * dx + (my - ay) * dy) / (dx * dx + dy * dy || 1)));
        if (Math.hypot(mx - ax - u * dx, my - ay - u * dy) < tol) return i;
      }
      if (t.kind[i] === AREA) {     // closing edge
        const ax = cm[(b - 1) * 2], ay = cm[(b - 1) * 2 + 1], bx = cm[a * 2], by = cm[a * 2 + 1];
        if ((ay > my) !== (by > my) && mx < (bx - ax) * (my - ay) / (by - ay) + ax) inside = !inside;
      }
    }
    if (inside) return i;
  }
  return -1;
}

/** Outline a feature (the selection highlight). */
export function highlightFeature(ctx: CanvasRenderingContext2D, view: View, L: Layer, t: Table, i: number) {
  const prep = t.prepared();
  const w = view.world;
  const px = (m: number) => (m - view.cx) * w + view.width / 2;
  const py = (m: number) => (m - view.cy) * w + view.height / 2;
  ctx.strokeStyle = '#2f8cff'; ctx.lineWidth = 2.4; ctx.setLineDash([]);
  if (t.kind[i] === POINT) {
    const r = Number(L.style?.size ?? 7) / 2 + 4;
    ctx.beginPath(); ctx.arc(px(prep.mx[i]), py(prep.my[i]), r, 0, Math.PI * 2); ctx.stroke();
    return;
  }
  const cm = prep.cm;
  ctx.beginPath();
  for (let r = t.featRing[i]; r < t.featRing[i + 1]; r++) {
    for (let v = t.ringStart[r]; v < t.ringStart[r + 1]; v++) {
      const X = px(cm[v * 2]), Y = py(cm[v * 2 + 1]);
      v === t.ringStart[r] ? ctx.moveTo(X, Y) : ctx.lineTo(X, Y);
    }
    if (t.kind[i] === AREA) ctx.closePath();
  }
  ctx.stroke();
}

export { LINE };
