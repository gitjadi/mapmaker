"""Imported data, stored by column so that hundreds of thousands of features stay fast and small.

A Table holds
* one representative position per feature (lon/lat; the point itself for points),
* a geometry kind per feature (0 point, 1 line, 2 area),
* the vertices of lines and areas in flat arrays (coords, ring offsets, feature offsets),
* attribute columns: numbers as float arrays, everything else as codes into a list of distinct
  values (so 500 000 times "Grav" is stored once).

Tables never change after they are made; selecting, filtering or deleting features makes a new
table. That keeps drawing on background threads safe and makes those steps undoable."""
from __future__ import annotations

import io
import json
import math
import re
import struct
import threading
from array import array
from pathlib import Path

import numpy as np

POINT, LINE, AREA = 0, 1, 2
KIND_NAMES = {POINT: "point", LINE: "line", AREA: "polygon"}


# ====================================================================== columns
class NumCol:
    kind = "num"

    def __init__(self, values: np.ndarray, is_int: bool):
        self.values = np.asarray(values, dtype=np.float64)
        self.is_int = bool(is_int)

    def get(self, i):
        v = self.values[i]
        if not math.isfinite(v):
            return None
        return int(v) if self.is_int else float(v)

    def text(self, i) -> str:
        v = self.get(i)
        return "" if v is None else (str(v) if self.is_int else f"{v:g}")

    def take(self, idx):
        return NumCol(self.values[idx], self.is_int)

    def counts(self, limit=300):
        v = self.values[np.isfinite(self.values)]
        if not len(v):
            return []
        u, c = np.unique(v, return_counts=True)
        o = np.argsort(-c, kind="stable")[:limit]
        return [((int(u[k]) if self.is_int else float(u[k])), int(c[k])) for k in o]


class CatCol:
    kind = "cat"

    def __init__(self, codes: np.ndarray, cats: list):
        self.codes = np.asarray(codes, dtype=np.int32)
        self.cats = list(cats)

    def get(self, i):
        c = self.codes[i]
        return None if c < 0 else self.cats[c]

    def text(self, i) -> str:
        v = self.get(i)
        return "" if v is None else str(v)

    def take(self, idx):
        codes = self.codes[idx]
        used = np.unique(codes[codes >= 0])
        remap = np.full(len(self.cats) + 1, -1, dtype=np.int32)
        remap[used] = np.arange(len(used), dtype=np.int32)
        return CatCol(np.where(codes >= 0, remap[codes], -1), [self.cats[k] for k in used])

    def counts(self, limit=300):
        c = np.bincount(self.codes[self.codes >= 0], minlength=len(self.cats))
        o = np.argsort(-c, kind="stable")
        return [(self.cats[k], int(c[k])) for k in o[:limit] if c[k] > 0]

    def mask(self, pred) -> np.ndarray:
        """Vectorised: evaluate pred once per distinct value."""
        pc = np.array([bool(pred(v)) for v in self.cats] + [bool(pred(None))], dtype=bool)
        return pc[self.codes]          # code -1 picks the last entry (None)


class _ColBuilder:
    __slots__ = ("index", "cats", "codes", "numeric", "ints")

    def __init__(self):
        self.index: dict = {}
        self.cats: list = []
        self.codes = array("i")
        self.numeric = True
        self.ints = True

    def add(self, v):
        if v is None or isinstance(v, (bytes, bytearray, memoryview)) or v == "" or (isinstance(v, float) and v != v):
            self.codes.append(-1)
            return
        c = self.index.get(v)
        if c is None:
            if isinstance(v, bool) or not isinstance(v, (int, float)):
                if not isinstance(v, str):
                    v = str(v)
                    c = self.index.get(v)
                self.numeric = False
            elif isinstance(v, float):
                self.ints = False if not v.is_integer() else self.ints
            if c is None:
                c = len(self.cats)
                self.index[v] = c
                self.cats.append(v)
        self.codes.append(c)

    def finish(self):
        codes = np.frombuffer(self.codes, dtype=np.int32).copy() if len(self.codes) else np.zeros(0, np.int32)
        if self.numeric and self.cats:
            vals = np.array([float(c) for c in self.cats] + [np.nan])
            return NumCol(vals[codes], self.ints and all(abs(float(c)) < 2 ** 53 for c in self.cats))
        return CatCol(codes, [str(c) for c in self.cats])


