// Imported data, stored by column – the same layout as the desktop app's table.py, so tables load
// from and save to .mapmaker files unchanged.
//
// Per feature: a representative position (lon/lat; the point itself for points) and a kind
// (0 point, 1 line, 2 area). Vertices of lines and areas in flat arrays: coords (lon, lat pairs),
// ring_start (vertex offset of each ring), feat_ring (ring offset of each feature).
// Attribute columns: numbers as Float64Array, everything else as codes into a list of values.

import { lonlatToMerc } from './geo';
import { readNpy, writeNpy } from './npy';
import { QUALITATIVE, SEQUENTIAL } from './shared/styles';
import type { ZipEntry } from './zip';

export const POINT = 0, LINE = 1, AREA = 2;
export const KIND_NAMES = ['point', 'line', 'polygon'];

export type Col = { kind: 'num'; values: Float64Array; isInt: boolean } | { kind: 'cat'; codes: Int32Array; cats: string[] };
export type Value = string | number | boolean | null | undefined;

export function colGet(c: Col, i: number): string | number | null {
  if (c.kind === 'num') { const v = c.values[i]; return Number.isFinite(v) ? v : null; }
  const k = c.codes[i];
  return k < 0 ? null : c.cats[k];
}

export function colText(c: Col, i: number): string {
  const v = colGet(c, i);
  if (v === null) return '';
  if (typeof v === 'number') return c.kind === 'num' && c.isInt ? String(Math.round(v)) : String(+v.toPrecision(10));
  return v;
}

/** Distinct values with their counts, most common first. */
export function colCounts(c: Col, limit = 300): [string | number, number][] {
  if (c.kind === 'cat') {
    const n = new Int32Array(c.cats.length);
    for (const k of c.codes) if (k >= 0) n[k]++;
    return c.cats.map((v, i) => [v, n[i]] as [string, number]).filter(x => x[1] > 0).sort((a, b) => b[1] - a[1]).slice(0, limit);
  }
  const m = new Map<number, number>();
  for (const v of c.values) if (Number.isFinite(v)) m.set(v, (m.get(v) || 0) + 1);
  return [...m.entries()].sort((a, b) => b[1] - a[1]).slice(0, limit);
}

// ---------------------------------------------------------------- building
class ColBuilder {
  index = new Map<any, number>();
  cats: (string | number)[] = [];
  codes: number[] = [];
  numeric = true;
  ints = true;

  add(v: Value) {
    if (v === null || v === undefined || v === '' || (typeof v === 'number' && !Number.isFinite(v))) { this.codes.push(-1); return; }
    if (typeof v !== 'number') { v = String(v); this.numeric = false; }
    else if (!Number.isInteger(v)) this.ints = false;
    let c = this.index.get(v);
    if (c === undefined) { c = this.cats.length; this.index.set(v, c); this.cats.push(v); }
    this.codes.push(c);
  }

  finish(): Col {
    const codes = Int32Array.from(this.codes);
    if (this.numeric && this.cats.length) {
      const vals = this.cats.map(Number);
      const values = new Float64Array(codes.length);
      for (let i = 0; i < codes.length; i++) values[i] = codes[i] < 0 ? NaN : vals[codes[i]];
      return { kind: 'num', values, isInt: this.ints && vals.every(v => Math.abs(v) < 2 ** 53) };
    }
    return { kind: 'cat', codes, cats: this.cats.map(String) };
  }
}

export class TableBuilder {
  fields: string[];
  cols = new Map<string, ColBuilder>();
  n = 0;
  kind: number[] = [];
  px: number[] = [];
  py: number[] = [];
  coords: number[] = [];
  ringStart: number[] = [0];
  ringFeat: number[] = [];

  constructor(fields: string[] = []) {
    this.fields = [...fields];
    for (const f of this.fields) this.cols.set(f, new ColBuilder());
  }

  private attrs(a: Record<string, Value>) {
    for (const k of Object.keys(a)) {
      if (!this.cols.has(k)) {
        const b = new ColBuilder();
        for (let i = 0; i < this.n; i++) b.codes.push(-1);
        this.cols.set(k, b);
        this.fields.push(k);
      }
    }
    for (const [f, b] of this.cols) b.add(a[f]);
  }

  addPoint(lon: number, lat: number, a: Record<string, Value> = {}) {
    if (!Number.isFinite(lon) || !Number.isFinite(lat)) return false;
    this.attrs(a);
    this.kind.push(POINT); this.px.push(lon); this.py.push(lat);
    this.n++;
    return true;
  }

