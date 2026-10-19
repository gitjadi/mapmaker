// Reading data files in the browser: GeoJSON, CSV, KML/KMZ, GPX and shapefiles (a .zip, or the
// .shp/.dbf/.prj files picked together). Coordinates in SWEREF 99 TM, its local zones, RT 90 or
// Web Mercator are converted to lon/lat, as in the desktop app's sources.py.

import { AREA, LINE, Table, TableBuilder, Value } from './table';
import { guessProjected, median, prjEpsg, toWgs84 } from './proj';
import { readZip } from './zip';
import { SqliteFile } from './sqlite';
import { GpkgLayer, gpkgLayers, readGpkgLayer } from './gpkg';

export const DATA_ACCEPT = '.gpkg,.geojson,.json,.csv,.txt,.tsv,.kml,.kmz,.gpx,.zip,.shp,.dbf,.prj,.cpg';

export interface Imported { name: string; table: Table; geometry: string; fields: [string, string][]; note?: string }

type Feat = { kind: 0 | 1 | 2; pt?: [number, number]; rings?: number[][][]; a: Record<string, Value> };

const LAT = ['lat', 'latitude', 'latitud', 'y_wgs84', 'wgs84_lat', 'lat_wgs84'];
const LON = ['lon', 'lng', 'long', 'longitude', 'longitud', 'x_wgs84', 'wgs84_lon', 'lon_wgs84'];
const NORTH = ['n', 'nord', 'north', 'northing', 'y', 'sweref99n', 'sweref99_n', 'sweref99nord', 'n_sweref99', 'y_sweref', 'northing_sweref', 'sweref_n', 'koordinat_n'];
const EAST = ['e', 'ost', 'öst', 'east', 'easting', 'x', 'sweref99e', 'sweref99_e', 'sweref99o', 'sweref99ost', 'e_sweref99', 'x_sweref', 'sweref_e', 'koordinat_e', 'o'];

const stem = (n: string) => n.replace(/^.*[\\/]/, '').replace(/\.[^.]+$/, '');
const ext = (n: string) => (/\.([^.]+)$/.exec(n)?.[1] || '').toLowerCase();

export function num(v: unknown): number | null {
  if (v === null || v === undefined) return null;
  if (typeof v === 'number') return Number.isFinite(v) ? v : null;
  let s = String(v).trim().replace(/[\s  ]/g, '');
  if (!s) return null;
  if ((s.match(/,/g) || []).length === 1 && !s.includes('.')) s = s.replace(',', '.');
  if (!/^[-+]?(\d+\.?\d*|\.\d+)([eE][-+]?\d+)?$/.test(s)) return null;
  return Number(s);
}

function typed(v: string): Value {
  const n = num(v);
  if (n === null) return v === '' ? null : v;
  return n;
}

function decodeText(buf: Uint8Array): string {
  try { return new TextDecoder('utf-8', { fatal: true }).decode(buf).replace(/^﻿/, ''); }
  catch { return new TextDecoder('windows-1252').decode(buf); }
}

/** Turn raw features into a table, converting from a projected system if the coordinates are metres. */
function build(feats: Feat[], epsg: number | null, fields: string[] = []): Table {
  if (epsg === null) {
    const sample = feats.slice(0, 300).map(f => f.pt || f.rings?.[0]?.[0]).filter(Boolean) as number[][];
    const mx = median(sample.map(p => p[0])), my = median(sample.map(p => p[1]));
    epsg = Math.abs(my) > 1000 || Math.abs(mx) > 1000 ? guessProjected(mx, my) : 4326;
  }
  const f = toWgs84(epsg);
  const b = new TableBuilder(fields);
  for (const ft of feats) {
    if (ft.kind === 0 && ft.pt) { const [lo, la] = f(ft.pt[0], ft.pt[1]); b.addPoint(lo, la, ft.a); }
    else if (ft.rings) b.addShape(ft.kind, ft.rings.map(r => r.map(p => f(p[0], p[1]))), ft.a);
  }
  return b.finish();
}

