// GeoPackage (.gpkg) in the browser: list the feature tables and read one into a Table – the same
// as the desktop app's gpkg.py and geo.py (GeoPackage binary header + WKB geometry).

import { SqliteFile, SqlValue } from './sqlite';
import { AREA, LINE, Table, TableBuilder, Value } from './table';
import { GEOGRAPHIC, prjEpsg, toWgs84 } from './proj';

export interface GpkgLayer { table: string; geometryColumn: string; geometryType: string; srsId: number; count: number }

export function gpkgLayers(db: SqliteFile): GpkgLayer[] {
  if (!db.table('gpkg_geometry_columns')) throw new Error('This SQLite file isn’t a GeoPackage (it has no gpkg_geometry_columns table).');
  const out: GpkgLayer[] = [];
  for (const r of db.select('gpkg_geometry_columns')) {
    const table = String(r.table_name);
    if (!db.table(table)) continue;
    out.push({ table, geometryColumn: String(r.column_name), geometryType: String(r.geometry_type_name || ''), srsId: Number(r.srs_id ?? 0), count: db.count(table) });
  }
  return out;
}

/** EPSG code for a GeoPackage srs_id (through gpkg_spatial_ref_sys). */
function srsEpsg(db: SqliteFile, srsId: number): number {
  if (srsId === 4326 || srsId === 0 || srsId === -1) return 4326;
  let def = '';
  if (db.table('gpkg_spatial_ref_sys')) {
    for (const r of db.select('gpkg_spatial_ref_sys')) {
      if (Number(r.srs_id) !== srsId) continue;
      if (String(r.organization || '').toUpperCase() === 'EPSG' && r.organization_coordsys_id != null) {
        const e = Number(r.organization_coordsys_id);
        try { toWgs84(e); return e; } catch { /* not built in – try the definition */ }
      }
      def = String(r.definition || '');
    }
  }
  try { toWgs84(srsId); return srsId; } catch { /* fall through */ }
  if (def) return prjEpsg(def);
  throw new Error(`The coordinate system ${srsId} of this GeoPackage isn’t built in.`);
}

// ---------------------------------------------------------------- geometry
type Geom = { kind: 0 | 1 | 2; pts?: number[][]; rings?: number[][][] };

function readWkb(b: Uint8Array, off = 0): { geoms: Geom[]; end: number } {
  const dv = new DataView(b.buffer, b.byteOffset, b.byteLength);
  let p = off;
  const le = b[p] === 1; p += 1;
  let type = dv.getUint32(p, le); p += 4;
  let dims = 2;
  // EWKB flags (PostGIS) and ISO WKB Z/M/ZM type offsets
  if (type & 0x20000000) p += 4;                                  // SRID
  if (type & 0x80000000) dims++;
  if (type & 0x40000000) dims++;
  type &= 0x0fffffff;
  if (type >= 1000) { const t = Math.floor(type / 1000); dims = 2 + (t === 3 ? 2 : 1); type %= 1000; }
  const pt = (): number[] => { const x = dv.getFloat64(p, le), y = dv.getFloat64(p + 8, le); p += 8 * dims; return [x, y]; };
  const seq = (): number[][] => { const n = dv.getUint32(p, le); p += 4; const a: number[][] = []; for (let i = 0; i < n; i++) a.push(pt()); return a; };
  const geoms: Geom[] = [];
  switch (type) {
    case 1: { const q = pt(); if (Number.isFinite(q[0])) geoms.push({ kind: 0, pts: [q] }); break; }
    case 2: geoms.push({ kind: LINE as 1, rings: [seq()] }); break;
    case 3: { const n = dv.getUint32(p, le); p += 4; const rings = []; for (let i = 0; i < n; i++) rings.push(seq()); geoms.push({ kind: AREA as 2, rings }); break; }
    case 4: case 5: case 6: case 7: {
      const n = dv.getUint32(p, le); p += 4;
      const parts: Geom[] = [];
      for (let i = 0; i < n; i++) { const r = readWkb(b, p); parts.push(...r.geoms); p = r.end; }
      if (type === 4) { const pts = parts.flatMap(g => g.pts || []); if (pts.length) geoms.push({ kind: 0, pts }); }
      else if (type === 5) { const rings = parts.flatMap(g => g.rings || []); if (rings.length) geoms.push({ kind: 1, rings }); }
      else if (type === 6) { const rings = parts.flatMap(g => g.rings || []); if (rings.length) geoms.push({ kind: 2, rings }); }
      else geoms.push(...parts);
      break;
    }
    default: throw new Error(`Unsupported geometry type ${type}`);
  }
  return { geoms, end: p };
}

const ENV = [0, 32, 48, 48, 64];

/** GeoPackage geometry blob -> geometries (null for empty ones). */
export function gpkgGeom(blob: Uint8Array): Geom[] | null {
  if (blob.length < 8) return null;
  if (blob[0] === 0x47 && blob[1] === 0x50) {               // "GP"
    const flags = blob[3];
    if (flags & 0x10) return null;                          // empty geometry
    const env = ENV[(flags >> 1) & 7] ?? 0;
    return readWkb(blob, 8 + env).geoms;
  }
  return readWkb(blob, 0).geoms;                            // plain WKB (some writers)
}

// ---------------------------------------------------------------- reading a layer
export function readGpkgLayer(db: SqliteFile, layer: GpkgLayer): Table {
  const epsg = srsEpsg(db, layer.srsId);
  const f = GEOGRAPHIC.includes(epsg) ? null : toWgs84(epsg);
  const t = db.table(layer.table)!;
  const fields = t.columns.filter(c => c !== layer.geometryColumn);
  const b = new TableBuilder(fields);
  const tr = (p: number[]) => f ? f(p[0], p[1]) : [p[0], p[1]];
  for (const r of db.select(layer.table)) {
    const a: Record<string, Value> = {};
    for (const c of fields) {
      const v: SqlValue = r[c];
      a[c] = v instanceof Uint8Array ? null : v;
    }
    const blob = r[layer.geometryColumn];
    if (!(blob instanceof Uint8Array)) continue;
    let geoms: Geom[] | null;
    try { geoms = gpkgGeom(blob); } catch { continue; }
    if (!geoms?.length) continue;
    // a feature with several geometries (a collection) keeps its first kind
    const g = geoms[0];
    if (g.kind === 0) { const p = tr(g.pts![0]); b.addPoint(p[0], p[1], a); }
    else b.addShape(g.kind, geoms.filter(x => x.kind === g.kind).flatMap(x => x.rings!).map(rg => rg.map(tr)), a);
  }
  return b.finish();
}