# ====================================================================== building
class TableBuilder:
    """Collects features one at a time (coordinates in any CRS; to_wgs84 is applied at the end)."""

    def __init__(self, fields: list[str] | None = None):
        self.fields: list[str] = list(fields or [])
        self.cols = {f: _ColBuilder() for f in self.fields}
        self.n = 0
        self.kind = array("b")
        self.px = array("d")             # point coordinates (or nan for lines / areas)
        self.py = array("d")
        self.rings: list[np.ndarray] = []
        self.ring_feat = array("q")      # feature of each ring

    def _attrs(self, attrs: dict | list | tuple):
        if isinstance(attrs, dict):
            for k in attrs:
                if k not in self.cols:
                    b = _ColBuilder()
                    for _ in range(self.n):
                        b.codes.append(-1)
                    self.cols[k] = b
                    self.fields.append(k)
            for f, b in self.cols.items():
                b.add(attrs.get(f))
        else:
            for b, v in zip(self.cols.values(), attrs):
                b.add(v)

    def add_point(self, x, y, attrs):
        self._attrs(attrs)
        self.kind.append(POINT)
        self.px.append(float(x))
        self.py.append(float(y))
        self.n += 1

    def add_shape(self, kind: int, rings, attrs):
        """rings: list of (k, 2) arrays (lines: the parts; areas: outer rings and holes, even-odd)."""
        rr = [np.asarray(r, dtype=np.float64)[:, :2] for r in rings if r is not None and len(r) >= 2]
        if not rr:
            return False
        self._attrs(attrs)
        self.kind.append(kind)
        self.px.append(np.nan)
        self.py.append(np.nan)
        for r in rr:
            self.rings.append(r)
            self.ring_feat.append(self.n)
        self.n += 1
        return True

    def add_geometry(self, g, attrs):
        """g: geo.parse_wkb style (type, data) or nested-list (type, coords)."""
        if g is None or g[1] is None:
            return False
        t, d = g
        if t == "Point":
            return self.add_point(d[0], d[1], attrs) or True
        if t == "MultiPoint":
            p = d[0][1] if isinstance(d[0], tuple) else d[0]
            return self.add_point(p[0], p[1], attrs) or True
        if t == "LineString":
            return self.add_shape(LINE, [d], attrs)
        if t == "MultiLineString":
            return self.add_shape(LINE, [p[1] if isinstance(p, tuple) else p for p in d], attrs)
        if t == "Polygon":
            return self.add_shape(AREA, list(d), attrs)
        if t == "MultiPolygon":
            rings = []
            for p in d:
                rings += list(p[1] if isinstance(p, tuple) else p)
            return self.add_shape(AREA, rings, attrs)
        if t == "GeometryCollection":
            for p in d:
                if self.add_geometry(p if isinstance(p, tuple) else None, attrs):
                    return True
        return False

    def finish(self, to_wgs84=None) -> "Table":
        n = self.n
        kind = np.frombuffer(self.kind, dtype=np.int8).copy() if n else np.zeros(0, np.int8)
        x = np.frombuffer(self.px, dtype=np.float64).copy() if n else np.zeros(0)
        y = np.frombuffer(self.py, dtype=np.float64).copy() if n else np.zeros(0)
        if self.rings:
            coords = np.concatenate(self.rings)
            sizes = np.array([len(r) for r in self.rings], dtype=np.int64)
        else:
            coords = np.zeros((0, 2))
            sizes = np.zeros(0, dtype=np.int64)
        ring_start = np.concatenate([[0], np.cumsum(sizes)]).astype(np.int64)
        rf = np.frombuffer(self.ring_feat, dtype=np.int64) if len(self.ring_feat) else np.zeros(0, np.int64)
        feat_ring = np.zeros(n + 1, dtype=np.int64)
        if len(rf):
            np.add.at(feat_ring, rf + 1, 1)
        feat_ring = np.cumsum(feat_ring)
        if to_wgs84 is not None:
            pts = kind == POINT
            if pts.any():
                ll = to_wgs84(np.column_stack([x[pts], y[pts]]))
                x[pts], y[pts] = ll[:, 0], ll[:, 1]
            if len(coords):
                coords = np.asarray(to_wgs84(coords), dtype=np.float64)
        cols = {f: b.finish() for f, b in self.cols.items()}
        t = Table(x, y, kind, coords, ring_start, feat_ring, cols, list(self.fields))
        t._fill_rep_points()
        return t