  /** A line (kind 1) or area (kind 2) as a list of rings, each a list of [lon, lat]. */
  addShape(kind: number, rings: number[][][], a: Record<string, Value> = {}) {
    const ok = rings.filter(r => r.length >= (kind === AREA ? 3 : 2));
    if (!ok.length) return false;
    this.attrs(a);
    for (const r of ok) {
      for (const p of r) this.coords.push(p[0], p[1]);
      this.ringStart.push(this.coords.length / 2);
      this.ringFeat.push(this.n);
    }
    this.kind.push(kind); this.px.push(NaN); this.py.push(NaN);
    this.n++;
    return true;
  }

  finish(): Table {
    const n = this.n;
    const featRing = new Float64Array(n + 1);
    for (const f of this.ringFeat) featRing[f + 1]++;
    for (let i = 0; i < n; i++) featRing[i + 1] += featRing[i];
    const cols: Record<string, Col> = {};
    for (const [f, b] of this.cols) cols[f] = b.finish();
    const t = new Table(Float64Array.from(this.px), Float64Array.from(this.py), Int8Array.from(this.kind),
      Float64Array.from(this.coords), Float64Array.from(this.ringStart), featRing, cols, [...this.fields]);
    t.fillRepPoints();
    return t;
  }
}

// ---------------------------------------------------------------- the table
export interface Prepared {
  mx: Float64Array; my: Float64Array;          // representative point in unit Web Mercator
  cm: Float64Array;                             // all vertices in unit Web Mercator (x, y pairs)
  bbox: Float64Array;                           // per feature: minx, miny, maxx, maxy (Mercator)
  points: Int32Array; shapes: Int32Array;
}

export class Table {
  n: number;
  private prep: Prepared | null = null;
  private clsCache = new Map<string, Int16Array>();
  /** the arrays as .npy files, kept once made (autosave and saving reuse them) */
  private files: ZipEntry[] | null = null;

  constructor(public x: Float64Array, public y: Float64Array, public kind: Int8Array, public coords: Float64Array,
              public ringStart: Float64Array, public featRing: Float64Array, public cols: Record<string, Col>, public fields: string[]) {
    this.n = kind.length;
  }

  rings(i: number): Float64Array[] {
    const out: Float64Array[] = [];
    for (let r = this.featRing[i]; r < this.featRing[i + 1]; r++) out.push(this.coords.subarray(this.ringStart[r] * 2, this.ringStart[r + 1] * 2));
    return out;
  }

  row(i: number): Record<string, string | number | null> {
    const o: Record<string, string | number | null> = {};
    for (const f of this.fields) o[f] = colGet(this.cols[f], i);
    return o;
  }

  mainKind(): string {
    const c = [0, 0, 0];
    for (const k of this.kind) c[k]++;
    return KIND_NAMES[c.indexOf(Math.max(...c))];
  }

  bbox(): [number, number, number, number] | null {
    let w = Infinity, s = Infinity, e = -Infinity, n = -Infinity;
    const take = (x: number, y: number) => { if (Number.isFinite(x) && Number.isFinite(y)) { w = Math.min(w, x); e = Math.max(e, x); s = Math.min(s, y); n = Math.max(n, y); } };
    for (let i = 0; i < this.n; i++) take(this.x[i], this.y[i]);
    for (let i = 0; i < this.coords.length; i += 2) take(this.coords[i], this.coords[i + 1]);
    return w <= e ? [w, s, e, n] : null;
  }

  /** Representative points: the middle vertex of a line, the mean of an area's first ring. */
  fillRepPoints() {
    for (let i = 0; i < this.n; i++) {
      if (this.kind[i] === POINT) continue;
      const r = this.featRing[i];
      const a = this.ringStart[r], b = this.ringStart[r + 1];
      if (this.kind[i] === LINE) {
        const m = (a + b) >> 1;
        this.x[i] = this.coords[m * 2]; this.y[i] = this.coords[m * 2 + 1];
      } else {
        let sx = 0, sy = 0;
        for (let v = a; v < b; v++) { sx += this.coords[v * 2]; sy += this.coords[v * 2 + 1]; }
        this.x[i] = sx / Math.max(1, b - a); this.y[i] = sy / Math.max(1, b - a);
      }
    }
  }

