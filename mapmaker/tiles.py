"""Map tiles: sources (XYZ / WMTS / WMS), a disk + memory cache, background
downloading and prefetching, terrain tiles derived from elevation data, and
mosaicking the tiles for a view into one image."""
from __future__ import annotations

import base64
import io
import math
import os
import threading
import time
import urllib.parse
import urllib.request
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np
from PIL import Image

from . import terrain
from .render import View

USER_AGENT = "MapMaker/0.1 (desktop application)"


@dataclass
class TileSource:
    id: str
    name: str
    urls: list[str]
    kind: str = "xyz"            # "xyz" ({z}/{x}/{y}), "wms" (GetMap in EPSG:3857) or "dem" (Terrarium elevation)
    max_zoom: int = 19
    min_zoom: int = 0
    attribution: str = ""
    wms_layers: str = ""
    user: str | None = None
    password: str | None = None
    note: str = ""
    role: str = "base"           # "base" or "overlay"
    user_defined: bool = False
    labels: list[str] = field(default_factory=list)   # optional label tiles drawn on top of a base map
    crs: int = 3857              # WMS only: 3006 = ask the server in SWEREF 99 TM and reproject to map tiles
    tile_size: int = 256         # 512 = the server's tiles are 512 px (each is four 256-px map tiles)

    def url(self, z: int, x: int, y: int) -> str:
        tmpl = self.urls[(x + y) % len(self.urls)]
        if self.kind == "wms":
            n = 2 ** z
            half = 20037508.342789244
            size = 2 * half / n
            minx = -half + x * size
            maxy = half - y * size
            bbox = f"{minx},{maxy - size},{minx + size},{maxy}"
            sep = "&" if "?" in tmpl else "?"
            return (f"{tmpl}{sep}SERVICE=WMS&REQUEST=GetMap&VERSION=1.3.0&LAYERS={urllib.parse.quote(self.wms_layers)}"
                    f"&STYLES=&FORMAT=image/png&TRANSPARENT=true&CRS=EPSG:3857&WIDTH=256&HEIGHT=256&BBOX={bbox}")
        return tmpl.replace("{z}", str(z)).replace("{x}", str(x)).replace("{y}", str(y)).replace("{-y}", str(2 ** z - 1 - y))

    def headers(self) -> dict:
        h = {"User-Agent": USER_AGENT}
        if self.user:
            tok = base64.b64encode(f"{self.user}:{self.password or ''}".encode()).decode()
            h["Authorization"] = "Basic " + tok
        return h

    def label_source(self) -> "TileSource | None":
        if not self.labels:
            return None
        return TileSource(self.id + "-labels", self.name + " labels", self.labels, max_zoom=self.max_zoom,
                          role="overlay")

    def to_dict(self):
        return asdict(self)


OSM_ATTR = "© OpenStreetMap contributors"
ESRI = "https://server.arcgisonline.com/ArcGIS/rest/services/"