# ====================================================================== the table
class Table:
    def __init__(self, x, y, kind, coords, ring_start, feat_ring, cols: dict, fields: list[str]):
        self.x = np.asarray(x, dtype=np.float64)          # lon of the point / representative point
        self.y = np.asarray(y, dtype=np.float64)
        self.kind = np.asarray(kind, dtype=np.int8)
        self.coords = np.asarray(coords, dtype=np.float64).reshape(-1, 2)
        self.ring_start = np.asarray(ring_start, dtype=np.int64)
        self.feat_ring = np.asarray(feat_ring, dtype=np.int64)
        self.cols = cols
        self.fields = fields
        self.n = len(self.kind)
        self._lock = threading.Lock()
        self._prep = None

    # -- basics
    def __len__(self):
        return self.n

    def rings(self, i) -> list[np.ndarray]:
        a, b = self.feat_ring[i], self.feat_ring[i + 1]
        return [self.coords[self.ring_start[r]:self.ring_start[r + 1]] for r in range(a, b)]

    def row(self, i) -> dict:
        return {f: self.cols[f].get(i) for f in self.fields}

    def text(self, i, field) -> str:
        c = self.cols.get(field)
        return "" if c is None else c.text(i)

    def kinds_present(self) -> set:
        return {KIND_NAMES[int(k)] for k in np.unique(self.kind)}

    def main_kind(self) -> str:
        if not self.n:
            return "point"
        c = np.bincount(self.kind, minlength=3)
        return KIND_NAMES[int(np.argmax(c))]

    def bbox(self):
        xs = np.concatenate([self.x[np.isfinite(self.x)], self.coords[:, 0]])
        ys = np.concatenate([self.y[np.isfinite(self.y)], self.coords[:, 1]])
        if not len(xs):
            return None
        return float(xs.min()), float(ys.min()), float(xs.max()), float(ys.max())

    def bbox_of(self, idx) -> tuple | None:
        idx = np.asarray(idx)
        if not len(idx):
            return None
        xs, ys = [self.x[idx]], [self.y[idx]]
        sh = idx[self.kind[idx] != POINT]
        if len(sh) and len(sh) < 20000:
            for i in sh.tolist():
                for r in self.rings(i):
                    xs.append(r[:, 0])
                    ys.append(r[:, 1])
        X, Y = np.concatenate(xs), np.concatenate(ys)
        ok = np.isfinite(X) & np.isfinite(Y)
        if not ok.any():
            return None
        return float(X[ok].min()), float(Y[ok].min()), float(X[ok].max()), float(Y[ok].max())

    def _fill_rep_points(self):
        """Representative points for lines (middle vertex) and areas (mean of the first ring)."""
        sh = np.nonzero(self.kind != POINT)[0]
        if not len(sh):
            return
        first_ring = self.feat_ring[sh]
        a, b = self.ring_start[first_ring], self.ring_start[first_ring + 1]
        lines = self.kind[sh] == LINE
        mid = (a + b) // 2
        sums = np.add.reduceat(self.coords, self.ring_start[:-1], axis=0) if len(self.coords) else None
        if sums is not None:
            cnt = np.maximum(1, self.ring_start[1:] - self.ring_start[:-1])[:, None]
            means = sums / cnt
            # a closed ring repeats its first vertex – close enough for a label position
            rep = means[first_ring]
            rep[lines] = self.coords[mid[lines]]
            self.x[sh], self.y[sh] = rep[:, 0], rep[:, 1]

    # -- deriving new tables
    def take(self, idx) -> "Table":
        idx = np.asarray(idx)
        if idx.dtype == bool:
            idx = np.nonzero(idx)[0]
        ring_counts = self.feat_ring[idx + 1] - self.feat_ring[idx]
        rings = np.concatenate([np.arange(self.feat_ring[i], self.feat_ring[i + 1]) for i in idx[ring_counts > 0]]
                               ) if (ring_counts > 0).any() else np.zeros(0, np.int64)
        if len(rings):
            sizes = self.ring_start[rings + 1] - self.ring_start[rings]
            starts = self.ring_start[rings]
            total = int(sizes.sum())
            # gather the vertices of the chosen rings in one go
            offs = np.repeat(starts - np.concatenate([[0], np.cumsum(sizes)[:-1]]), sizes)
            coords = self.coords[np.arange(total) + offs]
            ring_start = np.concatenate([[0], np.cumsum(sizes)])
        else:
            coords, ring_start = np.zeros((0, 2)), np.zeros(1, np.int64)
        feat_ring = np.concatenate([[0], np.cumsum(ring_counts)])
        cols = {f: c.take(idx) for f, c in self.cols.items()}
        return Table(self.x[idx], self.y[idx], self.kind[idx], coords, ring_start, feat_ring, cols, list(self.fields))

    # -- background preparation for drawing (Web Mercator, spatial index, boxes)
    def prepared(self):
        with self._lock:
            if self._prep is None:
                self._prep = _Prepared(self)
            return self._prep

    # -- saving
    def save(self, z, prefix: str):
        def npy(name, a):
            buf = io.BytesIO()
            np.save(buf, a, allow_pickle=False)
            z.writestr(f"{prefix}/{name}.npy", buf.getvalue())
        meta = {"n": self.n, "fields": self.fields, "cols": {}}
        for k in ("x", "y", "kind", "coords", "ring_start", "feat_ring"):
            npy(k, getattr(self, k))
        for j, f in enumerate(self.fields):
            c = self.cols[f]
            if c.kind == "num":
                npy(f"c{j}", c.values)
                meta["cols"][f] = {"kind": "num", "int": c.is_int, "file": f"c{j}"}
            else:
                npy(f"c{j}", c.codes)
                meta["cols"][f] = {"kind": "cat", "file": f"c{j}", "cats": c.cats}
        z.writestr(f"{prefix}/meta.json", json.dumps(meta, ensure_ascii=False, separators=(",", ":")))

    @classmethod
    def load(cls, z, prefix: str) -> "Table":
        def npy(name):
            return np.load(io.BytesIO(z.read(f"{prefix}/{name}.npy")), allow_pickle=False)
        meta = json.loads(z.read(f"{prefix}/meta.json").decode("utf-8"))
        cols = {}
        for f in meta["fields"]:
            m = meta["cols"][f]
            cols[f] = NumCol(npy(m["file"]), m.get("int", False)) if m["kind"] == "num" else CatCol(npy(m["file"]), m["cats"])
        return cls(npy("x"), npy("y"), npy("kind"), npy("coords"), npy("ring_start"), npy("feat_ring"), cols,
                   list(meta["fields"]))

    # -- other sources
    @classmethod
    def from_records(cls, recs, fields=None, progress=None, cancelled=None) -> "Table":
        """Records (lon, lat, attributes dict, geometry or None) from sources.read_file / download."""
        names = [f[0] if isinstance(f, (list, tuple)) else str(f) for f in (fields or [])]
        b = TableBuilder(names)
        for k, (lon, lat, attrs, geom) in enumerate(recs):
            if geom is None:
                try:
                    lon, lat = float(lon), float(lat)
                except (TypeError, ValueError):
                    continue
                if math.isfinite(lon) and math.isfinite(lat):
                    b.add_point(lon, lat, attrs or {})
            else:
                b.add_geometry(_nested_to_arrays(geom), attrs or {})
            if k % 20000 == 0:
                if cancelled and cancelled():
                    raise RuntimeError("Cancelled")
                if progress:
                    progress(k)
        return b.finish()

    @classmethod
    def from_features(cls, feats: list) -> "Table":
        """The JSON feature lists of MapMaker 0.1 files."""
        recs = []
        for f in feats:
            t, c = f["g"]
            if t == "Point":
                recs.append((c[0], c[1], f.get("p") or {}, None))
            else:
                recs.append((None, None, f.get("p") or {}, (t, c)))
        return cls.from_records(recs)


