"""Getting data into data layers: downloading a layer from an ArcGIS map/feature
service (Skogsstyrelsen's AI-detected remains and Skog och historia are built in),
and importing files – CSV, GeoJSON, KML/KMZ, GPX, GeoPackage and shapefiles (also zipped).

Everything ends up as records (lon, lat, attributes, geometry or None) for
map layers. Coordinates are converted to WGS 84 lon/lat."""
from __future__ import annotations

import csv
import io
import json
import re
import struct
import time
import urllib.parse
import urllib.request
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from xml.etree import ElementTree as ET

import numpy as np

USER_AGENT = "MapMaker (desktop application)"
SKS = "https://geodpags.skogsstyrelsen.se/arcgis/rest/services/"

# ---------------------------------------------------------------------- catalogue
CATALOGUE = [
    dict(key="kolbotten_sdl", name="Charcoal kiln bases – AI (Skogsdatalabbet)", item_name="Charcoal kiln",
         url=SKS + "Skogsdatalabbet/SkogsdatalabbetVisaKolbottenEjValiderad_4_0/MapServer/0",
         score_field="Det_Prob", approx="139 000 points", color="#c08cff",
         register_words=("kolning", "kolbott", "kolmil"),
         attribution="© Skogsstyrelsen (Skogsdatalabbet)",
         description="Charcoal kiln bases (kolbottnar) found by Skogsstyrelsen's AI model in the national laser "
                     "elevation model – the layer in the Skogsdatalabbet map. Not checked in the field. "
                     "Det_Prob is the model's confidence (0–1)."),
    dict(key="kolbotten_dvis", name="Charcoal kiln bases – AI (Skogsstyrelsen, newer and larger)",
         item_name="Charcoal kiln", url=SKS + "Geodataportal/GeodataportalVisaDVIS/MapServer/2",
         score_field="Indikationsvarde", approx="305 000 points", color="#b48cff",
         register_words=("kolning", "kolbott", "kolmil"), attribution="© Skogsstyrelsen",
         description="Charcoal kiln bases from Skogsstyrelsen's DVIS project (indications of cultural remains in "
                     "laser data). Not checked in the field. Indikationsvarde = how strong the indication is."),
    dict(key="fangstgrop_ai", name="Hunting pits – AI (Skogsstyrelsen)", item_name="Hunting pit",
         url=SKS + "Geodataportal/GeodataportalVisaDVIS/MapServer/1", approx="199 000 points", color="#ff8a65",
         register_words=("fångstgrop", "fangstgrop", "fångst"), attribution="© Skogsstyrelsen",
         description="Hunting pits (fångstgropar) found with AI in laser data (DVIS project)."),
    dict(key="aker_ai", name="Prehistoric field boundaries – AI (lines)", item_name="Field boundary",
         url=SKS + "Geodataportal/GeodataportalVisaDVIS/MapServer/3", approx="lines – large, best for a limited area",
         color="#ffd54f", register_words=("fossil åker", "röjningsröse", "hägnad"), attribution="© Skogsstyrelsen",
         description="Possible prehistoric field boundaries (stone walls, lynchets) found with AI in laser data. "
                     "Not checked in the field. Large: download it for the map view or a drawn area."),
    dict(key="skoghistoria_p", name="Skog och historia – points", item_name="Remain", title_field="Sakord",
         url=SKS + "Geodataportal/GeodataportalVisaSkoghistoria/MapServer/0", approx="points", color="#81c784",
         attribution="© Skogsstyrelsen",
         description="Remains recorded by Skogsstyrelsen's Skog och historia inventories – many are not in the "
                     "national register."),
    dict(key="skoghistoria_l", name="Skog och historia – lines", item_name="Remain", title_field="Sakord",
         url=SKS + "Geodataportal/GeodataportalVisaSkoghistoria/MapServer/1", approx="lines", color="#a5d6a7",
         attribution="© Skogsstyrelsen", description="Linear remains from Skog och historia (walls, roads, ditches…)."),
    dict(key="skoghistoria_y", name="Skog och historia – areas", item_name="Remain", title_field="Sakord",
         url=SKS + "Geodataportal/GeodataportalVisaSkoghistoria/MapServer/2", approx="areas", color="#66bb6a",
         attribution="© Skogsstyrelsen", description="Area remains from Skog och historia (fields, settlements…)."),
]
CATALOGUE_BY_KEY = {c["key"]: c for c in CATALOGUE}


