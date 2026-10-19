// The map document – the same JSON as the desktop app's document.py, so .mapmaker files open in both.
// A stack of layers (bottom to top), the page, the view; undo is a list of JSON snapshots.

import { AREA_STYLE, DARK2, DATA_STYLE, LINE_STYLE, TEXT_BASE, TEXT_PRESETS } from './shared/styles';
import { Table } from './table';
import { BASEMAPS } from './shared/basemaps';
import { SYMBOLS } from './shared/symbols';
import { readZip, writeZip, ZipEntry } from './zip';

export const SUFFIX = '.mapmaker';
export const FORMAT = 2;
export const SWEDEN_VIEW: [number, number, number] = [0.5444, 0.2770, 4.6];
export const APP_VERSION = 'MapMaker web 0.1';

export type Layer = Record<string, any> & {
  id: string; kind: string; name: string; visible: boolean; locked: boolean; opacity: number; blend: string;
};
export type Item = Record<string, any> & { id: string; type: string; hidden: boolean };

export const BLENDS: Record<string, string> = {
  normal: 'Normal', multiply: 'Multiply', screen: 'Screen', overlay: 'Overlay', darken: 'Darken',
};
export const RASTER_KINDS = ['color', 'basemap', 'terrain', 'service'];
export const KIND_LABELS: Record<string, string> = {
  color: 'Plain colour', basemap: 'Background map', terrain: 'Terrain', service: 'Map service',
  data: 'Data', annotations: 'Labels & symbols', grid: 'Coordinate grid',
};
/** Layer kinds the browser version can draw so far (others are kept and saved untouched). */
export const WEB_KINDS = ['color', 'basemap', 'service', 'annotations', 'data'];

export const newId = () => crypto.getRandomValues(new Uint8Array(5)).reduce((s, b) => s + b.toString(16).padStart(2, '0'), '');
const clone = <T>(x: T): T => JSON.parse(JSON.stringify(x));

// ---------------------------------------------------------------- page
export function defaultPage(): Record<string, any> {
  return {
    show: true, paper: 'A4', landscape: true, margin_mm: 12, background: '#ffffff',
    title: '', subtitle: '', title_align: 'left',
    title_style: { font: 'Georgia', size: 22, bold: true, color: '#1d1d1d' },
    neatline: true,
    legend: { show: true, title: '', pos: [0.985, 0.975], anchor: 'br' },
    scalebar: { show: true, pos: [0.015, 0.975], anchor: 'bl', units: 'metric', style: 'bar' },
    north: { show: true, pos: [0.985, 0.025], anchor: 'tr' },
    credits: { show: true, text: '', auto: true },
    design: {},
    images: [],
  };
}

export function mergePage(saved: Record<string, any> | undefined) {
  const page = defaultPage();
  for (const [k, v] of Object.entries(saved || {})) {
    page[k] = page[k] && typeof page[k] === 'object' && !Array.isArray(page[k]) && v && typeof v === 'object' && !Array.isArray(v)
      ? { ...page[k], ...v } : v;
  }
  return page;
}

// ---------------------------------------------------------------- text styles
export function textStyle(preset = 'place'): Record<string, any> {
  return { ...clone(TEXT_BASE), ...clone((TEXT_PRESETS[preset] || TEXT_PRESETS.place).style) };
}

// ---------------------------------------------------------------- layers
function layer(kind: string, name: string, kw: Record<string, any> = {}): Layer {
  return { id: newId(), kind, name, visible: true, locked: false, opacity: 1, blend: 'normal', ...kw };
}
export const colorLayer = (color = '#f4f1ea', name = 'Background colour') => layer('color', name, { color });
export function basemapLayer(source = 'esri-light', name?: string) {
  const src = BASEMAPS.find(b => b.id === source);
  return layer('basemap', name || (src ? src.name : source), { source, labels: false });
}
export const serviceLayer = (source: Record<string, any>) => layer('service', source.name || 'Map service', { opacity: 0.85, source });
export const annotationLayer = (name = 'Labels & symbols') => layer('annotations', name, { items: [] });

let nextColor = 0;
export function defaultDataStyle(color?: string): Record<string, any> {
  const c = color || DARK2[nextColor++ % DARK2.length];
  return { ...clone(DATA_STYLE), color: c, line_color: c, fill: c, stroke: c };
}
export function dataLayer(name: string, geometry: string, fields: [string, string][], source: Record<string, any>, table: string) {
  return layer('data', name, {
    table, geometry, fields, source, style: defaultDataStyle(),
    label: { field: null, style: textStyle('feature') }, legend: true, count: 0,
  });
}

// ---------------------------------------------------------------- annotation items
function item(type: string, kw: Record<string, any>): Item {
  return { id: newId(), type, hidden: false, legend: type === 'symbol', legend_text: '', ...kw };
}
export const textItem = (lon: number, lat: number, text: string, preset = 'place') =>
  item('text', { lon, lat, text, preset, style: textStyle(preset), legend: false });