def _nested_to_arrays(geom):
    t, c = geom
    if t == "LineString":
        return ("LineString", np.asarray(c, dtype=float))
    if t == "MultiLineString":
        return ("MultiLineString", [np.asarray(p, dtype=float) for p in c])
    if t == "Polygon":
        return ("Polygon", [np.asarray(r, dtype=float) for r in c])
    if t == "MultiPolygon":
        return ("MultiPolygon", [[np.asarray(r, dtype=float) for r in poly] for poly in c])
    if t == "Point":
        return ("Point", c)
    return None


# ====================================================================== drawing support
class _Prepared:
    """Web Mercator positions, a Z-order index of the representative points and the boxes of shapes."""

    def __init__(self, t: Table):
        from .render import lonlat_to_merc
        mx, my = lonlat_to_merc(t.x, t.y)
        self.mx = np.where(np.isfinite(t.x), mx, np.nan)
        self.my = np.where(np.isfinite(t.y), my, np.nan)
        self.index = PointIndex(self.mx, self.my)
        if len(t.coords):
            cx, cy = lonlat_to_merc(t.coords[:, 0], t.coords[:, 1])
            self.cm = np.column_stack([cx, cy])
        else:
            self.cm = np.zeros((0, 2))
        self.shapes = np.nonzero(t.kind != POINT)[0]
        self.points = np.nonzero(t.kind == POINT)[0]
        if len(self.shapes):
            rs = t.ring_start
            fr = t.feat_ring
            # box of every ring, then of every shape
            st = rs[:-1]
            minx = np.minimum.reduceat(self.cm[:, 0], st)
            maxx = np.maximum.reduceat(self.cm[:, 0], st)
            miny = np.minimum.reduceat(self.cm[:, 1], st)
            maxy = np.maximum.reduceat(self.cm[:, 1], st)
            a = fr[self.shapes]
            self.sbb = np.column_stack([np.minimum.reduceat(minx, a), np.minimum.reduceat(miny, a),
                                        np.maximum.reduceat(maxx, a), np.maximum.reduceat(maxy, a)])
            # shapes own consecutive runs of rings, so each reduceat slice is exactly one shape's rings
        else:
            self.sbb = np.zeros((0, 4))

    def shapes_in(self, box, min_size: float = 0.0) -> np.ndarray:
        if not len(self.shapes):
            return self.shapes
        x0, y0, x1, y1 = box
        b = self.sbb
        m = (b[:, 2] >= x0) & (b[:, 0] <= x1) & (b[:, 3] >= y0) & (b[:, 1] <= y1)
        if min_size > 0:
            m &= ((b[:, 2] - b[:, 0]) >= min_size) | ((b[:, 3] - b[:, 1]) >= min_size)
        return self.shapes[m]

    def points_in(self, box) -> np.ndarray:
        x0, y0, x1, y1 = box
        p = self.points
        if not len(p):
            return p
        mx, my = self.mx[p], self.my[p]
        return p[(mx >= x0) & (mx <= x1) & (my >= y0) & (my <= y1)]