function finish(name: string, t: Table, note?: string): Imported {
  if (!t.n) throw new Error(`“${name}” has no features with coordinates.`);
  const fields: [string, string][] = t.fields.map(f => {
    const c = t.cols[f];
    return [f, c.kind === 'num' ? (c.isInt ? 'INTEGER' : 'REAL') : 'TEXT'];
  });
  return { name, table: t, geometry: t.mainKind(), fields, note };
}

// ---------------------------------------------------------------- GeoJSON
function gjFeats(g: any, a: Record<string, Value>, out: Feat[]) {
  if (!g) return;
  const c = g.coordinates;
  switch (g.type) {
    case 'Point': out.push({ kind: 0, pt: [c[0], c[1]], a }); break;
    case 'MultiPoint': for (const p of c) out.push({ kind: 0, pt: [p[0], p[1]], a }); break;
    case 'LineString': out.push({ kind: LINE, rings: [c], a }); break;
    case 'MultiLineString': out.push({ kind: LINE, rings: c, a }); break;
    case 'Polygon': out.push({ kind: AREA, rings: c, a }); break;
    case 'MultiPolygon': out.push({ kind: AREA, rings: c.flat(1), a }); break;
    case 'GeometryCollection': for (const gg of g.geometries || []) gjFeats(gg, a, out); break;
  }
}

export function readGeoJSON(text: string, name: string): Imported {
  const d = JSON.parse(text);
  const feats: any[] = d.type === 'FeatureCollection' ? d.features : d.type === 'Feature' ? [d] : [{ geometry: d, properties: {} }];
  const out: Feat[] = [];
  for (const f of feats) {
    const a: Record<string, Value> = {};
    for (const [k, v] of Object.entries(f.properties || {})) a[k] = v === null || typeof v !== 'object' ? v as Value : JSON.stringify(v);
    gjFeats(f.geometry, a, out);
  }
  const crs = JSON.stringify(d.crs || {});
  const m = /EPSG:{1,2}(\d+)/.exec(crs);
  return finish(name, build(out, m ? Number(m[1]) : null));
}

// ---------------------------------------------------------------- CSV
export function parseCsv(text: string): string[][] {
  const sample = text.slice(0, 20000);
  const firstLine = sample.split(/\r?\n/)[0];
  const delims = [',', ';', '\t', '|'];
  const delim = delims.map(d => [d, firstLine.split(d).length] as [string, number]).sort((a, b) => b[1] - a[1])[0][0];
  const rows: string[][] = [];
  let row: string[] = [], field = '', q = false;
  for (let i = 0; i < text.length; i++) {
    const ch = text[i];
    if (q) {
      if (ch === '"') { if (text[i + 1] === '"') { field += '"'; i++; } else q = false; }
      else field += ch;
    } else if (ch === '"' && field === '') q = true;
    else if (ch === delim) { row.push(field); field = ''; }
    else if (ch === '\n' || ch === '\r') {
      if (ch === '\r' && text[i + 1] === '\n') i++;
      row.push(field); field = '';
      rows.push(row); row = [];
    } else field += ch;
  }
  if (field || row.length) { row.push(field); rows.push(row); }
  return rows.filter(r => r.some(c => c.trim()));
}