BASEMAPS = [
    TileSource("esri-light", "Light grey canvas (Esri)", [ESRI + "Canvas/World_Light_Gray_Base/MapServer/tile/{z}/{y}/{x}"],
               max_zoom=16, attribution="© Esri, HERE, Garmin, OpenStreetMap contributors",
               labels=[ESRI + "Canvas/World_Light_Gray_Reference/MapServer/tile/{z}/{y}/{x}"],
               note="Quiet and neutral – lets your own symbols and labels stand out"),
    TileSource("esri-dark", "Dark grey canvas (Esri)", [ESRI + "Canvas/World_Dark_Gray_Base/MapServer/tile/{z}/{y}/{x}"],
               max_zoom=16, attribution="© Esri, HERE, Garmin, OpenStreetMap contributors",
               labels=[ESRI + "Canvas/World_Dark_Gray_Reference/MapServer/tile/{z}/{y}/{x}"],
               note="Dark background – bright symbols glow on it"),
    TileSource("esri-topo", "Topographic (Esri World Topo)", [ESRI + "World_Topo_Map/MapServer/tile/{z}/{y}/{x}"],
               max_zoom=19, attribution="© Esri, HERE, Garmin, FAO, NOAA, USGS, OpenStreetMap contributors",
               note="Detailed general reference map"),
    TileSource("opentopo", "Contours (OpenTopoMap)", ["https://a.tile.opentopomap.org/{z}/{x}/{y}.png",
               "https://b.tile.opentopomap.org/{z}/{x}/{y}.png", "https://c.tile.opentopomap.org/{z}/{x}/{y}.png"],
               max_zoom=17, attribution=OSM_ATTR + ", SRTM | © OpenTopoMap (CC-BY-SA)", note="Contour lines and terrain"),
    TileSource("osm", "OpenStreetMap", ["https://tile.openstreetmap.org/{z}/{x}/{y}.png"], max_zoom=19, attribution=OSM_ATTR),
    TileSource("esri-hillshade", "Hillshade (Esri)", [ESRI + "Elevation/World_Hillshade/MapServer/tile/{z}/{y}/{x}"],
               max_zoom=16, attribution="© Esri, Airbus DS, USGS, NGA, NASA, CGIAR, NLS, OS, NMA, Geodatastyrelsen",
               note="Grey shaded relief of the whole world"),
    TileSource("esri-ocean", "Oceans (Esri)", [ESRI + "Ocean/World_Ocean_Base/MapServer/tile/{z}/{y}/{x}"],
               max_zoom=13, attribution="© Esri, GEBCO, NOAA, National Geographic, DeLorme, HERE, Geonames.org",
               labels=[ESRI + "Ocean/World_Ocean_Reference/MapServer/tile/{z}/{y}/{x}"],
               note="Sea floor relief and soft land colours"),
    TileSource("satellite", "Satellite (Esri World Imagery)", [ESRI + "World_Imagery/MapServer/tile/{z}/{y}/{x}"],
               max_zoom=19, attribution="Imagery © Esri, Maxar, Earthstar Geographics",
               labels=[ESRI + "Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}"]),
]
BASEMAPS_BY_ID = {b.id: b for b in BASEMAPS}
DEFAULT_BASEMAP = "esri-light"

# Global elevation, Terrarium encoding (AWS Open Data "Terrain Tiles"); ~10–30 m in Sweden
DEM = TileSource("terrarium", "Terrain Tiles (AWS)", ["https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png"],
                 kind="dem", max_zoom=15, attribution="Elevation: Terrain Tiles (Mapzen/AWS)", role="overlay")
DEM_MAX_ZOOM = 13   # finer terrain tiles add nothing at this data resolution

# Lantmäteriet's 1 m laser elevation model (Markhöjdmodell, CC0), served openly as Terrarium tiles by
# Mapterhorn (512 px WebP, 1 m in Sweden down to zoom 16 = map tiles of zoom 17). Other countries:
# Mapterhorn's best available open data.
LIDAR_DEM = TileSource("mapterhorn", "Laser elevation 1 m (Lantmäteriet, via Mapterhorn)",
                       ["https://tiles.mapterhorn.com/{z}/{x}/{y}.webp"], kind="dem", max_zoom=16,
                       attribution="Elevation © Lantmäteriet (CC0) via Mapterhorn", role="overlay", tile_size=512)
LIDAR_MAX_ZOOM = 17   # in 256-px map tiles

# Surface water 1984-2020 from Landsat, 30 m (EC JRC / Google, Copernicus, free of restriction).
# Transparent = never water; colour runs from red (rarely water) to blue (always water).
WATER = TileSource("gsw-occurrence", "Surface water (JRC Global Surface Water)",
                   ["https://storage.googleapis.com/global-surface-water/tiles2021/occurrence/{z}/{x}/{y}.png"],
                   max_zoom=13, attribution="Surface water: EC JRC/Google", role="overlay")
EMPTY_TILE = np.zeros((256, 256, 4), np.uint8)


def _write_atomic(p: Path, data: bytes):
    """Write via a temporary file, so another thread never reads a half-written tile."""
    tmp = p.with_name(f"{p.name}.{threading.get_ident()}.tmp")
    try:
        tmp.write_bytes(data)
        os.replace(tmp, p)
    except OSError:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass


def _remove(p: Path):
    """Delete a broken cache file; on Windows it may be open in another thread, then leave it."""
    try:
        p.unlink(missing_ok=True)
    except OSError:
        pass