class Cancelled(Exception):
    pass


# ---------------------------------------------------------------------- ArcGIS REST
def _http(url: str, data: dict | None = None, timeout: int = 90) -> bytes:
    body = urllib.parse.urlencode(data).encode() if data is not None else None
    req = urllib.request.Request(url, data=body, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def _json(url: str, data: dict | None = None, retries: int = 3, timeout: int = 90) -> dict:
    last = None
    for attempt in range(retries):
        try:
            d = json.loads(_http(url, data, timeout).decode("utf-8"))
            if isinstance(d, dict) and "error" in d:
                raise RuntimeError(f"The service answered: {d['error'].get('message') or d['error']}")
            return d
        except RuntimeError:
            raise
        except Exception as e:
            last = e
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"Could not reach the service: {last}")


def normalise_layer_url(url: str) -> str:
    """Accept a layer URL with or without /query and query parameters."""
    u = url.strip().split("?")[0].rstrip("/")
    if u.endswith("/query"):
        u = u[:-6]
    if not re.search(r"/(MapServer|FeatureServer)/\d+$", u):
        raise ValueError("Give the address of one layer, ending in …/MapServer/<number> or …/FeatureServer/<number>.")
    return u


SQL_TYPES = {"esriFieldTypeOID": "INTEGER", "esriFieldTypeInteger": "INTEGER", "esriFieldTypeSmallInteger": "INTEGER",
             "esriFieldTypeBigInteger": "INTEGER", "esriFieldTypeDouble": "REAL", "esriFieldTypeSingle": "REAL"}


class ArcGISLayer:
    def __init__(self, url: str):
        self.url = normalise_layer_url(url)
        self._info = None

    def info(self) -> dict:
        if self._info is None:
            self._info = _json(self.url + "?f=json")
        return self._info

    @property
    def name(self) -> str:
        return self.info().get("name") or self.url.rsplit("/", 2)[-2]

    @property
    def oid_field(self) -> str:
        i = self.info()
        if i.get("objectIdField"):
            return i["objectIdField"]
        return next((f["name"] for f in i.get("fields", []) if f.get("type") == "esriFieldTypeOID"), "OBJECTID")

    def fields(self) -> list[tuple[str, str]]:
        out = []
        for f in self.info().get("fields") or []:
            t = f.get("type", "")
            if t in ("esriFieldTypeGeometry", "esriFieldTypeBlob", "esriFieldTypeRaster"):
                continue
            out.append((f["name"], SQL_TYPES.get(t, "TEXT")))
        return out

    def _filter(self, where: str, bbox) -> dict:
        q = {"where": where or "1=1", "f": "json"}
        if bbox is not None:
            q.update(geometry=",".join(f"{v:.7f}" for v in bbox), geometryType="esriGeometryEnvelope", inSR="4326",
                     spatialRel="esriSpatialRelIntersects")
        return q

    def count(self, where: str = "1=1", bbox=None) -> int:
        d = _json(self.url + "/query", dict(self._filter(where, bbox), returnCountOnly="true"))
        return int(d.get("count", 0))

    def object_ids(self, where: str = "1=1", bbox=None) -> list[int]:
        d = _json(self.url + "/query", dict(self._filter(where, bbox), returnIdsOnly="true"), timeout=180)
        return sorted(int(i) for i in (d.get("objectIds") or []))

    def features(self, ids: list[int], progress=None, cancelled=None, workers: int = 4):
        """Records for the given object ids, in id order, fetched in parallel chunks."""
        size = int(self.info().get("maxRecordCount") or 1000)
        size = max(100, min(size, 2000))
        chunks = [ids[k:k + size] for k in range(0, len(ids), size)]
        date_fields = {f["name"] for f in self.info().get("fields") or [] if f.get("type") == "esriFieldTypeDate"}
        url = self.url + "/query"

        def get(chunk):
            if cancelled and cancelled():
                raise Cancelled()
            d = _json(url, {"objectIds": ",".join(map(str, chunk)), "outFields": "*", "returnGeometry": "true",
                            "outSR": "4326", "f": "json"}, retries=4, timeout=120)
            return d.get("features") or []

        done = 0
        ex = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="arcgis")
        futs = [ex.submit(get, c) for c in chunks]
        try:
            for f in futs:
                feats = f.result()
                for ft in feats:
                    rec = esri_record(ft, date_fields)
                    if rec is not None:
                        yield rec
                done += len(feats)
                if progress:
                    progress(done, len(ids))
                if cancelled and cancelled():
                    raise Cancelled()
        finally:
            ex.shutdown(wait=False, cancel_futures=True)


