// Drawing data layers – the browser counterpart of gui/datadraw.py: points as circles or map
// symbols, lines, areas, colour by class, and labels from a field. Large point layers are thinned
// to one marker per small screen cell, so hundreds of thousands of features still draw quickly.

import type { Layer } from './doc';
import type { View } from './geo';
import { AREA, LINE, POINT, Table, classColors } from './table';
import { DASHES, drawText, layoutText, symbolImage, symbolRect } from './render';
import { HEAT_RAMPS } from './shared/styles';
import { numericValues } from './table';

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
  if (st.render === 'heatmap') return drawHeatmap(ctx, view, L, t, k, dpr);
  if (st.render === 'clusters') return drawClusters(ctx, view, L, t, k, dpr);
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

// ---------------------------------------------------------------- heat maps and clusters
/** Density of the features as a smooth colour surface (a Gaussian kernel, three box blurs). */
export function drawHeatmap(ctx: CanvasRenderingContext2D, view: View, L: Layer, t: Table, k: number, dpr: number): number {
  const st = L.style || {};
  const prep = t.prepared();
  const ci = t.classes(st.classify);
  const radius = Math.max(2, Number(st.heat_radius ?? 20) * k);
  const res = Math.max(1, radius * dpr / 5);                 // grid cell in device pixels
  const pad = Math.ceil(radius * 3 * dpr / res);
  const gw = Math.ceil(view.width * dpr / res) + 2 * pad, gh = Math.ceil(view.height * dpr / res) + 2 * pad;
  if (gw * gh > 6e6) return 0;
  const grid = new Float32Array(gw * gh);
  const w = view.world;
  const wf = st.heat_weight && t.cols[st.heat_weight] ? numericValues(t, st.heat_weight) : null;
  let n = 0;
  for (let i = 0; i < t.n; i++) {
    if (!Number.isFinite(prep.mx[i])) continue;
    if (ci && ci[i] < 0) continue;
    const gx = Math.floor(((prep.mx[i] - view.cx) * w + view.width / 2) * dpr / res) + pad;
    const gy = Math.floor(((prep.my[i] - view.cy) * w + view.height / 2) * dpr / res) + pad;
    if (gx < 0 || gy < 0 || gx >= gw || gy >= gh) continue;
    const v = wf ? wf[i] : 1;
    if (!Number.isFinite(v)) continue;
    grid[gy * gw + gx] += v;
    n++;
  }
  if (!n) return 0;
  // three box blurs ≈ a Gaussian with sigma = radius / 2
  const sigma = radius * dpr / res / 2;
  const box = Math.max(1, Math.round(Math.sqrt(12 * sigma * sigma / 3 + 1)));
  const tmp = new Float32Array(grid.length);
  const blurH = (src: Float32Array, dst: Float32Array, r: number) => {
    const d = 2 * r + 1;
    for (let y = 0; y < gh; y++) {
      const o = y * gw;
      let acc = 0;
      for (let x = -r; x <= r; x++) if (x >= 0 && x < gw) acc += src[o + x];
      for (let x = 0; x < gw; x++) {
        dst[o + x] = acc / d;
        const add = x + r + 1, sub = x - r;
        if (add < gw) acc += src[o + add];
        if (sub >= 0) acc -= src[o + sub];
      }
    }
  };
  const blurV = (src: Float32Array, dst: Float32Array, r: number) => {
    const d = 2 * r + 1;
    for (let x = 0; x < gw; x++) {
      let acc = 0;
      for (let y = -r; y <= r; y++) if (y >= 0 && y < gh) acc += src[y * gw + x];
      for (let y = 0; y < gh; y++) {
        dst[y * gw + x] = acc / d;
        const add = y + r + 1, sub = y - r;
        if (add < gh) acc += src[add * gw + x];
        if (sub >= 0) acc -= src[sub * gw + x];
      }
    }
  };
  const r = Math.max(1, Math.floor(box / 2));
  for (let pass = 0; pass < 3; pass++) { blurH(grid, tmp, r); blurV(tmp, grid, r); }
  // scale: the 99.5th percentile of the non-empty cells is full colour (÷ intensity)
  let max = 0;
  for (let i = 0; i < grid.length; i++) if (grid[i] > max) max = grid[i];
  if (max <= 0) return 0;
  const nz: number[] = [];
  const step = Math.max(1, Math.floor(grid.length / 200000));
  for (let i = 0; i < grid.length; i += step) if (grid[i] > max * 1e-4) nz.push(grid[i]);
  nz.sort((a, b) => a - b);
  const vmax = (nz[Math.floor(nz.length * 0.995)] || max) / Math.max(0.05, Number(st.heat_intensity ?? 1));
  const ramp = HEAT_RAMPS[st.heat_ramp || 'heat'] || HEAT_RAMPS.heat;
  const lut = new Uint8ClampedArray(256 * 4);
  for (let j = 0; j < 256; j++) {
    const v = j / 255;
    let a = 0;
    while (a < ramp.length - 2 && v > ramp[a + 1][0]) a++;
    const [p0, c0] = ramp[a], [p1, c1] = ramp[a + 1];
    const f = Math.max(0, Math.min(1, (v - p0) / ((p1 - p0) || 1)));
    for (let ch = 0; ch < 4; ch++) lut[j * 4 + ch] = c0[ch] + (c1[ch] - c0[ch]) * f;
  }
  const iw = gw - 2 * pad, ih = gh - 2 * pad;
  const img = new ImageData(iw, ih);
  for (let y = 0; y < ih; y++) for (let x = 0; x < iw; x++) {
    const v = grid[(y + pad) * gw + x + pad] / vmax;
    const j = Math.max(0, Math.min(255, Math.round(v * 255))) * 4;
    const o = (y * iw + x) * 4;
    img.data[o] = lut[j]; img.data[o + 1] = lut[j + 1]; img.data[o + 2] = lut[j + 2]; img.data[o + 3] = lut[j + 3];
  }
  const cv = document.createElement('canvas');
  cv.width = iw; cv.height = ih;
  cv.getContext('2d')!.putImageData(img, 0, 0);
  ctx.save();
  ctx.imageSmoothingEnabled = true;
  ctx.imageSmoothingQuality = 'high';
  ctx.drawImage(cv, 0, 0, iw * res / dpr, ih * res / dpr);
  ctx.restore();
  return n;
}

