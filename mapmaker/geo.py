"""Geometry helpers with no heavy dependencies.

* Parses GeoPackage geometry blobs (GP header + WKB) into simple Python/numpy
  structures.
* Coordinate transforms for the Swedish systems (SWEREF 99 TM + local zones),
  WGS 84 and Web Mercator. If ``pyproj`` is installed, it is used for any other
  coordinate system found in the source files.
"""
from __future__ import annotations

import math
import struct
from typing import Callable

import numpy as np

# --------------------------------------------------------------------------
# GeoPackage / WKB parsing
# --------------------------------------------------------------------------

GEOM_NAMES = {1: "Point", 2: "LineString", 3: "Polygon", 4: "MultiPoint",
              5: "MultiLineString", 6: "MultiPolygon", 7: "GeometryCollection"}
_ENV_SIZE = {0: 0, 1: 32, 2: 48, 3: 48, 4: 64}


def split_gpkg_blob(blob: bytes) -> tuple[int, bytes | None]:
    """Return (srs_id, wkb) for a GeoPackage geometry blob.
    Plain WKB (no GP header) is also accepted. Returns wkb=None for empty geometries."""
    if blob is None:
        return 0, None
    b = bytes(blob)
    if len(b) >= 8 and b[0:2] == b"GP":
        flags = b[3]
        little = flags & 1
        env = (flags >> 1) & 7
        empty = (flags >> 4) & 1
        srs_id = struct.unpack("<i" if little else ">i", b[4:8])[0]
        if empty:
            return srs_id, None
        start = 8 + _ENV_SIZE.get(env, 0)
        return srs_id, b[start:]
    return 0, b


class _Reader:
    __slots__ = ("b", "o")

    def __init__(self, b: bytes):
        self.b = b
        self.o = 0

    def geom(self):
        b = self.b
        le = b[self.o] == 1
        self.o += 1
        e = "<" if le else ">"
        t = struct.unpack_from(e + "I", b, self.o)[0]
        self.o += 4
        # EWKB flags
        has_z = bool(t & 0x80000000)
        has_m = bool(t & 0x40000000)
        if t & 0x20000000:
            self.o += 4  # embedded SRID
        t &= 0x0FFFFFFF
        # ISO WKB (1000 = Z, 2000 = M, 3000 = ZM)
        dim_code, base = divmod(t, 1000)
        if dim_code in (1, 3):
            has_z = True
        if dim_code in (2, 3):
            has_m = True
        nd = 2 + has_z + has_m
        dt = np.dtype(e + "f8")

        def coords(n):
            arr = np.frombuffer(b, dtype=dt, count=n * nd, offset=self.o).reshape(n, nd)[:, :2]
            self.o += n * nd * 8
            return arr

        def count():
            n = struct.unpack_from(e + "I", b, self.o)[0]
            self.o += 4
            return n

        if base == 1:
            c = coords(1)[0]
            if math.isnan(c[0]):
                return ("Point", None)
            return ("Point", c)
        if base == 2:
            return ("LineString", coords(count()))
        if base == 3:
            return ("Polygon", [coords(count()) for _ in range(count())])
        if base in (4, 5, 6, 7):
            parts = [self.geom() for _ in range(count())]
            return (GEOM_NAMES[base], parts)
        # Curves etc. are not supported – treated as empty
        raise ValueError(f"Unsupported WKB geometry type {t}")


def parse_wkb(wkb: bytes | None):
    """Return (type_name, data). data is a numpy array / nested lists of arrays."""
    if not wkb:
        return None
    return _Reader(wkb).geom()


def _all_coords(g, out: list):
    t, d = g
    if d is None:
        return
    if t == "Point":
        out.append(d.reshape(1, 2))
    elif t == "LineString":
        out.append(d)
    elif t == "Polygon":
        out.extend(d)
    else:
        for p in d:
            _all_coords(p, out)


def all_coords(g) -> np.ndarray:
    out: list = []
    _all_coords(g, out)
    if not out:
        return np.empty((0, 2))
    return np.concatenate(out)