def esri_record(ft: dict, date_fields=()):
    a = dict(ft.get("attributes") or {})
    for k in date_fields:
        v = a.get(k)
        if isinstance(v, (int, float)):
            try:
                a[k] = time.strftime("%Y-%m-%d", time.gmtime(v / 1000))
            except (OverflowError, OSError, ValueError):
                pass
    g = ft.get("geometry") or {}
    if "x" in g and "y" in g:
        if g["x"] is None or g["y"] is None or (isinstance(g["x"], str)):
            return None
        return (float(g["x"]), float(g["y"]), a, None)
    if g.get("points"):
        p = g["points"][0]
        return (float(p[0]), float(p[1]), a, None)
    if g.get("paths"):
        paths = [[[float(c[0]), float(c[1])] for c in path] for path in g["paths"] if path]
        geom = ("LineString", paths[0]) if len(paths) == 1 else ("MultiLineString", paths)
        x, y = rep_point(geom)
        return (x, y, a, geom)
    if g.get("rings"):
        rings = [[[float(c[0]), float(c[1])] for c in r] for r in g["rings"] if r]
        geom = ("Polygon", rings)          # all rings in one path; drawn with the even–odd rule
        x, y = rep_point(geom)
        return (x, y, a, geom)
    # attributes only: use coordinate fields if there are any
    xy = coord_fields(a)
    if xy is not None:
        return (xy[0], xy[1], a, None)
    return None


def coord_fields(a: dict):
    lon = next((a[k] for k in a if k.lower() in ("longitude", "lon", "long")), None)
    lat = next((a[k] for k in a if k.lower() in ("latitude", "lat")), None)
    try:
        return (float(lon), float(lat)) if lon is not None and lat is not None else None
    except (TypeError, ValueError):
        return None