def sweref_wms_tile(src: TileSource, z: int, x: int, y: int, size: int = 256) -> np.ndarray:
    """A web-map tile from a WMS that only serves SWEREF 99 TM (e.g. Lantmäteriet's services): request the
    SWEREF box around the tile and resample it onto the tile's pixels."""
    from . import geo
    n = 2 ** z
    px = (np.arange(size) + 0.5) / size
    PX, PY = np.meshgrid((x + px) / n, (y + px) / n)
    lon = PX * 360.0 - 180.0
    lat = np.degrees(np.arctan(np.sinh(math.pi * (1 - 2 * PY))))
    xy = geo.get_from_wgs84(3006)(np.column_stack([lon.ravel(), lat.ravel()]))
    xmin, xmax = xy[:, 0].min(), xy[:, 0].max()
    ymin, ymax = xy[:, 1].min(), xy[:, 1].max()
    padx, pady = (xmax - xmin) * 0.02, (ymax - ymin) * 0.02
    xmin, xmax, ymin, ymax = xmin - padx, xmax + padx, ymin - pady, ymax + pady
    W = H = int(size * 1.25)
    tmpl = src.urls[(x + y) % len(src.urls)]
    sep = "&" if "?" in tmpl else "?"
    url = (f"{tmpl}{sep}SERVICE=WMS&REQUEST=GetMap&VERSION=1.1.1&LAYERS={urllib.parse.quote(src.wms_layers)}"
           f"&STYLES=&FORMAT=image/png&TRANSPARENT=true&SRS=EPSG:3006&WIDTH={W}&HEIGHT={H}"
           f"&BBOX={xmin:.2f},{ymin:.2f},{xmax:.2f},{ymax:.2f}")
    req = urllib.request.Request(url, headers=src.headers())
    with urllib.request.urlopen(req, timeout=30) as r:
        data = r.read()
    im = Image.open(io.BytesIO(data))
    img = np.asarray(im.convert("RGBA"), dtype=np.uint8)
    h, w = img.shape[:2]
    c = np.clip(((xy[:, 0] - xmin) / (xmax - xmin) * w).astype(np.int64), 0, w - 1)
    r_ = np.clip(((ymax - xy[:, 1]) / (ymax - ymin) * h).astype(np.int64), 0, h - 1)
    return np.ascontiguousarray(img[r_, c].reshape(size, size, 4))