def _spread(v: np.ndarray) -> np.ndarray:
    """Insert a zero bit between the bits of a 32-bit integer (for Morton codes)."""
    v = v.astype(np.uint64) & np.uint64(0xFFFFFFFF)
    v = (v | (v << np.uint64(16))) & np.uint64(0x0000FFFF0000FFFF)
    v = (v | (v << np.uint64(8))) & np.uint64(0x00FF00FF00FF00FF)
    v = (v | (v << np.uint64(4))) & np.uint64(0x0F0F0F0F0F0F0F0F)
    v = (v | (v << np.uint64(2))) & np.uint64(0x3333333333333333)
    v = (v | (v << np.uint64(1))) & np.uint64(0x5555555555555555)
    return v


class PointIndex:
    """Positions sorted along a Z-order (Morton) curve: the points of any map tile are one
    contiguous slice, found with two binary searches."""
    LEVEL = 22

    def __init__(self, mx: np.ndarray, my: np.ndarray):
        n = 2 ** self.LEVEL
        ok = np.isfinite(mx) & np.isfinite(my) & (mx >= 0) & (mx < 1) & (my >= 0) & (my < 1)
        ix = np.clip(np.where(ok, mx, 0) * n, 0, n - 1).astype(np.uint64)
        iy = np.clip(np.where(ok, my, 0) * n, 0, n - 1).astype(np.uint64)
        code = _spread(ix) | (_spread(iy) << np.uint64(1))
        code[~ok] = np.uint64(1) << np.uint64(62)
        self.order = np.argsort(code, kind="stable").astype(np.int64)
        self.codes = code[self.order]

    def tile_ids(self, z: int, x: int, y: int) -> np.ndarray:
        n = 2 ** z
        if not (0 <= y < n):
            return self.order[:0]
        x %= n
        if z > self.LEVEL:
            k = z - self.LEVEL
            z, x, y = self.LEVEL, x >> k, y >> k
        shift = np.uint64(2 * (self.LEVEL - z))
        c = (_spread(np.array([x]))[0] | (_spread(np.array([y]))[0] << np.uint64(1)))
        lo = c << shift
        hi = (c + np.uint64(1)) << shift
        a = int(np.searchsorted(self.codes, lo, "left"))
        b = int(np.searchsorted(self.codes, hi, "left"))
        return self.order[a:b]

    def ids_near_tile(self, z: int, x: int, y: int) -> np.ndarray:
        parts = [self.tile_ids(z, x + dx, y + dy) for dy in (-1, 0, 1) for dx in (-1, 0, 1)]
        return np.concatenate(parts)