export function readCsv(text: string, name: string): Imported {
  const rows = parseCsv(text);
  if (rows.length < 2) throw new Error('The file has no rows.');
  const head = rows[0].map((h, i) => h.trim() || `col${i + 1}`);
  const low = head.map(h => h.toLowerCase().replace(/[ ]/g, '').replace(/-/g, '_'));
  const find = (names: string[]) => { const i = low.findIndex(h => names.includes(h)); return i < 0 ? null : i; };
  let ilat = find(LAT), ilon = find(LON);
  let epsg: number | null = 4326;
  if (ilat === null || ilon === null) { ilat = find(NORTH); ilon = find(EAST); epsg = null; }
  if (ilat === null || ilon === null) throw new Error('Couldn’t find coordinate columns. Name them lat/lon (WGS 84) or N/E (SWEREF 99 TM).');
  const feats: Feat[] = [];
  for (const r of rows.slice(1)) {
    const y = num(r[ilat]), x = num(r[ilon]);
    if (x === null || y === null) continue;
    const a: Record<string, Value> = {};
    head.forEach((h, i) => { a[h] = typed((r[i] ?? '').trim()); });
    feats.push({ kind: 0, pt: [x, y], a });
  }
  if (epsg === 4326 && feats.length && Math.abs(median(feats.slice(0, 300).map(f => f.pt![1]))) > 1000) epsg = null;
  return finish(name, build(feats, epsg, head));
}

// ---------------------------------------------------------------- KML and GPX
const xml = (text: string) => {
  const d = new DOMParser().parseFromString(text, 'application/xml');
  if (d.querySelector('parsererror')) throw new Error('The file isn’t valid XML.');
  return d;
};
const byTag = (el: Element | Document, tag: string) => [...el.getElementsByTagNameNS('*', tag)];
const childText = (el: Element, tag: string) => (byTag(el, tag)[0]?.textContent || '').trim();

function kmlCoords(text: string | null | undefined): number[][] {
  return (text || '').trim().split(/\s+/).map(s => s.split(',').map(Number)).filter(p => p.length >= 2 && Number.isFinite(p[0]) && Number.isFinite(p[1])).map(p => [p[0], p[1]]);
}

export function readKml(text: string, name: string): Imported {
  const d = xml(text);
  const feats: Feat[] = [];
  for (const pm of byTag(d, 'Placemark')) {
    const a: Record<string, Value> = { name: childText(pm, 'name') };
    const desc = childText(pm, 'description');
    if (desc) a.description = desc.replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ').trim().slice(0, 2000);
    for (const dd of byTag(pm, 'Data')) a[dd.getAttribute('name') || 'data'] = typed(childText(dd, 'value'));
    for (const sd of byTag(pm, 'SimpleData')) a[sd.getAttribute('name') || 'data'] = typed((sd.textContent || '').trim());
    const polys = byTag(pm, 'Polygon').flatMap(pg => byTag(pg, 'LinearRing').map(r => kmlCoords(byTag(r, 'coordinates')[0]?.textContent)));
    const lines = byTag(pm, 'LineString').map(l => kmlCoords(byTag(l, 'coordinates')[0]?.textContent));
    const pts = byTag(pm, 'Point').map(p => kmlCoords(byTag(p, 'coordinates')[0]?.textContent)[0]).filter(Boolean);
    if (polys.length) feats.push({ kind: AREA, rings: polys, a });
    else if (lines.length) feats.push({ kind: LINE, rings: lines, a });
    else for (const p of pts) feats.push({ kind: 0, pt: [p[0], p[1]], a });
  }
  return finish(name, build(feats, 4326));
}

export function readGpx(text: string, name: string): Imported {
  const d = xml(text);
  const feats: Feat[] = [];
  for (const w of byTag(d, 'wpt')) {
    const a: Record<string, Value> = {};
    for (const k of ['name', 'desc', 'cmt', 'time', 'sym', 'type']) { const v = childText(w, k); if (v) a[k] = v; }
    const ele = num(childText(w, 'ele'));
    if (ele !== null) a.ele = ele;
    feats.push({ kind: 0, pt: [Number(w.getAttribute('lon')), Number(w.getAttribute('lat'))], a });
  }
  for (const [kind, ptTag] of [['trk', 'trkpt'], ['rte', 'rtept']]) {
    for (const t of byTag(d, kind)) {
      const segs = (kind === 'trk' ? byTag(t, 'trkseg') : [t]).map(s => byTag(s, ptTag).map(q => [Number(q.getAttribute('lon')), Number(q.getAttribute('lat'))]));
      const ok = segs.filter(s => s.length >= 2);
      if (ok.length) feats.push({ kind: LINE, rings: ok, a: { name: childText(t, 'name') || kind, kind } });
    }
  }
  return finish(name, build(feats, 4326));
}