class TileCache:
    """Memory + disk cache. get() never blocks: a missing tile is queued for
    download and `on_ready()` is called (from a worker thread) when it arrives.
    prefetch() fetches tiles the user is likely to need next, at low priority."""

    def __init__(self, cache_dir: Path, on_ready=None, max_mem: int = 400, max_age_days: int = 30, offline: bool = False):
        self.dir = Path(cache_dir)
        self.on_ready = on_ready or (lambda: None)
        self.mem: OrderedDict = OrderedDict()
        self.max_mem = max_mem
        self.max_age = max_age_days * 86400
        self.pending: set = set()
        self.visible: set = set()
        self.failed: dict = {}
        self.errors: dict[str, str] = {}   # source id -> last error message
        self.lock = threading.Lock()
        self.pool = ThreadPoolExecutor(max_workers=8, thread_name_prefix="tiles")
        self.prefetch_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="prefetch")
        self.offline = offline or bool(os.environ.get("MAPMAKER_OFFLINE"))
        self.derived: OrderedDict = OrderedDict()   # computed terrain tiles

    def _path(self, src: TileSource, z, x, y) -> Path:
        return self.dir / src.id / str(z) / str(x) / f"{y}.tile"

    def _decode(self, src: TileSource, data: bytes):
        im = Image.open(io.BytesIO(data))
        if src.kind == "dem":
            a = np.asarray(im.convert("RGB"), dtype=np.float32)
            return a[..., 0] * 256.0 + a[..., 1] + a[..., 2] / 256.0 - 32768.0
        return np.asarray(im.convert("RGBA"), dtype=np.uint8)

    def peek(self, src: TileSource, z, x, y):
        """Only memory/disk, no download."""
        key = (src.id, z, x, y)
        with self.lock:
            if key in self.mem:
                self.mem.move_to_end(key)
                return self.mem[key]
        p = self._path(src, z, x, y)
        if p.exists():
            try:
                arr = self._decode(src, p.read_bytes())
                self._remember(key, arr)
                return arr
            except Exception:
                _remove(p)
        return None

    def get(self, src: TileSource, z, x, y):
        arr = self.peek(src, z, x, y)
        if not self.offline:
            if arr is None:
                self._queue(src, z, x, y)
            else:
                p = self._path(src, z, x, y)
                try:
                    if time.time() - p.stat().st_mtime > self.max_age:
                        self._queue(src, z, x, y)
                except OSError:
                    pass
        return arr

    def _remember(self, key, arr):
        with self.lock:
            self.mem[key] = arr
            self.mem.move_to_end(key)
            while len(self.mem) > self.max_mem:
                self.mem.popitem(last=False)

    def _queue(self, src, z, x, y, pool=None):
        key = (src.id, z, x, y)
        with self.lock:
            if pool is None and key in self.pending:
                self.visible.add(key)   # a prefetch is already fetching it: announce when done
                return
            if key in self.pending or time.time() - self.failed.get(key, 0) < 60:
                return
            self.pending.add(key)
            if pool is None:
                self.visible.add(key)
        (pool or self.pool).submit(self._fetch, src, z, x, y)

    def prefetch(self, src: TileSource, tiles):
        """Download tiles in the background without announcing them."""
        if self.offline:
            return
        for z, x, y in tiles:
            if (src.id, z, x, y) in self.mem or self._path(src, z, x, y).exists():
                continue
            self._queue(src, z, x, y, pool=self.prefetch_pool)

    def _download(self, src: TileSource, z, x, y, remember=True):
        """Download one tile, store it on disk and return it (raises on failure)."""
        if src.kind == "wms" and src.crs == 3006:
            arr = sweref_wms_tile(src, z, x, y)
            buf = io.BytesIO()
            Image.fromarray(arr, "RGBA").save(buf, "PNG")
            data = buf.getvalue()
        else:
            req = urllib.request.Request(src.url(z, x, y), headers=src.headers())
            with urllib.request.urlopen(req, timeout=30) as r:
                data = r.read()
            arr = self._decode(src, data)
        p = self._path(src, z, x, y)
        p.parent.mkdir(parents=True, exist_ok=True)
        _write_atomic(p, data)
        if remember:
            self._remember((src.id, z, x, y), arr)
        return arr

    def fetch_many(self, src: TileSource, tiles, progress=None, cancelled=None, missing_ok=False) -> dict:
        """Blocking: every tile in `tiles` from the cache or the network, {(z, x, y): array or None}.
        For analyses that need a whole area at once. progress(done, total) is called as tiles
        arrive; cancelled() is polled and stops the downloads when it returns True."""
        from concurrent.futures import as_completed
        out, missing = {}, []
        for t in tiles:
            a = self._peek_disk(src, *t)
            if a is None:
                missing.append(t)
            else:
                out[t] = a
        if progress:
            progress(len(out), len(tiles))
        if not missing or self.offline:
            return out
        errors = 0
        with ThreadPoolExecutor(max_workers=8, thread_name_prefix="fetch") as ex:
            futs = {ex.submit(self._download, src, *t, False): t for t in missing}
            for f in as_completed(futs):
                t = futs[f]
                try:
                    out[t] = f.result()
                except Exception as e:
                    out[t] = None
                    if missing_ok and getattr(e, "code", None) == 404:
                        # no tile = nothing there (e.g. no water); remember it as an empty file
                        out[t] = EMPTY_TILE
                        p = self._path(src, *t)
                        p.parent.mkdir(parents=True, exist_ok=True)
                        _write_atomic(p, b"")
                    else:
                        errors += 1
                        self.errors[src.id] = str(e)
                if progress:
                    progress(len(out), len(tiles))
                if cancelled and cancelled():
                    for g in futs:
                        g.cancel()
                    break
        return out

    def _peek_disk(self, src, z, x, y):
        """Like peek() but does not fill the memory cache (for bulk reads)."""
        key = (src.id, z, x, y)
        with self.lock:
            if key in self.mem:
                return self.mem[key]
        p = self._path(src, z, x, y)
        if p.exists():
            try:
                data = p.read_bytes()
                if not data and src.kind != "dem":
                    return EMPTY_TILE   # the server has no tile here (remembered 404)
                return self._decode(src, data)
            except Exception:
                _remove(p)
        return None

    def _fetch(self, src: TileSource, z, x, y):
        key = (src.id, z, x, y)
        try:
            self._download(src, z, x, y)
            self.errors.pop(src.id, None)
            with self.lock:
                announce = key in self.visible
                self.pending.discard(key)
                self.visible.discard(key)
            if announce:
                self.on_ready()
        except Exception as e:  # network errors, 404 outside coverage, bad images…
            with self.lock:
                self.failed[key] = time.time()
            self.errors[src.id] = str(e)
        finally:
            with self.lock:
                self.pending.discard(key)
                self.visible.discard(key)

    def busy(self) -> int:
        """Tiles being downloaded for the current view (prefetching not counted)."""
        return len(self.visible)

    def clear_memory(self, source_id: str | None = None):
        with self.lock:
            for k in [k for k in self.mem if source_id is None or k[0] == source_id]:
                del self.mem[k]

    def shutdown(self):
        self.pool.shutdown(wait=False, cancel_futures=True)
        self.prefetch_pool.shutdown(wait=False, cancel_futures=True)

    # ------------------------------------------------------------ terrain tiles
    def dem256(self, src: TileSource, z, x, y, fetch=True):
        """Elevation for 256-px map tile (z, x, y) from a source with 256- or 512-px tiles; None if not loaded."""
        get = self.get if fetch else self.peek
        if src.tile_size == 256:
            return get(src, z, x, y)
        if z == 0:
            a = get(src, 0, 0, 0)
            return None if a is None else a[::2, ::2]
        nz = min(z - 1, src.max_zoom)
        k = z - 1 - nz                                   # finer than the source: enlarge part of a tile
        a = get(src, nz, x >> (k + 1), y >> (k + 1))
        if a is None:
            return None
        part = 512 >> (k + 1)
        qx, qy = x % (2 ** (k + 1)), y % (2 ** (k + 1))
        sub = a[qy * part:(qy + 1) * part, qx * part:(qx + 1) * part]
        if part != 256:
            sub = np.repeat(np.repeat(sub, 256 // part, 0), 256 // part, 1)
        return sub

    def native_tiles(self, src: TileSource, tiles):
        """The source's own tiles needed for 256-px map tiles [(z, x, y)]."""
        if src.tile_size == 256:
            return list(tiles)
        out = set()
        for z, x, y in tiles:
            if z == 0:
                out.add((0, 0, 0))
                continue
            nz = min(z - 1, src.max_zoom)
            k = z - 1 - nz
            out.add((nz, x >> (k + 1), y >> (k + 1)))
        return sorted(out)

    def _dem_block(self, z, x, y, fetch=True, src: TileSource = None):
        """3x3 DEM tiles around (x, y) as one 768x768 array; None if the centre is missing.
        Returns (array, complete)."""
        src = src or DEM
        n = 2 ** z
        centre = self.dem256(src, z, x % n, y, fetch)
        if centre is None:
            return None, False
        block = np.empty((768, 768), dtype=np.float32)
        complete = True
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                yy = y + dy
                t = centre if dx == 0 and dy == 0 else (self.dem256(src, z, (x + dx) % n, yy, fetch)
                                                        if 0 <= yy < n else None)
                if t is None:
                    complete = complete and not (0 <= yy < n)
                    t = centre  # stand-in, edges only
                block[(dy + 1) * 256:(dy + 2) * 256, (dx + 1) * 256:(dx + 2) * 256] = t
        return block, complete

    def terrain_tile(self, kind: str, params: tuple, z, x, y, fetch=True):
        """Hillshade / relief / sea-level tile computed from the DEM and cached."""
        key = (kind, params, z, x, y)
        with self.lock:
            hit = self.derived.get(key)
            if hit is not None:
                self.derived.move_to_end(key)
                return hit
        if kind == "hillshade":
            block, complete = self._dem_block(z, x, y, fetch)
            if block is None:
                return None
            lat = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * (y + 0.5) / 2 ** z))))
            mpp = 40075016.686 * math.cos(math.radians(lat)) / (256 * 2 ** z)
            strength, exaggeration = params
            tile = terrain.hillshade_rgba(block, mpp, strength, exaggeration)[256:512, 256:512].copy()
        elif kind == "lidar":
            block, complete = self._dem_block(z, x, y, fetch, LIDAR_DEM)
            if block is None:
                return None
            lat = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * (y + 0.5) / 2 ** z))))
            mpp = 40075016.686 * math.cos(math.radians(lat)) / (256 * 2 ** z)
            style, strength, background = params
            m = terrain.lidar_margin(style, mpp)
            part = block[256 - m:512 + m, 256 - m:512 + m]
            tile = terrain.lidar_rgba(part, mpp, style, strength, background)[m:m + 256, m:m + 256].copy()
        else:
            elev = (self.get if fetch else self.peek)(DEM, z, x % 2 ** z, y)
            if elev is None:
                return None
            complete = True
            if kind == "relief":
                tile = terrain.relief_rgba(elev, params[0])
            elif kind == "sealevel":
                tile = terrain.sealevel_rgba(elev, params[1], params[0])
            else:
                raise ValueError(kind)
        if complete:
            with self.lock:
                self.derived[key] = tile
                while len(self.derived) > 600:
                    self.derived.popitem(last=False)
        return tile