# ====================================================================== reading GeoPackages quickly
def read_gpkg(path, table: str | None = None, progress=None, cancelled=None) -> tuple["Table", str]:
    """A GeoPackage layer as a Table. Points are decoded without building a geometry object per
    row and all coordinates are converted to lon/lat in one go, so 500 000 points take seconds."""
    import sqlite3

    from . import geo
    from .gpkg import discover, resolve_epsg
    p = Path(path)
    layers = discover([p])
    if not layers:
        raise ValueError("The GeoPackage has no feature layers.")
    lay = next((d for d in layers if d["table"] == table), layers[0])
    epsg = resolve_epsg(lay["srsdef"], lay["srs"])
    to_wgs = geo.get_to_wgs84(epsg, lay["srsdef"][4] if lay["srsdef"] else None)
    cols = [c for c, _ in lay["cols"]]
    b = TableBuilder(cols)
    con = sqlite3.connect(f"file:{p.as_posix()}?mode=ro", uri=True)
    total = lay.get("n") or 0
    done = 0
    try:
        sel = ", ".join([f'"{lay["geom"]}"'] + [f'"{c}"' for c in cols])
        cur = con.execute(f'SELECT {sel} FROM "{lay["table"]}"')
        unpack = struct.unpack_from
        while True:
            rows = cur.fetchmany(20000)
            if not rows:
                break
            for row in rows:
                blob = row[0]
                if blob is None:
                    continue
                _srs, wkb = geo.split_gpkg_blob(blob)
                if not wkb:
                    continue
                e = "<" if wkb[0] == 1 else ">"
                t = unpack(e + "I", wkb, 1)[0] & 0x0FFFFFFF
                base = t % 1000
                if base == 1 and not (t & 0x20000000):
                    x, y = unpack(e + "dd", wkb, 5)
                    if x == x:
                        b.add_point(x, y, row[1:])
                    continue
                try:
                    g = geo.parse_wkb(wkb)
                except Exception:
                    continue
                b.add_geometry(g, row[1:])
            done += len(rows)
            if progress:
                progress(done, total)
            if cancelled and cancelled():
                raise RuntimeError("Cancelled")
    finally:
        con.close()
    name = lay["table"]
    return b.finish(to_wgs), name


# ====================================================================== attribute queries
OPS = [("eq", "is"), ("ne", "is not"), ("in", "is one of"), ("contains", "contains"),
       ("not_contains", "does not contain"), ("starts", "starts with"), ("gt", ">"), ("ge", "≥"), ("lt", "<"),
       ("le", "≤"), ("empty", "is empty"), ("not_empty", "has a value")]
