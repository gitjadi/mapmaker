// A small read-only SQLite reader – enough to read the tables of a GeoPackage in the browser
// without a database library. It walks the table b-trees page by page (following overflow pages
// for long values) and decodes records; indexes, WAL files and writing are not needed.

export type SqlValue = number | string | Uint8Array | null;

export interface SqlTable { name: string; rootpage: number; sql: string; columns: string[]; rowidColumn: number }

export class SqliteFile {
  private dv: DataView;
  pageSize: number;
  usable: number;
  private dec: TextDecoder;
  tables = new Map<string, SqlTable>();

  constructor(private buf: Uint8Array) {
    const magic = new TextDecoder().decode(buf.subarray(0, 15));
    if (magic !== 'SQLite format 3') throw new Error('This isn’t a GeoPackage (SQLite) file.');
    this.dv = new DataView(buf.buffer, buf.byteOffset, buf.byteLength);
    const ps = this.dv.getUint16(16, false);
    this.pageSize = ps === 1 ? 65536 : ps;
    this.usable = this.pageSize - buf[20];
    const enc = this.dv.getUint32(56, false);
    if (enc > 1) throw new Error('This GeoPackage uses a UTF-16 text encoding, which isn’t supported.');
    this.dec = new TextDecoder('utf-8');
    if (buf[18] === 2 || buf[19] === 2) {
      // WAL mode: the file itself is still readable if the WAL was checkpointed (normal for copied files)
    }
    for (const r of this.rows(1, 5)) {
      const [type, name, , rootpage, sql] = r.values as [string, string, string, number, string];
      if (type === 'table' && typeof rootpage === 'number' && rootpage > 0) {
        const { columns, rowidColumn } = parseColumns(String(sql || ''));
        this.tables.set(String(name).toLowerCase(), { name: String(name), rootpage, sql: String(sql || ''), columns, rowidColumn });
      }
    }
  }

  table(name: string) { return this.tables.get(name.toLowerCase()) || null; }

  /** All rows of a table as objects (column name -> value). */
  *select(name: string): Generator<Record<string, SqlValue>> {
    const t = this.table(name);
    if (!t) throw new Error(`The table “${name}” isn’t in the file.`);
    for (const r of this.rows(t.rootpage, t.columns.length)) {
      const o: Record<string, SqlValue> = {};
      t.columns.forEach((c, i) => { o[c] = i === t.rowidColumn ? r.rowid : (r.values[i] ?? null); });
      yield o;
    }
  }

  count(name: string): number {
    const t = this.table(name);
    if (!t) return 0;
    let n = 0;
    const stack = [t.rootpage];
    while (stack.length) {
      const pg = stack.pop()!;
      const base = (pg - 1) * this.pageSize, h = pg === 1 ? base + 100 : base;
      const type = this.buf[h], cells = this.dv.getUint16(h + 3, false);
      if (type === 0x0d) n += cells;
      else if (type === 0x05) {
        for (let i = 0; i < cells; i++) stack.push(this.dv.getUint32(base + this.dv.getUint16(h + 12 + i * 2, false), false));
        stack.push(this.dv.getUint32(h + 8, false));
      }
    }
    return n;
  }

  // ---------------------------------------------------------------- b-tree walking
  private *rows(root: number, ncols: number): Generator<{ rowid: number; values: SqlValue[] }> {
    // depth-first, left to right (rowid order)
    const stack: number[] = [root];
    while (stack.length) {
      const pg = stack.pop()!;
      const base = (pg - 1) * this.pageSize;
      const h = pg === 1 ? base + 100 : base;
      const type = this.buf[h];
      const cells = this.dv.getUint16(h + 3, false);
      if (type === 0x05) {                                   // interior table page
        const right = this.dv.getUint32(h + 8, false);
        stack.push(right);
        for (let i = cells - 1; i >= 0; i--) {
          const off = base + this.dv.getUint16(h + 12 + i * 2, false);
          stack.push(this.dv.getUint32(off, false));
        }
      } else if (type === 0x0d) {                            // leaf table page
        for (let i = 0; i < cells; i++) {
          let p = base + this.dv.getUint16(h + 8 + i * 2, false);
          const [size, n1] = this.varint(p); p += n1;
          const [rowid, n2] = this.varint(p); p += n2;
          const payload = this.payload(p, size);
          yield { rowid, values: this.record(payload, ncols) };
        }
      } else {
        throw new Error(`Unexpected page type ${type} – the GeoPackage may be damaged.`);
      }
    }
  }

  private varint(p: number): [number, number] {
    let v = 0;
    for (let i = 0; i < 8; i++) {
      const b = this.buf[p + i];
      v = v * 128 + (b & 0x7f);
      if (!(b & 0x80)) return [v, i + 1];
    }
    return [v * 256 + this.buf[p + 8], 9];
  }