export const symbolItem = (lon: number, lat: number, symbol = 'pin', color = '#b03a2e', size = 22, badge = false) =>
  item('symbol', { lon, lat, symbol, color, size, badge, label: '', label_style: textStyle('place') });
export function lineItem(coords: number[][], color = '#b03a2e', width = 2.5, dash = 'solid') {
  return item('line', { coords: clone(coords), style: { ...clone(LINE_STYLE), color, width, dash } });
}
export function areaItem(coords: number[][], fill = '#b03a2e', stroke?: string) {
  const style: Record<string, any> = { ...clone(AREA_STYLE), fill };
  if (stroke) style.stroke = stroke;
  return item('area', { coords: clone(coords), style });
}

export function itemTitle(it: Item): string {
  if (it.type === 'text') return (it.text || 'Text').replace(/\n/g, ' ').slice(0, 40);
  if (it.type === 'symbol') return (it.label || it.legend_text || SYMBOLS[it.symbol]?.label || 'Symbol').slice(0, 40);
  if (it.legend_text) return it.legend_text.slice(0, 40);
  return ({ line: 'Line', area: 'Area' } as Record<string, string>)[it.type] || it.type || 'Item';
}

export function moveItem(it: Item, dlon: number, dlat: number) {
  if ('lon' in it) { it.lon += dlon; it.lat += dlat; }
  for (const p of it.coords || []) { p[0] += dlon; p[1] += dlat; }
}

// ---------------------------------------------------------------- the document
interface UndoStep { label: string; state: string; merge: string | null; time: number }

export class MapDocument {
  title = 'Untitled map';
  layers: Layer[] = [];
  page = defaultPage();
  view: [number, number, number] = [...SWEDEN_VIEW];
  /** Imported features of data layers, by table id (tables never change once made). */
  tables = new Map<string, Table>();
  /** Files from a .mapmaker the browser doesn't use yet (pictures on the page) – kept and saved back. */
  extra: ZipEntry[] = [];
  fileName: string | null = null;
  dirty = false;
  revision = 0;
  private undoStack: UndoStep[] = [];
  private redoStack: { label: string; state: string }[] = [];
  onChange: (() => void) | null = null;

  static create(): MapDocument {
    const d = new MapDocument();
    d.layers = [basemapLayer('esri-light'), annotationLayer()];
    return d;
  }

  state(): string { return JSON.stringify({ title: this.title, layers: this.layers, page: this.page }); }

  private apply(st: string) {
    const d = JSON.parse(st);
    this.title = d.title; this.layers = d.layers; this.page = d.page;
    this.changed();
  }

  changed() { this.revision++; this.onChange?.(); }

  /** Call before a change. Changes with the same `merge` key under 1.5 s apart share one undo step. */
  checkpoint(label = '', merge: string | null = null) {
    const now = Date.now();
    const last = this.undoStack[this.undoStack.length - 1];
    if (merge !== null && last && last.merge === merge && now - last.time < 1500 && !this.redoStack.length) {
      last.time = now;
    } else {
      this.undoStack.push({ label, state: this.state(), merge, time: now });
      if (this.undoStack.length > 200) this.undoStack.shift();
    }
    this.redoStack = [];
    this.dirty = true;
  }

  /** Record an undo step from a state taken before a finished change (a drag). */
  pushUndo(label: string, state: string) {
    if (state === this.state()) return;
    this.undoStack.push({ label, state, merge: null, time: 0 });
    if (this.undoStack.length > 200) this.undoStack.shift();
    this.redoStack = [];
    this.dirty = true;
    this.changed();
  }

  canUndo() { return this.undoStack.length > 0; }
  canRedo() { return this.redoStack.length > 0; }
  undoLabel() { return this.undoStack[this.undoStack.length - 1]?.label || ''; }
  redoLabel() { return this.redoStack[this.redoStack.length - 1]?.label || ''; }

  undo(): string | null {
    const s = this.undoStack.pop();
    if (!s) return null;
    this.redoStack.push({ label: s.label, state: this.state() });
    this.dirty = true;
    this.apply(s.state);
    return s.label;
  }

  redo(): string | null {
    const s = this.redoStack.pop();
    if (!s) return null;
    this.undoStack.push({ label: s.label, state: this.state(), merge: null, time: 0 });
    this.dirty = true;
    this.apply(s.state);
    return s.label;
  }

  // -- layers
  layer(id: string | null | undefined) { return this.layers.find(L => L.id === id) || null; }
  index(id: string) { return this.layers.findIndex(L => L.id === id); }

  addLayer(L: Layer, above?: string | null) {
    const i = above ? this.index(above) : -1;
    if (i < 0) this.layers.push(L); else this.layers.splice(i + 1, 0, L);
    return L;
  }

  /** Background layers go just above the other backgrounds (below data and annotations). */
  addBackground(L: Layer) {
    if (L.kind === 'color') { this.layers.unshift(L); return L; }
    let i = 0;
    this.layers.forEach((x, j) => { if (RASTER_KINDS.includes(x.kind)) i = j + 1; });
    this.layers.splice(i, 0, L);
    return L;
  }