  prepared(): Prepared {
    if (this.prep) return this.prep;
    const n = this.n;
    const mx = new Float64Array(n), my = new Float64Array(n);
    for (let i = 0; i < n; i++) {
      if (Number.isFinite(this.x[i])) { const [a, b] = lonlatToMerc(this.x[i], this.y[i]); mx[i] = a; my[i] = b; }
      else { mx[i] = NaN; my[i] = NaN; }
    }
    const cm = new Float64Array(this.coords.length);
    for (let v = 0; v < cm.length; v += 2) { const [a, b] = lonlatToMerc(this.coords[v], this.coords[v + 1]); cm[v] = a; cm[v + 1] = b; }
    const bbox = new Float64Array(n * 4);
    const pts: number[] = [], shp: number[] = [];
    for (let i = 0; i < n; i++) {
      if (this.kind[i] === POINT) {
        bbox[i * 4] = bbox[i * 4 + 2] = mx[i]; bbox[i * 4 + 1] = bbox[i * 4 + 3] = my[i];
        if (Number.isFinite(mx[i])) pts.push(i);
        continue;
      }
      let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
      const v0 = this.ringStart[this.featRing[i]], v1 = this.ringStart[this.featRing[i + 1]];
      for (let v = v0; v < v1; v++) {
        const x = cm[v * 2], y = cm[v * 2 + 1];
        if (x < x0) x0 = x; if (x > x1) x1 = x; if (y < y0) y0 = y; if (y > y1) y1 = y;
      }
      bbox.set([x0, y0, x1, y1], i * 4);
      if (v1 > v0) shp.push(i);
    }
    this.prep = { mx, my, cm, bbox, points: Int32Array.from(pts), shapes: Int32Array.from(shp) };
    return this.prep;
  }

  /** The class of every feature (see classIndex), cached per classification. */
  classes(cls: any): Int16Array | null {
    if (!cls || (cls.mode || 'single') === 'single' || !this.cols[cls.field]) return null;
    const key = JSON.stringify(cls);
    let hit = this.clsCache.get(key);
    if (!hit) {
      hit = classIndex(this, cls);
      this.clsCache.set(key, hit);
      if (this.clsCache.size > 8) this.clsCache.delete(this.clsCache.keys().next().value!);
    }
    return hit;
  }

  // -- files
  toEntries(prefix: string): ZipEntry[] {
    if (!this.files) {
      const files: ZipEntry[] = [];
      const add = (name: string, data: Uint8Array) => files.push({ name, data });
      add('x.npy', writeNpy(this.x));
      add('y.npy', writeNpy(this.y));
      add('kind.npy', writeNpy(this.kind));
      add('coords.npy', writeNpy(this.coords, [this.coords.length / 2, 2]));
      add('ring_start.npy', writeNpy(this.ringStart, undefined, true));
      add('feat_ring.npy', writeNpy(this.featRing, undefined, true));
      const meta: any = { n: this.n, fields: this.fields, cols: {} };
      this.fields.forEach((f, j) => {
        const c = this.cols[f];
        if (c.kind === 'num') { add(`c${j}.npy`, writeNpy(c.values)); meta.cols[f] = { kind: 'num', int: c.isInt, file: `c${j}` }; }
        else { add(`c${j}.npy`, writeNpy(c.codes)); meta.cols[f] = { kind: 'cat', file: `c${j}`, cats: c.cats }; }
      });
      add('meta.json', new TextEncoder().encode(JSON.stringify(meta)));
      this.files = files;
    }
    return this.files.map(e => ({ name: `${prefix}/${e.name}`, data: e.data }));
  }

  static fromEntries(get: (name: string) => Uint8Array | undefined): Table {
    const need = (name: string) => { const d = get(name); if (!d) throw new Error(`missing ${name}`); return d; };
    const npy = (name: string) => readNpy(need(name + '.npy'));
    const meta = JSON.parse(new TextDecoder().decode(need('meta.json')));
    const f64 = (a: any) => a instanceof Float64Array ? a : Float64Array.from(a);
    const cols: Record<string, Col> = {};
    for (const f of meta.fields) {
      const m = meta.cols[f];
      const d = npy(m.file).data;
      cols[f] = m.kind === 'num' ? { kind: 'num', values: f64(d), isInt: !!m.int } : { kind: 'cat', codes: d instanceof Int32Array ? d : Int32Array.from(d), cats: m.cats };
    }
    const kind = npy('kind').data;
    return new Table(f64(npy('x').data), f64(npy('y').data), kind instanceof Int8Array ? kind : Int8Array.from(kind),
      f64(npy('coords').data), f64(npy('ring_start').data), f64(npy('feat_ring').data), cols, [...meta.fields]);
  }
}

// ---------------------------------------------------------------- colour by attribute
export function schemeColors(name: string, n: number): string[] {
  if (QUALITATIVE[name]) { const c = QUALITATIVE[name]; return Array.from({ length: n }, (_, i) => c[i % c.length]); }
  const c = SEQUENTIAL[name] || SEQUENTIAL.Blues;
  if (n <= 1) return [c[6]];
  const lo = 2, hi = c.length - 1;
  return Array.from({ length: n }, (_, i) => c[Math.round(lo + (hi - lo) * i / (n - 1))]);
}