def _tile_zoom(zoom: float, zmin: int, zmax: int) -> int:
    return int(max(zmin, min(zmax, math.floor(zoom + 0.35))))


def tiles_for_view(view: View, tz: int):
    """(z, x, y) of the tiles covering a view at tile zoom tz."""
    n = 2 ** tz
    tile_px = 256 * 2.0 ** (view.zoom - tz)
    X0 = view.cx * view.world - view.width / 2
    Y0 = view.cy * view.world - view.height / 2
    tx0, tx1 = int(math.floor(X0 / tile_px)), int(math.floor((X0 + view.width) / tile_px))
    ty0, ty1 = max(0, int(math.floor(Y0 / tile_px))), min(n - 1, int(math.floor((Y0 + view.height) / tile_px)))
    return [(tz, tx % n, ty) for ty in range(ty0, ty1 + 1) for tx in range(tx0, tx1 + 1)]


def mosaic_with(view: View, get, peek, zmin: int, zmax: int, is_float=False):
    """Mosaic tiles given accessor functions get(z,x,y) (may start a download) and
    peek(z,x,y) (cache only, used for lower-zoom stand-ins while tiles load)."""
    W, H = view.width, view.height
    tz = _tile_zoom(view.zoom, zmin, zmax)
    n = 2 ** tz
    tile_px = 256 * 2.0 ** (view.zoom - tz)
    X0 = view.cx * view.world - W / 2
    Y0 = view.cy * view.world - H / 2
    tx0, tx1 = int(math.floor(X0 / tile_px)), int(math.floor((X0 + W) / tile_px))
    ty0, ty1 = max(0, int(math.floor(Y0 / tile_px))), min(n - 1, int(math.floor((Y0 + H) / tile_px)))
    canvas = Image.new("F", (W, H), float("nan")) if is_float else Image.new("RGBA", (W, H), (0, 0, 0, 0))
    size = int(math.ceil(tile_px)) + 1
    found = 0
    for ty in range(ty0, ty1 + 1):
        for tx in range(tx0, tx1 + 1):
            x = tx % n
            arr = get(tz, x, ty)
            crop = None
            if arr is None:  # a lower-zoom tile we already have, enlarged
                for up in range(1, 7):
                    if tz - up < zmin:
                        break
                    parent = peek(tz - up, x >> up, ty >> up)
                    if parent is not None:
                        k = 2 ** up
                        sub = 256 / k
                        ox, oy = (x % k) * sub, (ty % k) * sub
                        crop = (int(round(ox)), int(round(oy)), int(round(ox + sub)), int(round(oy + sub)))
                        arr = parent
                        break
            if arr is None:
                continue
            found += 1
            im = Image.fromarray(arr.astype(np.float32), "F") if is_float else Image.fromarray(arr, "RGBA")
            if crop:
                im = im.crop(crop)
            if im.size != (size, size):
                im = im.resize((size, size), Image.BILINEAR)
            canvas.paste(im, (int(math.floor(tx * tile_px - X0)), int(math.floor(ty * tile_px - Y0))))
    if not found:
        return None
    return np.asarray(canvas, dtype=np.float32 if is_float else np.uint8)