// ---------------------------------------------------------------- shapefiles
function readDbf(data: Uint8Array | undefined, cpg?: string): { fields: string[]; rows: (Record<string, Value> | null)[] } {
  if (!data) return { fields: [], rows: [] };
  const dv = new DataView(data.buffer, data.byteOffset, data.byteLength);
  const n = dv.getUint32(4, true), hlen = dv.getUint16(8, true), rlen = dv.getUint16(10, true);
  const defs: { name: string; type: string; len: number; dec: number }[] = [];
  const latin = new TextDecoder('windows-1252');
  for (let p = 32; data[p] !== 0x0d && p < hlen; p += 32) {
    const raw = data.subarray(p, p + 11);
    const z = raw.indexOf(0);
    defs.push({ name: latin.decode(z >= 0 ? raw.subarray(0, z) : raw), type: String.fromCharCode(data[p + 11]), len: data[p + 16], dec: data[p + 17] });
  }
  const utf8 = !cpg || /utf-?8/i.test(cpg);
  const dec = (b: Uint8Array) => { if (utf8) { try { return new TextDecoder('utf-8', { fatal: true }).decode(b); } catch { /* fall through */ } } return latin.decode(b); };
  const rows: (Record<string, Value> | null)[] = [];
  for (let i = 0; i < n; i++) {
    const off = hlen + i * rlen;
    if (off >= data.length || data[off] === 0x2a) { rows.push(null); continue; }
    let p = off + 1;
    const row: Record<string, Value> = {};
    for (const f of defs) {
      const s = dec(data.subarray(p, p + f.len)).trim();
      p += f.len;
      if (f.type === 'N' || f.type === 'F') row[f.name] = num(s);
      else if (f.type === 'L') row[f.name] = s ? /^[YT]/i.test(s) : null;
      else row[f.name] = s || null;
    }
    rows.push(row);
  }
  return { fields: defs.map(d => d.name), rows };
}

function readShp(shp: Uint8Array, dbf: Uint8Array | undefined, prj: string | null, cpg: string | undefined, name: string): Imported {
  const { fields, rows } = readDbf(dbf, cpg);
  let epsg = prjEpsg(prj);
  const dv = new DataView(shp.buffer, shp.byteOffset, shp.byteLength);
  const feats: Feat[] = [];
  let pos = 100, k = 0;
  while (pos + 8 <= shp.length) {
    const clen = dv.getInt32(pos + 4, false) * 2;
    const b = pos + 8;
    pos += 8 + clen;
    const a = rows[k++] || {};
    if (clen < 4) continue;
    const t = dv.getInt32(b, true);
    const base = t < 30 ? t % 10 : t;
    if (base === 1) feats.push({ kind: 0, pt: [dv.getFloat64(b + 4, true), dv.getFloat64(b + 12, true)], a });
    else if (base === 8) {
      const np = dv.getInt32(b + 36, true);
      for (let i = 0; i < np; i++) feats.push({ kind: 0, pt: [dv.getFloat64(b + 40 + i * 16, true), dv.getFloat64(b + 48 + i * 16, true)], a });
    } else if (base === 3 || base === 5) {
      const nparts = dv.getInt32(b + 36, true), npts = dv.getInt32(b + 40, true);
      const parts: number[] = [];
      for (let i = 0; i < nparts; i++) parts.push(dv.getInt32(b + 44 + i * 4, true));
      parts.push(npts);
      const p0 = b + 44 + nparts * 4;
      const rings: number[][][] = [];
      for (let i = 0; i < nparts; i++) {
        const r: number[][] = [];
        for (let j = parts[i]; j < parts[i + 1]; j++) r.push([dv.getFloat64(p0 + j * 16, true), dv.getFloat64(p0 + j * 16 + 8, true)]);
        rings.push(r);
      }
      feats.push({ kind: base === 3 ? LINE : AREA, rings, a });
    }
  }
  const first = feats[0]?.pt || feats[0]?.rings?.[0]?.[0];
  if (!prj && first && Math.abs(first[1]) < 1000) epsg = 4326;      // no .prj and degrees: WGS 84
  return finish(name, build(feats, epsg, fields));
}