const pretty = (v: number) => { if (!Number.isFinite(v) || v === 0) return v; const e = 10 ** (Math.floor(Math.log10(Math.abs(v))) - 1); return Math.round(v / e) * e; };
export const fmtNum = (v: number) => Math.abs(v) >= 1000 ? Math.round(v).toLocaleString('sv-SE') : String(+v.toPrecision(6));

export function numericValues(t: Table, field: string): Float64Array {
  const c = t.cols[field];
  if (c.kind === 'num') return c.values;
  const nums = c.cats.map(s => { const v = parseFloat(String(s).replace(',', '.')); return Number.isFinite(v) ? v : NaN; });
  const out = new Float64Array(t.n);
  for (let i = 0; i < t.n; i++) out[i] = c.codes[i] < 0 ? NaN : nums[c.codes[i]];
  return out;
}

export function makeCategories(t: Table, field: string, scheme = 'Set2', maxClasses = 12) {
  const counts = colCounts(t.cols[field], 100000);
  const top = counts.slice(0, maxClasses);
  const colors = schemeColors(scheme, top.length);
  const rest = counts.slice(maxClasses).reduce((s, c) => s + c[1], 0);
  return {
    mode: 'categories', field, scheme,
    classes: top.map(([v, n], i) => ({ value: v, label: String(v), color: colors[i], show: true, count: n })),
    other: { show: rest > 0, color: '#b4b4b4', label: 'Other', count: rest },
  };
}

export function makeGraduated(t: Table, field: string, n = 5, method = 'quantile', scheme = 'YlOrRd') {
  const v = [...numericValues(t, field)].filter(Number.isFinite).sort((a, b) => a - b);
  if (!v.length) throw new Error(`${field} holds no numbers.`);
  const lo = v[0], hi = v[v.length - 1];
  const q = (p: number) => { const i = p * (v.length - 1); const a = Math.floor(i); return v[a] + (v[Math.min(a + 1, v.length - 1)] - v[a]) * (i - a); };
  const inner = Array.from({ length: n - 1 }, (_, i) => method === 'equal' ? lo + (hi - lo) * (i + 1) / n : q((i + 1) / n)).map(pretty);
  let edges = [...new Set([lo, ...inner, hi])].sort((a, b) => a - b);
  if (edges.length < 2) edges = [lo, hi > lo ? hi : lo + 1];
  const colors = schemeColors(scheme, edges.length - 1);
  const classes = edges.slice(0, -1).map((a, i) => {
    const b = edges[i + 1], last = i === edges.length - 2;
    const count = v.filter(x => x >= a && (last ? x <= b : x < b)).length;
    return { lo: a, hi: b, label: `${fmtNum(a)} – ${fmtNum(b)}`, color: colors[i], show: true, count };
  });
  return { mode: 'graduated', field, scheme, method, classes, other: { show: false, color: '#b4b4b4', label: 'No value', count: 0 } };
}

/** Class of every feature: 0..k-1 for the classes, k for “other”, -1 = not drawn. */
function classIndex(t: Table, cls: any): Int16Array {
  const classes: any[] = cls.classes || [];
  const k = classes.length;
  const out = new Int16Array(t.n).fill(cls.other?.show ? k : -1);
  const col = t.cols[cls.field];
  if (cls.mode === 'categories') {
    if (col.kind === 'cat') {
      const lookup = new Map(classes.map((c, i) => [String(c.value), i]));
      const per = col.cats.map(c => lookup.get(c) ?? -2);
      for (let i = 0; i < t.n; i++) { const p = col.codes[i] < 0 ? -2 : per[col.codes[i]]; if (p >= 0) out[i] = p; }
    } else {
      const lookup = new Map(classes.map((c, i) => [Number(c.value), i]));
      for (let i = 0; i < t.n; i++) { const p = lookup.get(col.values[i]); if (p !== undefined) out[i] = p; }
    }
  } else {
    const v = numericValues(t, cls.field);
    for (let i = 0; i < t.n; i++) {
      for (let j = 0; j < k; j++) {
        const c = classes[j], last = j === k - 1;
        if (v[i] >= c.lo && (last ? v[i] <= c.hi : v[i] < c.hi)) { out[i] = j; break; }
      }
    }
  }
  classes.forEach((c, j) => { if (c.show === false) for (let i = 0; i < t.n; i++) if (out[i] === j) out[i] = -1; });
  return out;
}

export function classColors(cls: any): string[] {
  return [...(cls.classes || []).map((c: any) => c.color), cls.other?.color || '#b4b4b4'];
}