NO_VALUE_OPS = ("empty", "not_empty")


def _num(s):
    if isinstance(s, (int, float)):
        return float(s)
    try:
        return float(str(s).strip().replace(" ", "").replace(",", "."))
    except (TypeError, ValueError):
        return None


def query(t: Table, field: str, op: str, value) -> np.ndarray:
    """Boolean mask of the features where `field op value` holds. Text comparisons ignore case."""
    col = t.cols.get(field)
    if col is None:
        raise KeyError(field)
    if op == "empty":
        return ~np.isfinite(col.values) if col.kind == "num" else col.codes < 0
    if op == "not_empty":
        return np.isfinite(col.values) if col.kind == "num" else col.codes >= 0
    sval = "" if value is None else str(value).strip()
    if col.kind == "num":
        v = col.values
        if op == "in":
            nums = [x for x in (_num(s) for s in re.split(r"[;,]\s*|\s+", sval) if s) if x is not None]
            return np.isin(v, nums)
        nv = _num(sval)
        if op in ("contains", "not_contains", "starts"):
            txt = sval.casefold()
            m = CatCol(*_num_as_cat(col)).mask(lambda c: c is not None and (
                (txt in c.casefold()) if op != "starts" else c.casefold().startswith(txt)))
            return ~m if op == "not_contains" else m
        if nv is None:
            raise ValueError(f"“{sval}” is not a number, and {field} holds numbers.")
        with np.errstate(invalid="ignore"):
            return {"eq": v == nv, "ne": (v != nv) & np.isfinite(v), "gt": v > nv, "ge": v >= nv, "lt": v < nv,
                    "le": v <= nv}[op]
    txt = sval.casefold()
    nv = _num(sval)
    if op == "eq":
        return col.mask(lambda c: c is not None and c.casefold() == txt)
    if op == "ne":
        return col.mask(lambda c: c is not None and c.casefold() != txt)
    if op == "in":
        wanted = {s.strip().casefold() for s in re.split(r"[;,]", sval) if s.strip()}
        return col.mask(lambda c: c is not None and c.casefold() in wanted)
    if op == "contains":
        return col.mask(lambda c: c is not None and txt in c.casefold())
    if op == "not_contains":
        return col.mask(lambda c: c is None or txt not in c.casefold())
    if op == "starts":
        return col.mask(lambda c: c is not None and c.casefold().startswith(txt))
    if op in ("gt", "ge", "lt", "le"):
        import operator
        f = {"gt": operator.gt, "ge": operator.ge, "lt": operator.lt, "le": operator.le}[op]

        def pred(c):
            if c is None:
                return False
            cn = _num(c)
            if cn is not None and nv is not None:
                return f(cn, nv)
            return f(c.casefold(), txt)
        return col.mask(pred)
    raise ValueError(op)


def _num_as_cat(col: NumCol):
    v = col.values
    ok = np.isfinite(v)
    u, inv = np.unique(v[ok], return_inverse=True)
    codes = np.full(len(v), -1, dtype=np.int32)
    codes[ok] = inv
    cats = [str(int(x)) if col.is_int else f"{x:g}" for x in u]
    return codes, cats




# ====================================================================== thematic classes
def _pretty(v: float) -> float:
    """Round a class break to two significant figures."""
    if not math.isfinite(v) or v == 0:
        return v
    e = 10 ** (math.floor(math.log10(abs(v))) - 1)
    return round(v / e) * e


def _fmt_num(v: float) -> str:
    if abs(v) >= 1000:
        return f"{v:,.0f}".replace(",", " ")
    return f"{v:g}"


def make_categories(t: Table, field: str, scheme: str = "Set2", max_classes: int = 12) -> dict:
    """One class per value of a field (the most common ones; the rest go into “Other”)."""
    from .styles import scheme_colors
    counts = t.cols[field].counts(100000)
    top = counts[:max_classes]
    colors = scheme_colors(scheme, len(top))
    rest = sum(c for _v, c in counts[max_classes:])
    return {"mode": "categories", "field": field, "scheme": scheme,
            "classes": [{"value": v, "label": str(v), "color": colors[i], "show": True, "count": n}
                        for i, (v, n) in enumerate(top)],
            "other": {"show": rest > 0, "color": "#b4b4b4", "label": "Other", "count": rest}}