// ---------------------------------------------------------------- any files
type Src = { name: string; data: Uint8Array };

/** Read the data files picked or dropped (several at once is fine; a shapefile's parts belong together). */
export type ChooseLayers = (file: string, layers: GpkgLayer[]) => Promise<GpkgLayer[] | null>;

export async function readDataFiles(files: File[], choose?: ChooseLayers): Promise<Imported[]> {
  const srcs: Src[] = await Promise.all(files.map(async f => ({ name: f.name, data: new Uint8Array(await f.arrayBuffer()) })));
  return readSources(srcs, choose);
}

export async function readSources(srcs: Src[], choose?: ChooseLayers): Promise<Imported[]> {
  const out: Imported[] = [];
  const byStem = (list: Src[], s: string, e: string) => list.find(x => stem(x.name).toLowerCase() === s.toLowerCase() && ext(x.name) === e);
  for (const s of srcs) {
    const e = ext(s.name), nm = stem(s.name);
    if (['dbf', 'prj', 'cpg', 'shx', 'sbn', 'sbx', 'qix', 'xml'].includes(e)) continue;
    if (e === 'geojson' || e === 'json') out.push(readGeoJSON(decodeText(s.data), nm));
    else if (e === 'csv' || e === 'txt' || e === 'tsv') out.push(readCsv(decodeText(s.data), nm));
    else if (e === 'kml') out.push(readKml(decodeText(s.data), nm));
    else if (e === 'gpx') out.push(readGpx(decodeText(s.data), nm));
    else if (e === 'shp') {
      const dbf = byStem(srcs, nm, 'dbf'), prj = byStem(srcs, nm, 'prj'), cpg = byStem(srcs, nm, 'cpg');
      out.push(readShp(s.data, dbf?.data, prj ? decodeText(prj.data) : null, cpg ? decodeText(cpg.data) : undefined, nm));
    } else if (e === 'kmz' || e === 'zip') {
      const entries = (await readZip(s.data)).map(z => ({ name: z.name, data: z.data }));
      const kml = entries.find(z => ext(z.name) === 'kml');
      if (e === 'kmz' || (kml && !entries.some(z => ext(z.name) === 'shp'))) {
        if (!kml) throw new Error('The KMZ file has no KML inside.');
        out.push(readKml(decodeText(kml.data), nm));
        continue;
      }
      const inner = await readSources(entries.filter(z => !z.name.endsWith('/')), choose);
      if (!inner.length) throw new Error(`“${s.name}” has no data files MapMaker can read.`);
      out.push(...inner);
    } else if (e === 'gpkg') {
      const db = new SqliteFile(s.data);
      const layers = gpkgLayers(db);
      if (!layers.length) throw new Error(`“${s.name}” has no feature tables.`);
      const picked = layers.length > 1 && choose ? await choose(s.name, layers) : layers;
      for (const L of picked || []) {
        await new Promise(r => setTimeout(r, 0));          // let the page show progress between tables
        out.push(finish(layers.length > 1 ? L.table : nm, readGpkgLayer(db, L)));
      }
    } else {
      throw new Error(`MapMaker can’t read .${e} files.`);
    }
  }
  return out;
}