def mosaic(view: View, src: TileSource, cache: TileCache, max_zoom: int | None = None):
    """The tiles of `src` covering `view` as one array (RGBA uint8, or float32 metres for DEM)."""
    if not src.urls:
        return None
    zmax = src.max_zoom if max_zoom is None else min(max_zoom, src.max_zoom)
    return mosaic_with(view, lambda z, x, y: cache.get(src, z, x, y), lambda z, x, y: cache.peek(src, z, x, y),
                       src.min_zoom, zmax, is_float=src.kind == "dem")


def terrain_mosaic(view: View, kind: str, params: tuple, cache: TileCache):
    return mosaic_with(view, lambda z, x, y: cache.terrain_tile(kind, params, z, x, y),
                       lambda z, x, y: cache.terrain_tile(kind, params, z, x, y, fetch=False), 0, DEM_MAX_ZOOM)


def prefetch_around(view: View, src: TileSource, cache: TileCache, max_zoom: int | None = None):
    """Queue the tiles for one zoom level in and out, so zooming has them ready."""
    if not src.urls:
        return
    zmax = src.max_zoom if max_zoom is None else min(max_zoom, src.max_zoom)
    tz = _tile_zoom(view.zoom, src.min_zoom, zmax)
    wanted = []
    for dz in (1, -1):
        z = tz + dz
        if src.min_zoom <= z <= zmax:
            wanted += tiles_for_view(view.moved(zoom=view.zoom + dz), z)[:64]
    cache.prefetch(src, wanted)