def numeric_values(t: Table, field: str) -> np.ndarray:
    col = t.cols[field]
    if col.kind == "num":
        return col.values
    nums = np.array([_num(c) if _num(c) is not None else np.nan for c in col.cats] + [np.nan])
    return nums[col.codes]


def make_graduated(t: Table, field: str, n: int = 5, method: str = "quantile", scheme: str = "YlOrRd") -> dict:
    """Classes of a number field: equal counts (quantiles) or equal steps (equal intervals)."""
    from .styles import scheme_colors
    v = numeric_values(t, field)
    v = v[np.isfinite(v)]
    if not len(v):
        raise ValueError(f"{field} holds no numbers.")
    lo, hi = float(v.min()), float(v.max())
    if method == "equal":
        edges = np.linspace(lo, hi, n + 1)
    else:
        edges = np.quantile(v, np.linspace(0, 1, n + 1))
    edges = [lo] + [_pretty(float(e)) for e in edges[1:-1]] + [hi]
    edges = sorted(set(edges))
    if len(edges) < 2:
        edges = [lo, hi if hi > lo else lo + 1]
    colors = scheme_colors(scheme, len(edges) - 1)
    classes = []
    for i in range(len(edges) - 1):
        a, b = edges[i], edges[i + 1]
        last = i == len(edges) - 2
        cnt = int(((v >= a) & ((v <= b) if last else (v < b))).sum())
        classes.append({"lo": a, "hi": b, "label": f"{_fmt_num(a)} – {_fmt_num(b)}", "color": colors[i], "show": True,
                        "count": cnt})
    return {"mode": "graduated", "field": field, "scheme": scheme, "method": method, "classes": classes,
            "other": {"show": False, "color": "#b4b4b4", "label": "No value", "count": 0}}


def class_index(t: Table, cls: dict | None) -> np.ndarray | None:
    """Class of every feature: 0..k-1 for the classes, k for “other”, -1 = not drawn. None = single colour."""
    if not cls or cls.get("mode", "single") == "single" or cls.get("field") not in t.cols:
        return None
    key = json.dumps(cls, sort_keys=True, default=str)
    with t._lock:
        cache = t.__dict__.setdefault("_cls_cache", {})
        hit = cache.get(key)
    if hit is not None:
        return hit
    classes = cls.get("classes") or []
    k = len(classes)
    other = cls.get("other") or {}
    out = np.full(t.n, k if other.get("show") else -1, dtype=np.int16)
    col = t.cols[cls["field"]]
    if cls["mode"] == "categories":
        if col.kind == "cat":
            lookup = {str(c["value"]): i for i, c in enumerate(classes)}
            per = np.array([lookup.get(c, -2) for c in col.cats] + [-2], dtype=np.int16)
            ci = per[col.codes]
        else:
            ci = np.full(t.n, -2, dtype=np.int16)
            for i, c in enumerate(classes):
                nv = _num(c["value"])
                if nv is not None:
                    ci[col.values == nv] = i
        found = ci >= 0
        out[found] = ci[found]
    else:
        v = numeric_values(t, cls["field"])
        for i, c in enumerate(classes):
            last = i == k - 1
            m = (v >= c["lo"]) & ((v <= c["hi"]) if last else (v < c["hi"]))
            out[m] = i
    for i, c in enumerate(classes):
        if not c.get("show", True):
            out[out == i] = -1
    with t._lock:
        cache[key] = out
        if len(cache) > 8:
            cache.pop(next(iter(cache)))
    return out


def class_colors(cls: dict) -> list[str]:
    """Colours by class index, “other” last."""
    return [c["color"] for c in cls.get("classes") or []] + [(cls.get("other") or {}).get("color", "#b4b4b4")]


__all__ = ["Table", "TableBuilder", "NumCol", "CatCol", "read_gpkg", "query", "OPS", "NO_VALUE_OPS", "POINT", "LINE",
           "AREA", "KIND_NAMES", "PointIndex", "make_categories", "make_graduated", "class_index", "class_colors",
           "numeric_values"]