def _ring_area_centroid(r: np.ndarray):
    if len(r) < 3:
        return 0.0, r.mean(axis=0)
    x, y = r[:, 0], r[:, 1]
    # shift to local origin for numerical stability
    x0, y0 = x[0], y[0]
    x = x - x0
    y = y - y0
    x1, y1 = np.roll(x, -1), np.roll(y, -1)
    cross = x * y1 - x1 * y
    a = cross.sum() / 2.0
    if abs(a) < 1e-12:
        return 0.0, r.mean(axis=0)
    cx = ((x + x1) * cross).sum() / (6 * a) + x0
    cy = ((y + y1) * cross).sum() / (6 * a) + y0
    return abs(a), np.array([cx, cy])


def representative_point(g) -> tuple[float, float] | None:
    """A single point for the overview map: the point itself, the middle vertex of
    a line, or the centroid of the largest polygon."""
    if g is None:
        return None
    t, d = g
    if d is None:
        return None
    if t == "Point":
        return float(d[0]), float(d[1])
    if t == "LineString":
        if len(d) == 0:
            return None
        return float(d[len(d) // 2][0]), float(d[len(d) // 2][1])
    if t == "Polygon":
        if not d or len(d[0]) == 0:
            return None
        _, c = _ring_area_centroid(d[0])
        return float(c[0]), float(c[1])
    if t == "MultiPolygon":
        best, bc = -1.0, None
        for p in d:
            if p[1] and len(p[1][0]):
                a, c = _ring_area_centroid(p[1][0])
                if a > best:
                    best, bc = a, c
        return (float(bc[0]), float(bc[1])) if bc is not None else None
    if t == "MultiLineString":
        longest = max((p for p in d if p[1] is not None and len(p[1])), key=lambda p: len(p[1]), default=None)
        return representative_point(longest)
    c = all_coords(g)
    if len(c) == 0:
        return None
    return float(c[:, 0].mean()), float(c[:, 1].mean())


def map_coords(g, fn: Callable[[np.ndarray], np.ndarray]):
    """Apply a vectorised coordinate function to every coordinate array in g."""
    t, d = g
    if d is None:
        return g
    if t == "Point":
        return (t, fn(d.reshape(1, 2))[0])
    if t == "LineString":
        return (t, fn(d))
    if t == "Polygon":
        return (t, [fn(r) for r in d])
    return (t, [map_coords(p, fn) for p in d])


def transform_geom_fast(g, fn):
    """Transform all coordinates of a geometry in one vectorised call."""
    arrays: list = []
    _all_coords(g, arrays)
    if not arrays:
        return g
    sizes = [len(a) for a in arrays]
    allc = fn(np.concatenate(arrays))
    it = iter(np.split(allc, np.cumsum(sizes)[:-1]))

    def rebuild(gg):
        t, d = gg
        if d is None:
            return gg
        if t == "Point":
            return (t, next(it)[0])
        if t == "LineString":
            return (t, next(it))
        if t == "Polygon":
            return (t, [next(it) for _ in d])
        return (t, [rebuild(p) for p in d])

    return rebuild(g)


def to_geojson(g, ndigits: int = 7):
    if g is None or g[1] is None:
        return None
    t, d = g

    def r(a):
        return np.round(a, ndigits).tolist()

    if t == "Point":
        return {"type": t, "coordinates": r(d)}
    if t == "LineString":
        return {"type": t, "coordinates": r(d)}
    if t == "Polygon":
        return {"type": t, "coordinates": [r(x) for x in d]}
    if t == "GeometryCollection":
        return {"type": t, "geometries": [to_geojson(p, ndigits) for p in d]}
    return {"type": t, "coordinates": [to_geojson(p, ndigits)["coordinates"] for p in d if p[1] is not None]}


# --------------------------------------------------------------------------
# Projections (GRS 80 Transverse Mercator – Lantmäteriet's Gauss–Krüger formulas)
# --------------------------------------------------------------------------

_A = 6378137.0
_F = 1 / 298.257222101
_E2 = _F * (2 - _F)
_N = _F / (2 - _F)
_AROOF = _A / (1 + _N) * (1 + _N ** 2 / 4 + _N ** 4 / 64)


def _tm_params(lon0: float, k0: float, fn: float, fe: float):
    return dict(lon0=math.radians(lon0), k0=k0, fn=fn, fe=fe)


# EPSG code -> TM parameters (SWEREF 99 TM and the 12 local zones)
_SWEREF_ZONES = {
    3006: (15.0, 0.9996, 0.0, 500000.0),
    3007: (12.0, 1.0, 0.0, 150000.0), 3008: (13.5, 1.0, 0.0, 150000.0),
    3009: (15.0, 1.0, 0.0, 150000.0), 3010: (16.5, 1.0, 0.0, 150000.0),
    3011: (18.0, 1.0, 0.0, 150000.0), 3012: (14.25, 1.0, 0.0, 150000.0),
    3013: (15.75, 1.0, 0.0, 150000.0), 3014: (17.25, 1.0, 0.0, 150000.0),
    3015: (18.75, 1.0, 0.0, 150000.0), 3016: (20.25, 1.0, 0.0, 150000.0),
    3017: (21.75, 1.0, 0.0, 150000.0), 3018: (23.25, 1.0, 0.0, 150000.0),
}


def tm_inverse(xy: np.ndarray, lon0, k0, fn, fe) -> np.ndarray:
    """Grid (easting, northing) -> (lon, lat) degrees."""
    n = _N
    d1 = n / 2 - 2 * n ** 2 / 3 + 37 * n ** 3 / 96 - n ** 4 / 360
    d2 = n ** 2 / 48 + n ** 3 / 15 - 437 * n ** 4 / 1440
    d3 = 17 * n ** 3 / 480 - 37 * n ** 4 / 840
    d4 = 4397 * n ** 4 / 161280
    e2 = _E2
    As = e2 + e2 ** 2 + e2 ** 3 + e2 ** 4
    Bs = -(7 * e2 ** 2 + 17 * e2 ** 3 + 30 * e2 ** 4) / 6
    Cs = (224 * e2 ** 3 + 889 * e2 ** 4) / 120
    Ds = -(4279 * e2 ** 4) / 1260
    E = xy[:, 0]
    N = xy[:, 1]
    xi = (N - fn) / (k0 * _AROOF)
    eta = (E - fe) / (k0 * _AROOF)
    xp = (xi - d1 * np.sin(2 * xi) * np.cosh(2 * eta) - d2 * np.sin(4 * xi) * np.cosh(4 * eta)
          - d3 * np.sin(6 * xi) * np.cosh(6 * eta) - d4 * np.sin(8 * xi) * np.cosh(8 * eta))
    ep = (eta - d1 * np.cos(2 * xi) * np.sinh(2 * eta) - d2 * np.cos(4 * xi) * np.sinh(4 * eta)
          - d3 * np.cos(6 * xi) * np.sinh(6 * eta) - d4 * np.cos(8 * xi) * np.sinh(8 * eta))
    phis = np.arcsin(np.sin(xp) / np.cosh(ep))
    dl = np.arctan2(np.sinh(ep), np.cos(xp))
    s = np.sin(phis)
    lat = phis + s * np.cos(phis) * (As + Bs * s ** 2 + Cs * s ** 4 + Ds * s ** 6)
    lon = lon0 + dl
    return np.column_stack([np.degrees(lon), np.degrees(lat)])


def tm_forward(ll: np.ndarray, lon0, k0, fn, fe) -> np.ndarray:
    """(lon, lat) degrees -> grid (easting, northing)."""
    n = _N
    e2 = _E2
    A = e2
    B = (5 * e2 ** 2 - e2 ** 3) / 6
    C = (104 * e2 ** 3 - 45 * e2 ** 4) / 120
    D = 1237 * e2 ** 4 / 1260
    b1 = n / 2 - 2 * n ** 2 / 3 + 5 * n ** 3 / 16 + 41 * n ** 4 / 180
    b2 = 13 * n ** 2 / 48 - 3 * n ** 3 / 5 + 557 * n ** 4 / 1440
    b3 = 61 * n ** 3 / 240 - 103 * n ** 4 / 140
    b4 = 49561 * n ** 4 / 161280
    lam = np.radians(ll[:, 0])
    phi = np.radians(ll[:, 1])
    s = np.sin(phi)
    phis = phi - s * np.cos(phi) * (A + B * s ** 2 + C * s ** 4 + D * s ** 6)
    dl = lam - lon0
    xp = np.arctan2(np.tan(phis), np.cos(dl))
    ep = np.arctanh(np.cos(phis) * np.sin(dl))
    N = k0 * _AROOF * (xp + b1 * np.sin(2 * xp) * np.cosh(2 * ep) + b2 * np.sin(4 * xp) * np.cosh(4 * ep)
                       + b3 * np.sin(6 * xp) * np.cosh(6 * ep) + b4 * np.sin(8 * xp) * np.cosh(8 * ep)) + fn
    E = k0 * _AROOF * (ep + b1 * np.cos(2 * xp) * np.sinh(2 * ep) + b2 * np.cos(4 * xp) * np.sinh(4 * ep)
                       + b3 * np.cos(6 * xp) * np.sinh(6 * ep) + b4 * np.cos(8 * xp) * np.sinh(8 * ep)) + fe
    return np.column_stack([E, N])


def _merc_inverse(xy):
    R = 6378137.0
    lon = np.degrees(xy[:, 0] / R)
    lat = np.degrees(2 * np.arctan(np.exp(xy[:, 1] / R)) - math.pi / 2)
    return np.column_stack([lon, lat])


def lonlat_to_unit_mercator(lon: np.ndarray, lat: np.ndarray):
    """(lon, lat) -> MapLibre 'MercatorCoordinate' space (0..1)."""
    x = (lon + 180.0) / 360.0
    latc = np.clip(lat, -85.05112878, 85.05112878)
    y = (1 - np.log(np.tan(np.radians(latc)) + 1 / np.cos(np.radians(latc))) / math.pi) / 2
    return x, y


def get_to_wgs84(epsg: int | None, definition: str | None = None) -> Callable[[np.ndarray], np.ndarray]:
    """Return a vectorised function mapping (N,2) source coords -> (N,2) lon/lat."""
    if epsg in (4326, 4258, 4619, 4124, 4979, 4937):  # geographic lon/lat (WGS 84 / ETRS 89 / SWEREF 99)
        return lambda a: np.asarray(a, dtype=float)
    if epsg in _SWEREF_ZONES:
        p = _SWEREF_ZONES[epsg]
        prm = (math.radians(p[0]), p[1], p[2], p[3])
        return lambda a: tm_inverse(np.asarray(a, dtype=float), *prm)
    if epsg in (3857, 900913):
        return _merc_inverse
    try:
        from pyproj import CRS, Transformer  # optional
        src = CRS.from_epsg(epsg) if epsg and epsg > 0 else CRS.from_wkt(definition)
        tr = Transformer.from_crs(src, CRS.from_epsg(4326), always_xy=True)
        return lambda a: np.column_stack(tr.transform(np.asarray(a)[:, 0], np.asarray(a)[:, 1]))
    except ImportError:
        raise RuntimeError(
            f"Coordinate system EPSG:{epsg} is not built in. Install pyproj (pip install pyproj) "
            "or convert the file to SWEREF 99 TM (EPSG:3006) / WGS 84.")


def get_from_wgs84(epsg: int | None) -> Callable[[np.ndarray], np.ndarray]:
    if epsg in (4326, 0, -1, None):
        return lambda a: np.asarray(a, dtype=float)
    if epsg in _SWEREF_ZONES:
        p = _SWEREF_ZONES[epsg]
        prm = (math.radians(p[0]), p[1], p[2], p[3])
        return lambda a: tm_forward(np.asarray(a, dtype=float), *prm)
    from pyproj import CRS, Transformer
    tr = Transformer.from_crs(CRS.from_epsg(4326), CRS.from_epsg(epsg), always_xy=True)
    return lambda a: np.column_stack(tr.transform(np.asarray(a)[:, 0], np.asarray(a)[:, 1]))


def point_in_polygon(lon: np.ndarray, lat: np.ndarray, poly: list[list[float]]) -> np.ndarray:
    """Vectorised even–odd test of many points against one ring."""
    p = np.asarray(poly, dtype=float)
    inside = np.zeros(len(lon), dtype=bool)
    xj, yj = p[-1]
    for xi, yi in p:
        cond = ((yi > lat) != (yj > lat)) & (lon < (xj - xi) * (lat - yi) / ((yj - yi) or 1e-300) + xi)
        inside ^= cond
        xj, yj = xi, yi
    return inside


# --------------------------------------------------------------------------
# GeoPackage blob writing (for export)
# --------------------------------------------------------------------------

def make_gpkg_blob(srs_id: int, wkb: bytes, env: tuple[float, float, float, float] | None) -> bytes:
    if env is None:
        return b"GP\x00\x01" + struct.pack("<i", srs_id) + wkb
    flags = 0b00000011  # little endian, envelope xy
    return b"GP\x00" + bytes([flags]) + struct.pack("<i4d", srs_id, env[0], env[1], env[2], env[3]) + wkb