def rep_point(geom) -> tuple[float, float]:
    """A point to stand for a line (its middle vertex) or polygon (centroid of the largest ring)."""
    t, c = geom
    if t == "LineString":
        m = c[len(c) // 2]
        return float(m[0]), float(m[1])
    if t == "MultiLineString":
        longest = max(c, key=len)
        m = longest[len(longest) // 2]
        return float(m[0]), float(m[1])
    rings = c if t == "Polygon" else [r for poly in c for r in poly]
    best, bc = -1.0, None
    for r in rings:
        a = np.asarray(r, float)
        if len(a) < 3:
            continue
        x, y = a[:, 0] - a[0, 0], a[:, 1] - a[0, 1]
        x1, y1 = np.roll(x, -1), np.roll(y, -1)
        cr = x * y1 - x1 * y
        area = cr.sum() / 2
        if abs(area) < 1e-14:
            cxy = a.mean(0)
        else:
            cxy = np.array([((x + x1) * cr).sum() / (6 * area) + a[0, 0], ((y + y1) * cr).sum() / (6 * area) + a[0, 1]])
        if abs(area) > best:
            best, bc = abs(area), cxy
    if bc is None:
        a = np.asarray([p for r in rings for p in r], float)
        bc = a.mean(0)
    return float(bc[0]), float(bc[1])


def download(url: str, *, where: str = "1=1", bbox=None, polygon=None, progress=None, cancelled=None):
    """Download an ArcGIS layer (or the part in bbox / polygon): (records list, fields, layer name)."""
    layer = ArcGISLayer(url)
    if progress:
        progress(0, 0, "Asking the service what it has…")
    ids = layer.object_ids(where, bbox)
    if not ids:
        raise RuntimeError("The service returned no features for this area.")

    def prog(done, total):
        if progress:
            progress(done, total, f"Downloading… {done:,} of {total:,}".replace(",", " "))

    recs = list(layer.features(ids, prog, cancelled))
    if polygon is not None:
        from .geo import point_in_polygon
        recs = [r for r in recs if point_in_polygon(np.array([r[0]]), np.array([r[1]]), polygon)[0]]
    return recs, layer.fields(), layer.name


# ---------------------------------------------------------------------- files
FILE_FILTER = ("Data files (*.csv *.txt *.geojson *.json *.kml *.kmz *.gpx *.gpkg *.shp *.zip);;"
               "CSV (*.csv *.txt);;GeoJSON (*.geojson *.json);;KML / KMZ (*.kml *.kmz);;GPX (*.gpx);;"
               "GeoPackage (*.gpkg);;Shapefile (*.shp *.zip)")


def file_layers(path) -> list[tuple[str, str, int]]:
    """Layers a file holds: [(layer name, geometry type, count)] – several only for GeoPackages / zips."""
    p = Path(path)
    ext = p.suffix.lower()
    if ext == ".gpkg":
        from .gpkg import discover
        return [(d["table"], d["gtype"] or "", d["n"]) for d in discover([p])]
    if ext == ".zip":
        with zipfile.ZipFile(p) as z:
            return [(n, "shapefile", 0) for n in z.namelist() if n.lower().endswith(".shp")]
    return [(p.stem, ext.lstrip("."), 0)]


def read_file(path, layer: str | None = None):
    """(records, fields or None, suggested name) for a data file."""
    p = Path(path)
    ext = p.suffix.lower()
    if ext in (".csv", ".txt"):
        recs, fields = read_csv(p)
    elif ext in (".geojson", ".json"):
        recs, fields = read_geojson(p.read_bytes()), None
    elif ext in (".kml", ".kmz"):
        recs, fields = read_kml(p), None
    elif ext == ".gpx":
        recs, fields = read_gpx(p), None
    elif ext == ".gpkg":
        recs, fields = read_gpkg(p, layer)
    elif ext == ".shp":
        recs, fields = read_shapefile(p.read_bytes(), _sibling(p, ".dbf"), _sibling_text(p, ".prj"))
    elif ext == ".zip":
        recs, fields = read_shapefile_zip(p, layer)
    else:
        raise ValueError(f"MapMaker cannot read {ext} files.")
    name = layer.rsplit("/", 1)[-1].rsplit(".", 1)[0] if layer and ext in (".gpkg", ".zip") else p.stem
    return recs, fields, name


# -- CSV
LAT_NAMES = ("lat", "latitude", "latitud", "y_wgs84", "wgs84_lat", "lat_wgs84")
LON_NAMES = ("lon", "lng", "long", "longitude", "longitud", "x_wgs84", "wgs84_lon", "lon_wgs84")
N_NAMES = ("n", "nord", "north", "northing", "y", "sweref99n", "sweref99_n", "sweref99nord", "n_sweref99", "y_sweref",
           "northing_sweref", "sweref_n", "koordinat_n")
E_NAMES = ("e", "ost", "öst", "east", "easting", "x", "sweref99e", "sweref99_e", "sweref99o", "sweref99ost",
           "e_sweref99", "x_sweref", "sweref_e", "koordinat_e", "o")


def _num(v):
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace(" ", "").replace(" ", "")
    if not s:
        return None
    if s.count(",") == 1 and "." not in s:
        s = s.replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


def _typed(v):
    n = _num(v)
    if n is None:
        return v if v not in ("",) else None
    return int(n) if n.is_integer() and re.fullmatch(r"-?\d+", str(v).strip()) else n


def read_csv(p: Path):
    raw = p.read_bytes()
    for enc in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    sample = text[:20000]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
    except csv.Error:
        class dialect(csv.excel):
            delimiter = ";" if sample.count(";") > sample.count(",") else ","
    rows = list(csv.reader(io.StringIO(text), dialect))
    if len(rows) < 2:
        raise ValueError("The file has no rows.")
    head = [h.strip() for h in rows[0]]
    low = [h.lower().replace(" ", "").replace("-", "_") for h in head]

    def find(names):
        return next((i for i, h in enumerate(low) if h in names), None)
    ilat, ilon = find(LAT_NAMES), find(LON_NAMES)
    epsg = 4326
    if ilat is None or ilon is None:
        ilat, ilon = find(N_NAMES), find(E_NAMES)
        epsg = None
    if ilat is None or ilon is None:
        raise ValueError("Could not find coordinate columns. Name them lat/lon (WGS 84) or N/E (SWEREF 99 TM).")
    data = [r for r in rows[1:] if any(c.strip() for c in r)]
    ys = np.array([_num(r[ilat]) if ilat < len(r) else None for r in data], dtype=float)
    xs = np.array([_num(r[ilon]) if ilon < len(r) else None for r in data], dtype=float)
    if epsg is None or np.nanmedian(np.abs(ys)) > 1000:
        epsg = guess_projected(xs, ys)
    lonlat = to_wgs84(np.column_stack([xs, ys]), epsg)
    fields = [(h or f"col{i + 1}", "TEXT") for i, h in enumerate(head)]

    def recs():
        for r, (lo, la) in zip(data, lonlat):
            a = {fields[i][0]: _typed(r[i]) if i < len(r) else None for i in range(len(head))}
            yield (None if not np.isfinite(lo) else float(lo), None if not np.isfinite(la) else float(la), a, None)
    # guess types from the values
    sample_recs = list(recs())
    fields = [(n, _col_type([s[2].get(n) for s in sample_recs[:500]])) for n, _ in fields]
    return iter(sample_recs), fields


def guess_projected(xs, ys) -> int:
    """EPSG code for metre coordinates in Sweden: SWEREF 99 TM or RT 90 2.5 gon V."""
    x, y = np.nanmedian(xs), np.nanmedian(ys)
    if 6.0e6 < y < 7.8e6 and 2.0e5 < x < 1.0e6:
        return 3006
    if 6.0e6 < y < 7.8e6 and 1.1e6 < x < 1.95e6:
        return 3021
    if 6.0e6 < x < 7.8e6 and 2.0e5 < y < 1.0e6:
        raise ValueError("The north and east columns seem to be swapped.")
    raise ValueError("Could not tell which coordinate system the coordinates are in (expected WGS 84 or SWEREF 99 TM).")


def to_wgs84(xy: np.ndarray, epsg: int | None) -> np.ndarray:
    from . import geo
    if epsg in (4326, None):
        return np.asarray(xy, float)
    ok = np.isfinite(xy).all(1)
    out = np.full(xy.shape, np.nan)
    if ok.any():
        out[ok] = geo.get_to_wgs84(epsg)(xy[ok])
    return out


# -- GeoJSON
def _gj_geom(g):
    """GeoJSON geometry -> (lon, lat, geometry or None)."""
    if not g:
        return None
    t, c = g.get("type"), g.get("coordinates")
    if t == "Point":
        return float(c[0]), float(c[1]), None
    if t == "MultiPoint":
        return float(c[0][0]), float(c[0][1]), None
    if t in ("LineString", "MultiLineString", "Polygon"):
        geom = (t, c)
        x, y = rep_point(geom)
        return x, y, geom
    if t == "MultiPolygon":
        geom = ("Polygon", [r for poly in c for r in poly])
        x, y = rep_point(geom)
        return x, y, geom
    if t == "GeometryCollection":
        for gg in g.get("geometries") or []:
            r = _gj_geom(gg)
            if r:
                return r
    return None


def read_geojson(data: bytes):
    d = json.loads(data.decode("utf-8-sig"))
    feats = d.get("features") if d.get("type") == "FeatureCollection" else [d]
    crs = json.dumps(d.get("crs") or {})
    epsg = 3006 if "3006" in crs else None
    out = []
    for f in feats:
        r = _gj_geom(f.get("geometry"))
        if r is None:
            continue
        x, y, geom = r
        out.append((x, y, dict(f.get("properties") or {}), geom))
    if epsg == 3006 or (out and abs(out[0][1]) > 1000):
        out = _project_records(out, 3006 if epsg == 3006 else guess_projected(
            np.array([r[0] for r in out[:200]]), np.array([r[1] for r in out[:200]])))
    return iter(out)


def _project_records(recs, epsg):
    from . import geo
    f = geo.get_to_wgs84(epsg)
    out = []
    for x, y, a, geom in recs:
        lo, la = f(np.array([[x, y]]))[0]
        if geom is not None:
            geom = (geom[0], _map_nested(geom[1], f))
        out.append((float(lo), float(la), a, geom))
    return out


def _map_nested(c, f):
    if c and isinstance(c[0], (int, float)):
        return [float(v) for v in f(np.array([c[:2]], float))[0]]
    if c and c[0] and isinstance(c[0][0], (int, float)):
        return [list(map(float, p)) for p in f(np.array([p[:2] for p in c], float))]
    return [_map_nested(x, f) for x in c]


# -- KML / KMZ
def _strip_ns(root):
    for el in root.iter():
        if isinstance(el.tag, str) and "}" in el.tag:
            el.tag = el.tag.split("}", 1)[1]
    return root


def _kml_coords(text: str):
    pts = []
    for tok in (text or "").replace("\n", " ").split():
        parts = tok.split(",")
        if len(parts) >= 2:
            try:
                pts.append([float(parts[0]), float(parts[1])])
            except ValueError:
                pass
    return pts


def read_kml(p: Path):
    data = p.read_bytes()
    if p.suffix.lower() == ".kmz":
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            name = next((n for n in z.namelist() if n.lower().endswith(".kml")), None)
            if name is None:
                raise ValueError("The KMZ file has no KML inside.")
            data = z.read(name)
    root = _strip_ns(ET.fromstring(data))
    out = []
    for pm in root.iter("Placemark"):
        a = {"name": (pm.findtext("name") or "").strip()}
        desc = (pm.findtext("description") or "").strip()
        if desc:
            a["description"] = re.sub(r"<[^>]+>", " ", desc).strip()[:2000]
        for d in pm.iter("Data"):
            a[d.get("name") or "data"] = (d.findtext("value") or "").strip()
        for sd in pm.iter("SimpleData"):
            a[sd.get("name") or "data"] = (sd.text or "").strip()
        pt = pm.find(".//Point/coordinates")
        if pt is not None:
            c = _kml_coords(pt.text)
            if c:
                out.append((c[0][0], c[0][1], a, None))
            continue
        lines = [_kml_coords(ls.findtext("coordinates")) for ls in pm.iter("LineString")]
        polys = []
        for pg in pm.iter("Polygon"):
            for b in pg.iter("LinearRing"):
                polys.append(_kml_coords(b.findtext("coordinates")))
        if polys:
            geom = ("Polygon", [r for r in polys if r])
        elif lines:
            lines = [ln for ln in lines if ln]
            geom = ("LineString", lines[0]) if len(lines) == 1 else ("MultiLineString", lines)
        else:
            continue
        x, y = rep_point(geom)
        out.append((x, y, a, geom))
    return iter(out)


# -- GPX
def read_gpx(p: Path):
    root = _strip_ns(ET.fromstring(p.read_bytes()))
    out = []
    for w in root.iter("wpt"):
        a = {k: (w.findtext(k) or "").strip() for k in ("name", "desc", "cmt", "time", "sym", "type")}
        a = {k: v for k, v in a.items() if v}
        ele = w.findtext("ele")
        if ele:
            a["ele"] = _num(ele)
        out.append((float(w.get("lon")), float(w.get("lat")), a, None))
    for kind in ("trk", "rte"):
        for t in root.iter(kind):
            segs = []
            for seg in (t.iter("trkseg") if kind == "trk" else [t]):
                pts = [[float(q.get("lon")), float(q.get("lat"))] for q in seg.iter("trkpt" if kind == "trk" else "rtept")]
                if len(pts) >= 2:
                    segs.append(pts)
            if segs:
                geom = ("LineString", segs[0]) if len(segs) == 1 else ("MultiLineString", segs)
                x, y = rep_point(geom)
                out.append((x, y, {"name": (t.findtext("name") or kind).strip(), "kind": kind}, geom))
    return iter(out)


# -- GeoPackage
def read_gpkg(p: Path, table: str | None):
    import sqlite3

    from . import geo
    from .gpkg import discover, resolve_epsg
    layers = discover([p])
    if not layers:
        raise ValueError("The GeoPackage has no feature layers.")
    lay = next((d for d in layers if d["table"] == table), layers[0])
    epsg = resolve_epsg(lay["srsdef"], lay["srs"])
    to_wgs = geo.get_to_wgs84(epsg, lay["srsdef"][4] if lay["srsdef"] else None)
    cols = [c for c, _ in lay["cols"]]
    fields = [(c, t if t in ("INTEGER", "REAL") else "TEXT") for c, t in lay["cols"]]

    def recs():
        con = sqlite3.connect(f"file:{p.as_posix()}?mode=ro", uri=True)
        try:
            sel = ", ".join([f'"{lay["geom"]}"'] + [f'"{c}"' for c in cols])
            for row in con.execute(f'SELECT {sel} FROM "{lay["table"]}"'):
                _srs, wkb = geo.split_gpkg_blob(row[0])
                g = geo.parse_wkb(wkb)
                if g is None:
                    continue
                g = geo.transform_geom_fast(g, to_wgs)
                a = {c: (v if not isinstance(v, bytes) else None) for c, v in zip(cols, row[1:])}
                if g[0] == "Point":
                    out = (float(g[1][0]), float(g[1][1]), a, None)
                elif g[0] == "MultiPoint":
                    d0 = g[1][0][1]
                    out = (float(d0[0]), float(d0[1]), a, None)
                else:
                    geom = _internal_to_nested(g)
                    if geom is None:
                        continue
                    x, y = rep_point(geom)
                    out = (x, y, a, geom)
                yield out
        finally:
            con.close()
    return recs(), fields


def _internal_to_nested(g):
    """geo.parse_wkb geometry -> (type, nested lists) as stored in datasets."""
    t, d = g
    if d is None:
        return None
    if t == "LineString":
        return ("LineString", np.asarray(d).tolist())
    if t == "Polygon":
        return ("Polygon", [np.asarray(r).tolist() for r in d])
    if t == "MultiLineString":
        return ("MultiLineString", [np.asarray(p[1]).tolist() for p in d if p[1] is not None])
    if t == "MultiPolygon":
        return ("Polygon", [np.asarray(r).tolist() for p in d if p[1] for r in p[1]])
    return None


# -- shapefile
def _sibling(p: Path, ext: str) -> bytes | None:
    for e in (ext, ext.upper()):
        q = p.with_suffix(e)
        if q.exists():
            return q.read_bytes()
    return None


def _sibling_text(p: Path, ext: str) -> str | None:
    b = _sibling(p, ext)
    return b.decode("latin-1") if b else None


def read_shapefile_zip(p: Path, member: str | None):
    with zipfile.ZipFile(p) as z:
        names = z.namelist()
        shp = member or next((n for n in names if n.lower().endswith(".shp")), None)
        if shp is None:
            raise ValueError("The zip file has no shapefile (.shp) in it.")
        stem = shp[:-4]

        def part(ext):
            n = next((x for x in names if x.lower() == (stem + ext).lower()), None)
            return z.read(n) if n else None
        prj = part(".prj")
        return read_shapefile(z.read(shp), part(".dbf"), prj.decode("latin-1") if prj else None)


def prj_epsg(prj: str | None) -> int:
    if not prj:
        return 3006          # Swedish data without .prj is nearly always SWEREF 99 TM; checked below
    t = prj.upper().replace(" ", "_")
    if "SWEREF" in t and "TM" in t:
        return 3006
    if "RT90" in t or "RT_90" in t:
        return 3021
    if t.startswith("GEOGCS") and ("WGS" in t or "ETRS" in t or "SWEREF" in t):
        return 4326
    m = re.search(r'AUTHORITY\["EPSG","(\d+)"\]\]$', prj.strip())
    if m:
        return int(m.group(1))
    raise ValueError("The shapefile's coordinate system (.prj) is not SWEREF 99 TM, RT 90 or WGS 84.")


def read_dbf(data: bytes | None) -> tuple[list[tuple[str, str]], list[dict]]:
    if not data:
        return [], []
    n = struct.unpack("<I", data[4:8])[0]
    hlen, rlen = struct.unpack("<HH", data[8:12])
    fields = []
    pos = 32
    while data[pos] != 0x0D:
        name = data[pos:pos + 11].split(b"\x00")[0].decode("latin-1")
        ftype = chr(data[pos + 11])
        flen, fdec = data[pos + 16], data[pos + 17]
        fields.append((name, ftype, flen, fdec))
        pos += 32
    enc = "utf-8"
    rows = []
    for i in range(n):
        off = hlen + i * rlen
        rec = data[off:off + rlen]
        if not rec or rec[0:1] == b"*":
            rows.append(None)
            continue
        p, row = 1, {}
        for name, ftype, flen, fdec in fields:
            raw = rec[p:p + flen]
            p += flen
            try:
                s = raw.decode(enc).strip()
            except UnicodeDecodeError:
                s = raw.decode("cp1252", errors="replace").strip()
            if ftype in "NF":
                v = _num(s)
                row[name] = None if v is None else (int(v) if fdec == 0 and v.is_integer() else v)
            elif ftype == "L":
                row[name] = s.upper() in ("Y", "T") if s else None
            else:
                row[name] = s or None
        rows.append(row)
    sql = [(name, "INTEGER" if t in "NF" and d == 0 else "REAL" if t in "NF" else "TEXT") for name, t, _, d in fields]
    return sql, rows


def read_shapefile(shp: bytes, dbf: bytes | None, prj: str | None):
    fields, rows = read_dbf(dbf)
    epsg = prj_epsg(prj)
    stype = struct.unpack("<i", shp[32:36])[0]
    pos, k = 100, 0
    recs = []
    while pos + 8 <= len(shp):
        clen = struct.unpack(">i", shp[pos + 4:pos + 8])[0] * 2
        body = shp[pos + 8:pos + 8 + clen]
        pos += 8 + clen
        a = rows[k] if k < len(rows) and rows[k] is not None else {}
        k += 1
        if len(body) < 4:
            continue
        t = struct.unpack("<i", body[:4])[0]
        if t == 0:
            continue
        base = t % 10 if t < 30 else t
        if base == 1:
            x, y = struct.unpack("<2d", body[4:20])
            recs.append((x, y, a, None))
        elif base == 8:
            npts = struct.unpack("<i", body[36:40])[0]
            if npts:
                x, y = struct.unpack("<2d", body[40:56])
                recs.append((x, y, a, None))
        elif base in (3, 5):
            nparts, npts = struct.unpack("<2i", body[36:44])
            parts = list(struct.unpack(f"<{nparts}i", body[44:44 + 4 * nparts])) + [npts]
            pts = np.frombuffer(body, dtype="<f8", count=2 * npts, offset=44 + 4 * nparts).reshape(-1, 2)
            segs = [pts[parts[i]:parts[i + 1]].tolist() for i in range(nparts)]
            if base == 3:
                geom = ("LineString", segs[0]) if len(segs) == 1 else ("MultiLineString", segs)
            else:
                geom = ("Polygon", segs)
            x, y = rep_point(geom)
            recs.append((x, y, a, geom))
    if stype == 0 and not recs:
        raise ValueError("The shapefile is empty.")
    if recs and epsg == 3006 and abs(recs[0][1]) < 1000:
        epsg = 4326           # no .prj and degrees: WGS 84
    if epsg != 4326:
        recs = _project_records(recs, epsg)
    return iter(recs), fields or None


__all__ = ["CATALOGUE", "CATALOGUE_BY_KEY", "ArcGISLayer", "download", "read_file", "file_layers", "FILE_FILTER",
           "Cancelled", "normalise_layer_url", "rep_point"]


def _col_type(values) -> str:
    """SQL-like type for a column from sample values: INTEGER, REAL or TEXT."""
    seen, is_int = False, True
    for v in values:
        if v is None or v == "" or isinstance(v, bool):
            continue
        seen = True
        if isinstance(v, int):
            continue
        if isinstance(v, float):
            is_int = False
            continue
        return "TEXT"
    return ("INTEGER" if is_int else "REAL") if seen else "TEXT"