  removeLayer(id: string) { this.layers = this.layers.filter(L => L.id !== id); }

  moveLayer(id: string, newIndex: number) {
    const i = this.index(id);
    if (i < 0) return;
    const [L] = this.layers.splice(i, 1);
    this.layers.splice(Math.max(0, Math.min(this.layers.length, newIndex)), 0, L);
  }

  duplicateLayer(id: string) {
    const L = this.layer(id);
    if (!L) return null;
    const c = clone(L);
    c.id = newId();
    c.name = L.name + ' copy';
    for (const it of c.items || []) it.id = newId();
    return this.addLayer(c, id);
  }

  findItem(itemId: string | null | undefined): [Layer | null, Item | null] {
    for (const L of this.layers) for (const it of L.items || []) if (it.id === itemId) return [L, it];
    return [null, null];
  }

  removeItem(itemId: string) {
    for (const L of this.layers) if (L.items) L.items = L.items.filter((it: Item) => it.id !== itemId);
  }

  moveItemTo(itemId: string, layerId: string, index: number) {
    const [src, it] = this.findItem(itemId);
    const dst = this.layer(layerId);
    if (!src || !it || !dst || dst.kind !== 'annotations') return;
    src.items = src.items.filter((x: Item) => x.id !== itemId);
    dst.items.splice(Math.max(0, Math.min(dst.items.length, index)), 0, it);
  }

  annotationLayers() { return this.layers.filter(L => L.kind === 'annotations'); }

  uniqueName(name: string) {
    const names = new Set(this.layers.map(L => L.name));
    if (!names.has(name)) return name;
    let i = 2;
    while (names.has(`${name} ${i}`)) i++;
    return `${name} ${i}`;
  }

  // -- files
  body() {
    const now = new Date();
    const pad = (n: number) => String(n).padStart(2, '0');
    return {
      format: FORMAT, app: APP_VERSION,
      saved: `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())} ${pad(now.getHours())}:${pad(now.getMinutes())}`,
      title: this.title, layers: this.layers, page: this.page, view: this.view,
    };
  }

  addTable(t: Table): string { const id = newId(); this.tables.set(id, t); return id; }
  table(L: Layer | null | undefined): Table | null { return L?.table ? this.tables.get(L.table) || null : null; }

  /** The .npy files of the tables in use (and nothing for tables no layer shows any more). */
  tableEntries(): ZipEntry[] {
    const used = new Set(this.layers.filter(L => L.kind === 'data').map(L => L.table));
    const out: ZipEntry[] = [];
    for (const [id, t] of this.tables) if (used.has(id)) out.push(...t.toEntries(`data/${id}`));
    return out;
  }

  async toBlob(): Promise<Blob> {
    const json = new TextEncoder().encode(JSON.stringify(this.body(), null, 1));
    return writeZip([{ name: 'document.json', data: json }, ...this.tableEntries(), ...this.extra]);
  }

  /** Split loaded zip entries into tables (data/<id>/...) and the rest. */
  takeTables(entries: ZipEntry[]) {
    const groups = new Map<string, Map<string, Uint8Array>>();
    const rest: ZipEntry[] = [];
    for (const e of entries) {
      const m = /^data\/([^/]+)\/(.+)$/.exec(e.name);
      if (m) { if (!groups.has(m[1])) groups.set(m[1], new Map()); groups.get(m[1])!.set(m[2], e.data); }
      else rest.push(e);
    }
    for (const [id, files] of groups) {
      try { this.tables.set(id, Table.fromEntries(n => files.get(n))); }
      catch (err) {
        console.warn('Could not read table', id, err);
        for (const [n, d] of files) rest.push({ name: `data/${id}/${n}`, data: d });     // keep it as it was
      }
    }
    this.extra = rest;
  }

  static fromBody(body: any, extra: ZipEntry[] = []): MapDocument {
    if (Number(body.format || 1) > FORMAT) throw new Error('This map was saved by a newer MapMaker.');
    const d = new MapDocument();
    d.title = body.title || 'Untitled map';
    d.layers = body.layers || [];
    d.page = mergePage(body.page);
    if (Array.isArray(body.view) && body.view.length === 3) d.view = body.view;
    d.takeTables(extra);
    return d;
  }

  static async fromFile(file: Blob, name = ''): Promise<MapDocument> {
    const buf = new Uint8Array(await file.arrayBuffer());
    let d: MapDocument;
    if (buf[0] === 0x50 && buf[1] === 0x4b) {             // "PK" – a zip (.mapmaker)
      const entries = await readZip(buf);
      const docEntry = entries.find(e => e.name === 'document.json');
      if (!docEntry) throw new Error('This file has no document.json – is it a MapMaker map?');
      d = MapDocument.fromBody(JSON.parse(new TextDecoder().decode(docEntry.data)), entries.filter(e => e !== docEntry));
    } else {
      d = MapDocument.fromBody(JSON.parse(new TextDecoder().decode(buf)));
    }
    d.fileName = name || null;
    if (name && d.title === 'Untitled map') d.title = name.replace(/\.[^.]+$/, '');
    return d;
  }
}