const short = (n: number) => n >= 10000 ? `${Math.round(n / 1000)}k` : n >= 1000 ? `${(n / 1000).toFixed(1).replace('.0', '')}k` : String(n);

/** Nearby features gathered into circles with their count (a grid in map space, so groups stay put while panning). */
export function drawClusters(ctx: CanvasRenderingContext2D, view: View, L: Layer, t: Table, k: number, dpr: number): number {
  const st = L.style || {};
  const prep = t.prepared();
  const ci = t.classes(st.classify);
  const cell = Math.max(20, Number(st.cluster_px ?? 56)) * k;
  const w = view.world;
  const [x0, y0] = view.toMerc(-cell, -cell), [x1, y1] = view.toMerc(view.width + cell, view.height + cell);
  const groups = new Map<number, { n: number; sx: number; sy: number; i: number }>();
  for (let i = 0; i < t.n; i++) {
    const mx = prep.mx[i], my = prep.my[i];
    if (!(mx >= x0 && mx <= x1 && my >= y0 && my <= y1)) continue;
    if (ci && ci[i] < 0) continue;
    const key = Math.floor(my * w / cell) * 4294967311 + Math.floor(mx * w / cell);
    const g = groups.get(key);
    if (g) { g.n++; g.sx += mx; g.sy += my; } else groups.set(key, { n: 1, sx: mx, sy: my, i });
  }
  const px = (m: number) => (m - view.cx) * w + view.width / 2;
  const py = (m: number) => (m - view.cy) * w + view.height / 2;
  const color = st.color || '#d95f02';
  const size = Number(st.size ?? 7) * k;
  const list = [...groups.values()].sort((a, b) => a.n - b.n);
  for (const g of list) {
    const X = px(g.sx / g.n), Y = py(g.sy / g.n);
    if (g.n === 1) {
      ctx.beginPath(); ctx.arc(X, Y, size / 2, 0, Math.PI * 2);
      ctx.fillStyle = color; ctx.fill();
      if (st.outline) { ctx.lineWidth = Number(st.outline_width ?? 1) * k; ctx.strokeStyle = st.outline; ctx.stroke(); }
      continue;
    }
    const r = (9 + 4.2 * Math.log10(g.n) ** 1.3 * 2.2) * k;
    ctx.beginPath(); ctx.arc(X, Y, r, 0, Math.PI * 2);
    ctx.globalAlpha = 0.88; ctx.fillStyle = color; ctx.fill(); ctx.globalAlpha = 1;
    ctx.lineWidth = 1.6 * k; ctx.strokeStyle = '#ffffff'; ctx.stroke();
    drawText(ctx, X, Y, short(g.n), { font: 'Segoe UI', size: 7.5 + Math.min(3, Math.log10(g.n)), bold: true, color: '#ffffff', halo: '', align: 'center' }, k);
  }
  return t.n;
}

/** Outline the selected features (all of them if few; otherwise thinned like points). */
export function highlightMask(ctx: CanvasRenderingContext2D, view: View, L: Layer, t: Table, mask: Uint8Array) {
  const prep = t.prepared();
  const w = view.world;
  const px = (m: number) => (m - view.cx) * w + view.width / 2;
  const py = (m: number) => (m - view.cy) * w + view.height / 2;
  const [x0, y0] = view.toMerc(-20, -20), [x1, y1] = view.toMerc(view.width + 20, view.height + 20);
  const bb = prep.bbox;
  const r = Number(L.style?.size ?? 7) / 2 + 2.5;
  const ring = new Path2D(), shapes = new Path2D();
  let pts = 0;
  const seen = new Set<number>();
  for (let i = 0; i < t.n; i++) {
    if (!mask[i]) continue;
    if (bb[i * 4 + 2] < x0 || bb[i * 4] > x1 || bb[i * 4 + 3] < y0 || bb[i * 4 + 1] > y1) continue;
    if (t.kind[i] === POINT) {
      const X = px(prep.mx[i]), Y = py(prep.my[i]);
      const c = Math.floor(X / 3) * 100000 + Math.floor(Y / 3);
      if (seen.has(c)) continue;
      seen.add(c);
      ring.moveTo(X + r, Y); ring.arc(X, Y, r, 0, Math.PI * 2);
      if (++pts > 60000) break;
    } else {
      for (let rr = t.featRing[i]; rr < t.featRing[i + 1]; rr++) {
        for (let v = t.ringStart[rr]; v < t.ringStart[rr + 1]; v++) {
          const X = px(prep.cm[v * 2]), Y = py(prep.cm[v * 2 + 1]);
          v === t.ringStart[rr] ? shapes.moveTo(X, Y) : shapes.lineTo(X, Y);
        }
        if (t.kind[i] === AREA) shapes.closePath();
      }
    }
  }
  ctx.setLineDash([]);
  for (const [col, wd] of [['#ffffff', 4.2], ['#00b4ff', 2.2]] as [string, number][]) {
    ctx.strokeStyle = col; ctx.lineWidth = wd; ctx.lineJoin = 'round';
    ctx.stroke(ring); ctx.stroke(shapes);
  }
}