  /** A cell's payload, gathered from its overflow pages when it doesn't fit on the page. */
  private payload(p: number, size: number): Uint8Array {
    const U = this.usable, X = U - 35;
    if (size <= X) return this.buf.subarray(p, p + size);
    const M = Math.floor((U - 12) * 32 / 255) - 23;
    const K = M + ((size - M) % (U - 4));
    const local = K <= X ? K : M;
    const out = new Uint8Array(size);
    out.set(this.buf.subarray(p, p + local), 0);
    let got = local;
    let next = this.dv.getUint32(p + local, false);
    while (got < size && next) {
      const base = (next - 1) * this.pageSize;
      const n = Math.min(U - 4, size - got);
      out.set(this.buf.subarray(base + 4, base + 4 + n), got);
      got += n;
      next = this.dv.getUint32(base, false);
    }
    return out;
  }

  private record(b: Uint8Array, ncols: number): SqlValue[] {
    const dv = new DataView(b.buffer, b.byteOffset, b.byteLength);
    const vint = (p: number): [number, number] => {
      let v = 0;
      for (let i = 0; i < 8; i++) { const x = b[p + i]; v = v * 128 + (x & 0x7f); if (!(x & 0x80)) return [v, i + 1]; }
      return [v * 256 + b[p + 8], 9];
    };
    const [hsize, hn] = vint(0);
    const types: number[] = [];
    for (let p = hn; p < hsize;) { const [t, n] = vint(p); types.push(t); p += n; }
    const out: SqlValue[] = [];
    let p = hsize;
    for (const t of types) {
      let v: SqlValue = null;
      switch (t) {
        case 0: v = null; break;
        case 1: v = dv.getInt8(p); p += 1; break;
        case 2: v = dv.getInt16(p, false); p += 2; break;
        case 3: v = (dv.getInt8(p) << 16) | (b[p + 1] << 8) | b[p + 2]; p += 3; break;
        case 4: v = dv.getInt32(p, false); p += 4; break;
        case 5: v = dv.getInt16(p, false) * 2 ** 32 + dv.getUint32(p + 2, false); p += 6; break;
        case 6: v = Number(dv.getBigInt64(p, false)); p += 8; break;
        case 7: v = dv.getFloat64(p, false); p += 8; break;
        case 8: v = 0; break;
        case 9: v = 1; break;
        default:
          if (t >= 12 && t % 2 === 0) { const n = (t - 12) / 2; v = b.subarray(p, p + n); p += n; }
          else if (t >= 13) { const n = (t - 13) / 2; v = this.dec.decode(b.subarray(p, p + n)); p += n; }
      }
      out.push(v);
    }
    while (out.length < ncols) out.push(null);      // columns added later (ALTER TABLE) default to NULL
    return out;
  }
}

/** Column names from CREATE TABLE, and which one (if any) is the rowid alias (INTEGER PRIMARY KEY). */
function parseColumns(sql: string): { columns: string[]; rowidColumn: number } {
  const open = sql.indexOf('(');
  const close = sql.lastIndexOf(')');
  if (open < 0 || close < open) return { columns: [], rowidColumn: -1 };
  const body = sql.slice(open + 1, close);
  const parts: string[] = [];
  let depth = 0, cur = '', q: string | null = null;
  for (const ch of body) {
    if (q) { cur += ch; if (ch === q) q = null; continue; }
    if (ch === '"' || ch === "'" || ch === '`' || ch === '[') { q = ch === '[' ? ']' : ch; cur += ch; continue; }
    if (ch === '(') depth++;
    if (ch === ')') depth--;
    if (ch === ',' && depth === 0) { parts.push(cur); cur = ''; continue; }
    cur += ch;
  }
  if (cur.trim()) parts.push(cur);
  const columns: string[] = [];
  let rowidColumn = -1;
  for (const raw of parts) {
    const s = raw.trim();
    if (/^(CONSTRAINT|PRIMARY\s+KEY|UNIQUE|CHECK|FOREIGN\s+KEY)\b/i.test(s)) continue;
    const m = /^("([^"]|"")+"|`[^`]+`|\[[^\]]+\]|'[^']+'|\S+)\s*(.*)$/s.exec(s);
    if (!m) continue;
    let name = m[1];
    if (/^["`\['].*["`\]']$/.test(name)) name = name.slice(1, -1).replace(/""/g, '"');
    const rest = m[3] || '';
    if (/^INTEGER\b/i.test(rest) && /PRIMARY\s+KEY/i.test(rest) && !/\bDESC\b/i.test(rest)) rowidColumn = columns.length;
    columns.push(name);
  }
  return { columns, rowidColumn };
}
