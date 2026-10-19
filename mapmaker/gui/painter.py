"""Drawing a map document with QPainter – shared by the screen canvas and the exports,
so what you see is what you get.

Sizes in documents are in *reference pixels* (1/96 inch, like CSS pixels) and text sizes
in points. A Frame says where the map goes on the painter and how many painter units
make one reference pixel (k): 1 on screen in map view, (page scale) in page view and
dpi/96 when exporting.

Everything is drawn as vectors (paths) except map tiles, so PDFs stay sharp."""
from __future__ import annotations

import json
import math
import threading
from collections import OrderedDict
from dataclasses import dataclass, field

import numpy as np
from PySide6.QtCore import QByteArray, QPointF, QRectF, Qt
from PySide6.QtGui import (QBrush, QColor, QFont, QFontMetricsF, QImage, QPainter, QPainterPath, QPen, QPolygonF,
                           QTransform)
from PySide6.QtSvg import QSvgRenderer

from .. import symbols as SYM
from ..document import PAGE_ELEMENTS, paper_mm
from ..render import View, lonlat_to_merc, merc_to_lonlat
from ..tiles import (BASEMAPS_BY_ID, DEM, DEM_MAX_ZOOM, LIDAR_DEM, LIDAR_MAX_ZOOM, TileCache, TileSource)
from .tileimages import EMPTY, to_qimage

PX_PER_MM = 96 / 25.4          # reference pixels per millimetre
PT = 96 / 72                   # reference pixels per point
MM_PER_PT = 25.4 / 72

DASH_PATTERNS = {"solid": None, "dash": [5.0, 3.0], "dot": [0.8, 2.2], "dashdot": [5.0, 2.5, 0.8, 2.5]}
COMP = {"normal": QPainter.CompositionMode.CompositionMode_SourceOver,
        "multiply": QPainter.CompositionMode.CompositionMode_Multiply,
        "screen": QPainter.CompositionMode.CompositionMode_Screen,
        "overlay": QPainter.CompositionMode.CompositionMode_Overlay,
        "darken": QPainter.CompositionMode.CompositionMode_Darken}
BLENDS = {"normal": "Normal", "multiply": "Multiply", "screen": "Screen", "overlay": "Overlay", "darken": "Darken"}
RASTER_KINDS = ("color", "basemap", "terrain", "service")


def qcolor(h: str | None, alpha: float = 1.0) -> QColor:
    c = QColor(h or "#000000")
    if alpha < 1.0:
        c.setAlphaF(max(0.0, min(1.0, c.alphaF() * alpha)))
    return c


def pen(color, width, dash="solid", cap=Qt.PenCapStyle.RoundCap, join=Qt.PenJoinStyle.RoundJoin, alpha=1.0) -> QPen:
    pe = QPen(qcolor(color, alpha) if isinstance(color, str) else color)
    pe.setWidthF(max(0.01, float(width)))
    pe.setCapStyle(Qt.PenCapStyle.FlatCap if dash not in ("solid", None) and dash != "dot" else cap)
    pe.setJoinStyle(join)
    pat = DASH_PATTERNS.get(dash or "solid")
    if pat:
        pe.setDashPattern(pat)
        if dash == "dot":
            pe.setCapStyle(Qt.PenCapStyle.RoundCap)
    return pe


# ====================================================================== frame
class Frame:
    """The rectangle a map is drawn in and the scale: rect in painter coordinates, the map centre
    (Web Mercator), zoom at reference resolution, k = painter units per reference pixel,
    dpr = device pixels per painter unit (for choosing tile detail)."""

    def __init__(self, rect: QRectF, cx: float, cy: float, zoom_ref: float, k: float = 1.0, dpr: float = 1.0):
        self.rect = QRectF(rect)
        self.cx, self.cy, self.zoom_ref, self.k, self.dpr = float(cx), float(cy), float(zoom_ref), float(k), float(dpr)
        self.view = View(self.cx, self.cy, self.zoom_ref + math.log2(max(self.k, 1e-6)),
                         max(1.0, self.rect.width()), max(1.0, self.rect.height()))

    @property
    def world(self) -> float:
        return self.view.world

    def merc_to_px(self, mx, my):
        x, y = self.view.to_px(mx, my)
        return x + self.rect.x(), y + self.rect.y()

    def to_px(self, lon, lat):
        mx, my = lonlat_to_merc(np.asarray(lon, dtype=float), np.asarray(lat, dtype=float))
        return self.merc_to_px(mx, my)

    def pt(self, lon, lat) -> QPointF:
        x, y = self.to_px(lon, lat)
        return QPointF(float(x), float(y))

    def to_merc(self, x, y):
        return self.view.to_merc(x - self.rect.x(), y - self.rect.y())

    def to_lonlat(self, x, y):
        lon, lat = merc_to_lonlat(*self.to_merc(x, y))
        return float(lon), float(lat)

    def merc_bbox(self, margin_px: float = 0.0):
        w = self.world
        hw, hh = (self.rect.width() / 2 + margin_px) / w, (self.rect.height() / 2 + margin_px) / w
        return self.cx - hw, self.cy - hh, self.cx + hw, self.cy + hh

    def meters_per_ref_px(self) -> float:
        _, lat = merc_to_lonlat(self.cx, self.cy)
        return 40075016.686 * math.cos(math.radians(float(lat))) / (256 * 2.0 ** self.zoom_ref)

    def tile_zoom(self, zmin: int, zmax: int) -> int:
        """Tile detail: tiles are drawn at most twice as dense as at reference resolution, so
        background-map lettering keeps a readable size in print."""
        density = min(self.k * self.dpr, 2.0)
        return int(max(zmin, min(zmax, math.floor(self.zoom_ref + math.log2(max(density, 1e-3)) + 0.35))))


def tile_grid(view: View, tz: int, margin: int = 0):
    """Tiles at zoom tz covering a view: [(x, y, left, top, size)] in view pixels."""
    n = 2 ** tz
    ts = view.world / n
    X0 = view.cx * view.world - view.width / 2
    Y0 = view.cy * view.world - view.height / 2
    tx0, tx1 = int(math.floor(X0 / ts)) - margin, int(math.floor((X0 + view.width) / ts)) + margin
    ty0, ty1 = max(0, int(math.floor(Y0 / ts)) - margin), min(n - 1, int(math.floor((Y0 + view.height) / ts)) + margin)
    if (tx1 - tx0 + 1) * (ty1 - ty0 + 1) > 5000:
        return []
    return [(tx % n, ty, tx * ts - X0, ty * ts - Y0, ts) for ty in range(ty0, ty1 + 1) for tx in range(tx0, tx1 + 1)]


# ====================================================================== tile layers
@dataclass
class TileSpec:
    prefix: tuple
    zmin: int
    zmax: int
    get: object             # (z, x, y) -> array | None   (may start a download)
    peek: object            # (z, x, y) -> array | None   (cache only)
    fetch: object           # [(z, x, y)] -> None         (download, blocking – for exports)
    attribution: str = ""
    extra: dict = field(default_factory=dict)


def _src_from_dict(d: dict) -> TileSource:
    from dataclasses import fields
    names = {f.name for f in fields(TileSource)}
    return TileSource(**{k: v for k, v in d.items() if k in names})


def _xyz_spec(src: TileSource, cache: TileCache) -> TileSpec:
    return TileSpec(("src", src.id, tuple(src.urls), src.wms_layers), src.min_zoom, src.max_zoom,
                    lambda z, x, y: cache.get(src, z, x, y), lambda z, x, y: cache.peek(src, z, x, y),
                    lambda tiles: cache.fetch_many(src, tiles, missing_ok=True), src.attribution)


def _with_neighbours(tiles):
    out = set()
    for z, x, y in tiles:
        n = 2 ** z
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                if 0 <= y + dy < n:
                    out.add((z, (x + dx) % n, y + dy))
    return sorted(out)


def tile_specs(L: dict, cache: TileCache) -> list[TileSpec]:
    k = L["kind"]
    if k == "basemap":
        src = BASEMAPS_BY_ID.get(L.get("source"))
        if src is None or not src.urls:
            return []
        out = [_xyz_spec(src, cache)]
        if L.get("labels") and src.label_source() is not None:
            out.append(_xyz_spec(src.label_source(), cache))
        return out
    if k == "service":
        try:
            src = _src_from_dict(L.get("source") or {})
        except TypeError:
            return []
        return [_xyz_spec(src, cache)] if src.urls else []
    if k == "terrain":
        style = L.get("style", "hillshade")
        if style == "tint":
            prm = (1.0,)
            return [TileSpec(("relief", prm), 0, DEM_MAX_ZOOM,
                             lambda z, x, y: cache.terrain_tile("relief", prm, z, x, y),
                             lambda z, x, y: cache.terrain_tile("relief", prm, z, x, y, fetch=False),
                             lambda tiles: cache.fetch_many(DEM, tiles, missing_ok=True), DEM.attribution)]
        prm = (style, round(float(L.get("strength", 0.8)), 2), bool(L.get("opaque", False)))
        return [TileSpec(("lidar", prm), 0, LIDAR_MAX_ZOOM,
                         lambda z, x, y: cache.terrain_tile("lidar", prm, z, x, y),
                         lambda z, x, y: cache.terrain_tile("lidar", prm, z, x, y, fetch=False),
                         lambda tiles: cache.fetch_many(LIDAR_DEM, cache.native_tiles(LIDAR_DEM, _with_neighbours(tiles)),
                                                        missing_ok=True),
                         LIDAR_DEM.attribution)]
    return []


def _snap(left, top, size, dpr: float = 1.0):
    """Tile rectangle with its edges on whole device pixels, so neighbouring tiles meet exactly
    (no hairline gaps or overlaps between them)."""
    x0, y0 = round(left * dpr) / dpr, round(top * dpr) / dpr
    x1, y1 = round((left + size) * dpr) / dpr, round((top + size) * dpr) / dpr
    return QRectF(x0, y0, x1 - x0, y1 - y0)


def tiles_needed(frame: Frame, spec: TileSpec, max_tiles: int = 600):
    tz = frame.tile_zoom(spec.zmin, spec.zmax)
    grid = tile_grid(frame.view, tz)
    while tz > spec.zmin and (len(grid) > max_tiles or not grid):
        tz -= 1
        grid = tile_grid(frame.view, tz)
    return tz, grid


def draw_tile_layer(p: QPainter, frame: Frame, spec: TileSpec, get, have, wanted: set | None = None,
                    max_tiles: int = 600, snap: bool = True):
    """Draw one tile layer. get(key, z, x, y) -> QImage | EMPTY | None (None = not ready; may start work);
    have(key) -> QImage | EMPTY | None (cache only, for filling gaps with coarser or finer tiles)."""
    tz, grid = tiles_needed(frame, spec, max_tiles)
    ox, oy = frame.rect.x(), frame.rect.y()
    for x, y, left, top, ts in grid:
        key = (spec.prefix, tz, x, y)
        rect = _snap(ox + left, oy + top, ts, frame.dpr) if snap else QRectF(ox + left, oy + top, ts, ts)
        if wanted is not None:
            wanted.add(key)
        img = get(key, tz, x, y)
        if img is None:
            _fill_gap(p, spec, have, tz, x, y, rect)
        elif img is not EMPTY:
            p.drawImage(rect, img)
    return tz


def _fill_gap(p, spec, have, z, x, y, rect):
    for up in range(1, 7):                 # a coarser tile, enlarged
        zz = z - up
        if zz < spec.zmin:
            break
        parent = have((spec.prefix, zz, x >> up, y >> up))
        if parent is EMPTY:
            return
        if parent is not None:
            k = 2 ** up
            sw, sh = parent.width() / k, parent.height() / k
            p.drawImage(rect, parent, QRectF((x % k) * sw, (y % k) * sh, sw, sh))
            return
    if z + 1 <= spec.zmax:                  # or the four finer tiles
        hw, hh = rect.width() / 2, rect.height() / 2
        for dy in (0, 1):
            for dx in (0, 1):
                child = have((spec.prefix, z + 1, 2 * x + dx, 2 * y + dy))
                if child is not None and child is not EMPTY:
                    p.drawImage(QRectF(rect.x() + dx * hw, rect.y() + dy * hh, hw, hh), child)


class BlockingTiles:
    """Tile access for exports: downloads everything first, then draws from the cache."""

    def __init__(self, cache: TileCache):
        self.cache = cache
        self.images: dict = {}

    def prefetch(self, frame: Frame, specs: list[TileSpec], max_tiles: int = 600):
        for spec in specs:
            tz, grid = tiles_needed(frame, spec, max_tiles)
            try:
                spec.fetch([(tz, x, y) for x, y, *_ in grid])
            except Exception:
                pass

    def draw(self, p, frame, spec):
        def get(key, z, x, y):
            if key not in self.images:
                try:
                    a = spec.peek(z, x, y)
                except Exception:
                    a = None
                self.images[key] = None if a is None else to_qimage(a)
            return self.images[key]
        draw_tile_layer(p, frame, spec, get, lambda key: self.images.get(key), snap=False)


# ====================================================================== text
_TEXT_CACHE: OrderedDict = OrderedDict()
_LOCK = threading.RLock()          # text, symbol and SVG caches are shared with drawing threads


def make_font(st: dict, px: float = 100.0) -> QFont:
    f = QFont(st.get("font") or "Segoe UI")
    f.setPixelSize(max(1, int(round(px))))
    f.setBold(bool(st.get("bold")))
    f.setItalic(bool(st.get("italic")))
    sp = float(st.get("spacing") or 0)
    if sp:
        f.setLetterSpacing(QFont.SpacingType.PercentageSpacing, 100.0 + sp)
    f.setHintingPreference(QFont.HintingPreference.PreferNoHinting)
    return f


def text_path(text: str, st: dict, k: float) -> QPainterPath:
    """The text as a path, centred vertically on (0, 0) and aligned horizontally to it
    ("center", "left" = starts at 0, "right" = ends at 0). Built at 100 px and scaled,
    so it looks the same on screen, in PNG and in PDF."""
    key = (text, json.dumps(st, sort_keys=True), round(k, 4))
    with _LOCK:
        hit = _TEXT_CACHE.get(key)
        if hit is not None:
            _TEXT_CACHE.move_to_end(key)
            return QPainterPath(hit)
    f = make_font(st)
    fm = QFontMetricsF(f)
    lines = (text.upper() if st.get("caps") else text).split("\n")
    path = QPainterPath()
    align = st.get("align", "center")
    y = 0.0
    for line in lines:
        w = fm.horizontalAdvance(line)
        x = -w / 2 if align == "center" else (-w if align == "right" else 0.0)
        if line:
            path.addText(x, y + fm.ascent(), f, line)
        y += fm.lineSpacing()
    total = y - (fm.lineSpacing() - fm.height())
    sc = float(st.get("size", 10)) * PT * k / 100.0
    tr = QTransform()
    tr.scale(sc, sc)
    tr.translate(0, -total / 2)
    out = tr.map(path)
    with _LOCK:
        _TEXT_CACHE[key] = out
        while len(_TEXT_CACHE) > 4000:
            _TEXT_CACHE.popitem(last=False)
    return QPainterPath(out)


def text_bounds(text: str, st: dict, k: float) -> QRectF:
    """Bounding box of text_path including box padding (unrotated, relative to the anchor)."""
    r = text_path(text, st, k).boundingRect()
    if r.isEmpty():
        h = float(st.get("size", 10)) * PT * k
        r = QRectF(-h / 2, -h / 2, h, h)
    if st.get("box"):
        pad = 4 * k
        r = r.adjusted(-pad, -pad * 0.8, pad, pad * 0.8)
    return r


def draw_text(p: QPainter, x: float, y: float, text: str, st: dict, k: float) -> QPainterPath:
    """Draw a label with its halo or box; returns its outline (for selecting it)."""
    path = text_path(text, st, k)
    angle = float(st.get("angle") or 0.0)
    tr = QTransform()
    tr.translate(x, y)
    if angle:
        tr.rotate(-angle)
    box = text_bounds(text, st, k)
    p.save()
    p.setTransform(tr, True)
    if st.get("box"):
        p.setPen(QPen(QColor(0, 0, 0, 70), 0.8 * k))
        p.setBrush(qcolor(st["box"], 0.94))
        p.drawRoundedRect(box, 3 * k, 3 * k)
    elif st.get("halo"):
        hp = QPen(qcolor(st["halo"], 0.92), 2 * float(st.get("halo_width", 1.4)) * PT * k)
        hp.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        p.strokePath(path, hp)
    p.fillPath(path, QBrush(qcolor(st.get("color", "#222222"))))
    p.restore()
    shape = QPainterPath()
    shape.addPolygon(tr.map(QPolygonF(box)))
    return shape


# ====================================================================== symbols
_SVG_CACHE: dict = {}
_IMG_CACHE: OrderedDict = OrderedDict()


_TLS = threading.local()


def _renderer(key, color, halo) -> QSvgRenderer:
    """SVG renderers are not thread-safe, so each drawing thread keeps its own."""
    cache = getattr(_TLS, "svg", None)
    if cache is None:
        cache = _TLS.svg = {}
    k = (key, color, halo)
    r = cache.get(k)
    if r is None:
        r = QSvgRenderer(QByteArray(SYM.svg(key, color, halo)))
        cache[k] = r
    return r


def _paint_symbol(p: QPainter, rect: QRectF, key, color, badge, halo):
    if badge:
        d = rect.width()
        c = rect.center()
        p.setPen(QPen(QColor(halo or "#ffffff"), d * 0.07))
        p.setBrush(QColor(color))
        p.drawEllipse(c, d * 0.46, d * 0.46)
        g = d * 0.66
        _renderer(key, "#ffffff", None).render(p, QRectF(c.x() - g / 2, c.y() - g / 2, g, g))
    else:
        _renderer(key, color, halo).render(p, rect)


def symbol_rect(x: float, y: float, key: str, size: float, badge: bool) -> QRectF:
    """Where a symbol of `size` painter units is drawn so its anchor is at (x, y)."""
    if badge:
        return QRectF(x - size / 2, y - size / 2, size, size)
    ax, ay = SYM.anchor(key)
    return QRectF(x - ax * size, y - ay * size, size, size)


def draw_symbol(p: QPainter, x: float, y: float, key: str, color: str, size: float, badge: bool = False,
                vector: bool = False, halo: str | None = "#ffffff") -> QRectF:
    rect = symbol_rect(x, y, key, size, badge)
    if vector:
        _paint_symbol(p, rect, key, color, badge, halo)
        return rect
    dev = p.device()
    dpr = dev.devicePixelRatioF() if dev is not None and hasattr(dev, "devicePixelRatioF") else 1.0
    scale = math.hypot(p.transform().m11(), p.transform().m12()) or 1.0
    px = max(2, int(math.ceil(size * dpr * scale)))
    ck = (key, color, bool(badge), halo, px)
    with _LOCK:
        img = _IMG_CACHE.get(ck)
        if img is not None:
            _IMG_CACHE.move_to_end(ck)
    if img is None:
        img = QImage(px, px, QImage.Format.Format_ARGB32_Premultiplied)
        img.fill(Qt.GlobalColor.transparent)
        q = QPainter(img)
        q.setRenderHint(QPainter.RenderHint.Antialiasing)
        _paint_symbol(q, QRectF(0, 0, px, px), key, color, badge, halo)
        q.end()
        with _LOCK:
            _IMG_CACHE[ck] = img
            while len(_IMG_CACHE) > 600:
                _IMG_CACHE.popitem(last=False)
    p.drawImage(rect, img)
    return rect


def symbol_label_point(rect: QRectF, k: float) -> tuple[float, float]:
    """A symbol's name goes above and to the right of it – the preferred label position."""
    return rect.right() + 2.0 * k, rect.top() + rect.height() * 0.22


# ====================================================================== data layers
def draw_data_layer(p: QPainter, frame: Frame, L: dict, t, vector: bool = False, mask=None):
    """A data layer drawn straight onto the painter (exports and small layers on screen)."""
    from .datadraw import draw_data, draw_labels, frame_mapping
    if t is None:
        return
    mp = frame_mapping(frame)
    draw_data(p, mp, L, t, mask, vector=vector)
    draw_labels(p, mp, L, t, mask)


# ====================================================================== annotations
def _arrow_head(p: QPainter, a: QPointF, b: QPointF, width: float, color: QColor):
    ang = math.atan2(b.y() - a.y(), b.x() - a.x())
    s = max(7.0, width * 3.2)
    left = QPointF(b.x() - s * math.cos(ang - 0.42), b.y() - s * math.sin(ang - 0.42))
    right = QPointF(b.x() - s * math.cos(ang + 0.42), b.y() - s * math.sin(ang + 0.42))
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(color)
    p.drawPolygon(QPolygonF([b, left, right]))


def item_points(frame: Frame, it: dict) -> list[QPointF]:
    c = it.get("coords") or []
    if not c:
        return []
    a = np.asarray(c, dtype=float)
    x, y = frame.to_px(a[:, 0], a[:, 1])
    return [QPointF(float(u), float(v)) for u, v in zip(x, y)]


def draw_item(p: QPainter, frame: Frame, it: dict, vector: bool = False) -> QPainterPath | None:
    """Draw one annotation item; returns its outline for selecting."""
    k = frame.k
    t = it.get("type")
    shape = QPainterPath()
    if t == "area":
        pts = item_points(frame, it)
        if len(pts) < 3:
            return None
        st = it.get("style") or {}
        path = QPainterPath()
        path.addPolygon(QPolygonF(pts))
        path.closeSubpath()
        p.fillPath(path, QBrush(qcolor(st.get("fill", "#888888"), float(st.get("fill_opacity", 0.22)))))
        if float(st.get("width", 1.8)) > 0:
            p.strokePath(path, pen(st.get("stroke", "#555555"), float(st.get("width", 1.8)) * k, st.get("dash", "solid")))
        return path
    if t == "line":
        pts = item_points(frame, it)
        if len(pts) < 2:
            return None
        st = it.get("style") or {}
        w = float(st.get("width", 2.5)) * k
        alpha = float(st.get("opacity", 1.0))
        path = QPainterPath()
        path.addPolygon(QPolygonF(pts))
        p.strokePath(path, pen(st.get("color", "#333333"), w, st.get("dash", "solid"), alpha=alpha))
        if st.get("arrow"):
            _arrow_head(p, pts[-2], pts[-1], w, qcolor(st.get("color", "#333333"), alpha))
        from PySide6.QtGui import QPainterPathStroker
        s = QPainterPathStroker()
        s.setWidth(max(w, 8.0))
        return s.createStroke(path)
    if t == "symbol":
        c = frame.pt(it["lon"], it["lat"])
        size = float(it.get("size", 22)) * k
        rect = draw_symbol(p, c.x(), c.y(), it.get("symbol", "circle"), it.get("color", "#b03a2e"), size,
                           bool(it.get("badge")), vector)
        shape.addRect(rect)
        if it.get("label"):
            st = dict(it.get("label_style") or {})
            st["align"] = "left"
            lx, ly = symbol_label_point(rect, k)
            shape.addPath(draw_text(p, lx, ly, it["label"], st, k))
        return shape
    if t == "text":
        c = frame.pt(it["lon"], it["lat"])
        return draw_text(p, c.x(), c.y(), it.get("text") or " ", it.get("style") or {}, k)
    return None


def draw_annotations(p: QPainter, frame: Frame, L: dict, hits: list | None = None, skip: set | None = None,
                     vector: bool = False):
    for it in L.get("items", []):
        if it.get("hidden") or (skip and it["id"] in skip):
            continue
        shape = draw_item(p, frame, it, vector)
        if hits is not None and shape is not None:
            hits.append((L["id"], it["id"], shape))


# ====================================================================== the whole map
def render_map(p: QPainter, doc, frame: Frame, cache: TileCache, tiles_draw, *, data_draw=None, hits=None,
               skip=None, vector: bool = False, raster_groups: bool = False):
    """Draw every visible layer, bottom to top, inside frame.rect.
    tiles_draw(p, frame, spec) draws one tile layer; data_draw(p, frame, L) may replace the plain
    data-layer drawing (the screen caches it). raster_groups: draw runs of tile layers through an
    image first (for PDF, which cannot blend)."""
    p.save()
    p.setClipRect(frame.rect)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    layers = [L for L in doc.layers if L.get("visible", True)]
    i = 0
    while i < len(layers):
        L = layers[i]
        if raster_groups and L["kind"] in RASTER_KINDS:
            j = i
            while j < len(layers) and layers[j]["kind"] in RASTER_KINDS:
                j += 1
            _raster_group(p, frame, layers[i:j], cache, tiles_draw)
            i = j
            continue
        _draw_layer(p, doc, frame, L, cache, tiles_draw, data_draw, hits, skip, vector)
        i += 1
    p.restore()


def _draw_layer(p, doc, frame, L, cache, tiles_draw, data_draw, hits, skip, vector):
    kind = L["kind"]
    p.setOpacity(float(L.get("opacity", 1.0)))
    p.setCompositionMode(COMP.get(L.get("blend", "normal"), COMP["normal"]))
    try:
        if kind == "color":
            p.fillRect(frame.rect, qcolor(L.get("color", "#ffffff")))
        elif kind in ("basemap", "terrain", "service"):
            # tiles must be drawn without anti-aliasing: blended edges show as lines between tiles
            aa = p.testRenderHint(QPainter.RenderHint.Antialiasing)
            p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
            for spec in specs_for(L, cache):
                tiles_draw(p, frame, spec)
            p.setRenderHint(QPainter.RenderHint.Antialiasing, aa)
        elif kind == "data":
            if data_draw is not None:
                data_draw(p, frame, L)
            else:
                draw_data_layer(p, frame, L, doc.table(L), vector)
        elif kind == "annotations":
            draw_annotations(p, frame, L, hits, skip, vector)
        elif kind == "grid":
            draw_grid(p, frame, L)
    finally:
        p.setCompositionMode(COMP["normal"])
        p.setOpacity(1.0)


def _raster_group(p, frame, layers, cache, tiles_draw):
    W, H = int(math.ceil(frame.rect.width())), int(math.ceil(frame.rect.height()))
    if W < 1 or H < 1:
        return
    img = QImage(W, H, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(Qt.GlobalColor.transparent)
    q = QPainter(img)
    q.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    f2 = Frame(QRectF(0, 0, frame.rect.width(), frame.rect.height()), frame.cx, frame.cy, frame.zoom_ref, frame.k)
    for L in layers:
        _draw_layer(q, None, f2, L, cache, tiles_draw, None, None, None, False)
    q.end()
    p.drawImage(frame.rect, img)


_SPECS: dict = {}


def specs_for(L: dict, cache: TileCache) -> list[TileSpec]:
    """The tile specs of a tile layer (remembered while its settings stay the same)."""
    sig = json.dumps({k: v for k, v in L.items() if k in ("kind", "source", "labels", "style", "strength", "opaque")},
                     sort_keys=True, default=str)
    key = (id(cache), sig)
    hit = _SPECS.get(key)
    if hit is None:
        hit = tile_specs(L, cache)
        _SPECS[key] = hit
        if len(_SPECS) > 200:
            _SPECS.pop(next(iter(_SPECS)))
    return hit


def attributions(doc) -> list[str]:
    out = []

    def add(a):
        if a and a not in out:
            out.append(a)
    for L in doc.layers:
        if not L.get("visible", True):
            continue
        k = L["kind"]
        if k == "basemap":
            src = BASEMAPS_BY_ID.get(L.get("source"))
            add(src.attribution if src else "")
        elif k == "terrain":
            add(DEM.attribution if L.get("style") == "tint" else LIDAR_DEM.attribution)
        elif k == "service":
            add((L.get("source") or {}).get("attribution", ""))
        elif k == "data":
            add((L.get("source") or {}).get("attribution", ""))
    return out



# ====================================================================== coordinate grids
DEG_STEPS = [30, 20, 10, 5, 2, 1, 30 / 60, 20 / 60, 15 / 60, 10 / 60, 5 / 60, 2 / 60, 1 / 60, 30 / 3600, 20 / 3600,
             10 / 3600, 5 / 3600, 2 / 3600, 1 / 3600]


def _deg_label(v: float, axis: str) -> str:
    hemi = ("E" if v >= 0 else "W") if axis == "x" else ("N" if v >= 0 else "S")
    a = abs(v)
    d = int(a + 1e-9)
    m = int((a - d) * 60 + 1e-6)
    sec = (a - d - m / 60) * 3600
    out = f"{d}°"
    if m or sec > 0.5:
        out += f"{m:02d}′"
    if sec > 0.5:
        out += f"{round(sec):02d}″"
    return out + " " + hemi


def _metre_label(v: float) -> str:
    return f"{int(round(v)):,}".replace(",", " ")


def grid_lines(frame: Frame, L: dict):
    """The grid's lines in painter coordinates: [(axis, value, [QPointF...])]."""
    import numpy as np

    from ..geo import get_from_wgs84, get_to_wgs84
    system = L.get("system", "sweref")
    r = frame.rect
    xs = np.linspace(r.left(), r.right(), 9)
    ys = np.linspace(r.top(), r.bottom(), 9)
    gx, gy = np.meshgrid(xs, ys)
    lonlat = np.array([frame.to_lonlat(float(a), float(b)) for a, b in zip(gx.ravel(), gy.ravel())])
    if system == "sweref":
        fwd = get_from_wgs84(3006)
        inv = get_to_wgs84(3006)
        g = fwd(lonlat)
    else:
        def fwd(a):
            return np.asarray(a, dtype=float)
        inv = fwd
        g = lonlat
    x0, x1 = float(g[:, 0].min()), float(g[:, 0].max())
    y0, y1 = float(g[:, 1].min()), float(g[:, 1].max())
    span = max(x1 - x0, y1 - y0)
    iv = float(L.get("interval") or 0)
    if iv <= 0:
        target = span / 6
        if system == "sweref":
            iv = nice_length(target)
        else:
            iv = next((d for d in DEG_STEPS if d <= target), DEG_STEPS[-1])
    if span / iv > 60:
        return [], iv, inv
    out = []
    for axis, lo, hi, olo, ohi in (("x", x0, x1, y0, y1), ("y", y0, y1, x0, x1)):
        v = math.floor(lo / iv) * iv
        while v <= hi + 1e-9:
            t = np.linspace(olo, ohi, 48)
            pts = np.column_stack([np.full_like(t, v), t]) if axis == "x" else np.column_stack([t, np.full_like(t, v)])
            ll = inv(pts)
            px, py = frame.to_px(ll[:, 0], ll[:, 1])
            out.append((axis, v, [QPointF(float(a), float(b)) for a, b in zip(px, py)]))
            v += iv
    return out, iv, inv


def draw_grid(p: QPainter, frame: Frame, L: dict):
    """A coordinate grid: full lines, crosses at the intersections, or ticks along the frame edge,
    with coordinates written where the lines meet the top and left edges."""
    k = frame.k
    lines, _iv, inv = grid_lines(frame, L)
    if not lines:
        return
    col = L.get("color", "#3c3c3c")
    style = L.get("style", "lines")
    pe = pen(col, float(L.get("width", 0.6)) * k, L.get("dash", "solid"))
    p.save()
    p.setClipRect(frame.rect)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.setPen(pe)
    r = frame.rect
    tick = 7 * k
    if style == "lines":
        for _axis, _v, pts in lines:
            p.drawPolyline(QPolygonF(pts))
    elif style == "ticks":
        for axis, _v, pts in lines:
            for q in (pts[0], pts[-1]):
                if axis == "x":
                    y = r.top() if abs(q.y() - r.top()) < abs(q.y() - r.bottom()) else r.bottom()
                    p.drawLine(QPointF(q.x(), y), QPointF(q.x(), y + (tick if y == r.top() else -tick)))
                else:
                    x = r.left() if abs(q.x() - r.left()) < abs(q.x() - r.right()) else r.right()
                    p.drawLine(QPointF(x, q.y()), QPointF(x + (tick if x == r.left() else -tick), q.y()))
    else:                                            # crosses at the intersections
        import numpy as np
        xv = [v for a, v, _p in lines if a == "x"]
        yv = [v for a, v, _p in lines if a == "y"]
        if xv and yv:
            gx, gy = np.meshgrid(xv, yv)
            ll = inv(np.column_stack([gx.ravel(), gy.ravel()]))
            px, py = frame.to_px(ll[:, 0], ll[:, 1])
            for x, y in zip(px.tolist(), py.tolist()):
                p.drawLine(QPointF(x - tick, y), QPointF(x + tick, y))
                p.drawLine(QPointF(x, y - tick), QPointF(x, y + tick))
    if L.get("labels", True):
        system = L.get("system", "sweref")
        st = {"font": "Segoe UI", "size": float(L.get("label_size", 7.0)), "color": col, "halo": "#ffffff",
              "halo_width": 1.2, "align": "left"}
        for axis, v, pts in lines:
            text = _metre_label(v) if system == "sweref" else _deg_label(v, axis)
            if axis == "x":
                q = _at_y(pts, r.top() + 2 * k)
                if q is not None and r.left() + 10 * k < q.x() < r.right() - 30 * k:
                    b = text_bounds(text, st, k)
                    draw_text(p, q.x() + 2.5 * k, r.top() + 2 * k + b.height() / 2, text, st, k)
            else:
                q = _at_x(pts, r.left() + 2 * k)
                if q is not None and r.top() + 14 * k < q.y() < r.bottom() - 8 * k:
                    draw_text(p, r.left() + 2.5 * k, q.y() - 6 * k, text, st, k)
    p.restore()


def _at_y(pts, y):
    for a, b in zip(pts, pts[1:]):
        if (a.y() - y) * (b.y() - y) <= 0 and a.y() != b.y():
            t = (y - a.y()) / (b.y() - a.y())
            return QPointF(a.x() + t * (b.x() - a.x()), y)
    return None


def _at_x(pts, x):
    for a, b in zip(pts, pts[1:]):
        if (a.x() - x) * (b.x() - x) <= 0 and a.x() != b.x():
            t = (x - a.x()) / (b.x() - a.x())
            return QPointF(x, a.y() + t * (b.y() - a.y()))
    return None


# ====================================================================== page elements
def legend_entries(doc) -> list[dict]:
    """What the legend shows, top layers first: data layers and annotation items marked for it."""
    out, seen = [], set()
    for L in reversed(doc.layers):
        if not L.get("visible", True):
            continue
        if L["kind"] == "data" and L.get("legend", True):
            st = L.get("style") or {}
            cls = st.get("classify") or {}
            geom = L.get("geometry", "point")
            if st.get("render") == "heatmap":
                out.append({"kind": "heat", "ramp": st.get("heat_ramp", "heat"), "text": L["name"]})
            elif cls.get("mode", "single") != "single" and cls.get("classes"):
                out.append({"kind": "heading", "text": L["name"]})
                rows = [c for c in cls["classes"] if c.get("show", True)]
                oth = cls.get("other") or {}
                if oth.get("show") and oth.get("count"):
                    rows.append(oth)
                for c in rows:
                    col = c.get("color", "#b4b4b4")
                    out.append({"kind": geom, "style": dict(st, color=col, fill=col, stroke=col, line_color=col),
                                "text": str(c.get("label", ""))})
            else:
                out.append({"kind": geom, "style": st, "text": L["name"]})
        elif L["kind"] == "annotations":
            for it in reversed(L.get("items", [])):
                if it.get("hidden") or not it.get("legend"):
                    continue
                t = it["type"]
                if t == "symbol":
                    text = it.get("legend_text") or SYM.label(it.get("symbol", "circle"))
                    sig = ("symbol", it.get("symbol"), it.get("color"), bool(it.get("badge")), text)
                    e = {"kind": "symbol", "symbol": it.get("symbol"), "color": it.get("color"),
                         "badge": bool(it.get("badge")), "text": text}
                elif t in ("line", "area"):
                    text = it.get("legend_text") or L["name"]
                    sig = (t, json.dumps(it.get("style"), sort_keys=True), text)
                    e = {"kind": "line_item" if t == "line" else "area_item", "style": it.get("style") or {},
                         "text": text}
                else:
                    text = it.get("legend_text") or (it.get("text") or "")[:30]
                    sig = ("text", text)
                    e = {"kind": "text", "style": it.get("style") or {}, "text": text}
                if sig not in seen:
                    seen.add(sig)
                    out.append(e)
    return out


def _lt(d: dict) -> dict:
    return {"font": d["font"], "size": float(d["legend_text_size"]), "color": d["text"], "halo": "", "align": "left"}


def _ltitle(d: dict) -> dict:
    return {"font": d["font"], "size": float(d["legend_text_size"]) + 1.0, "bold": True, "color": d["text"],
            "halo": "", "align": "left"}


def _swatch(p: QPainter, e: dict, r: QRectF, k: float, vector: bool):
    kind = e["kind"]
    st = e.get("style") or {}
    c = r.center()
    if kind == "point":
        size = min(r.height(), float(st.get("size", 7)) * k)
        if st.get("marker", "circle") == "circle":
            ow = float(st.get("outline_width", 1.0))
            p.setPen(pen(st.get("outline", "#ffffff"), ow * k) if ow > 0 and st.get("outline") else Qt.PenStyle.NoPen)
            p.setBrush(qcolor(st.get("color")))
            p.drawEllipse(c, size / 2, size / 2)
        else:
            s = min(r.height() * 1.1, size * 1.5)
            ax, ay = SYM.anchor(st["marker"])
            draw_symbol(p, c.x() + (ax - 0.5) * s, c.y() + (ay - 0.5) * s, st["marker"], st.get("color"), s,
                        False, vector, st.get("outline") or None)
    elif kind == "line":
        p.setPen(pen(st.get("line_color"), float(st.get("line_width", 1.5)) * k, st.get("dash", "solid")))
        p.drawLine(QPointF(r.left(), c.y()), QPointF(r.right(), c.y()))
    elif kind == "line_item":
        p.setPen(pen(st.get("color"), float(st.get("width", 2.5)) * k, st.get("dash", "solid"),
                     alpha=float(st.get("opacity", 1.0))))
        p.drawLine(QPointF(r.left(), c.y()), QPointF(r.right(), c.y()))
    elif kind in ("polygon", "area_item"):
        if kind == "polygon":
            fill, fo, stroke, sw, dash = (st.get("fill"), st.get("fill_opacity", 0.25), st.get("stroke"),
                                          st.get("stroke_width", 1.2), st.get("stroke_dash", "solid"))
        else:
            fill, fo, stroke, sw, dash = (st.get("fill"), st.get("fill_opacity", 0.22), st.get("stroke"),
                                          st.get("width", 1.8), st.get("dash", "solid"))
        rr = r.adjusted(1 * k, 2 * k, -1 * k, -2 * k)
        p.setBrush(qcolor(fill, float(fo)))
        p.setPen(pen(stroke, float(sw) * k, dash) if float(sw) > 0 else Qt.PenStyle.NoPen)
        p.drawRect(rr)
    elif kind == "symbol":
        s = r.height() * (1.05 if e.get("badge") else 1.15)
        if e.get("badge"):
            draw_symbol(p, c.x(), c.y(), e["symbol"], e["color"], s, True, vector)
        else:
            ax, ay = SYM.anchor(e["symbol"])
            draw_symbol(p, c.x() + (ax - 0.5) * s, c.y() + (ay - 0.5) * s, e["symbol"], e["color"], s, False, vector)
    elif kind == "heat":
        from ..styles import HEAT_RAMPS
        from PySide6.QtGui import QLinearGradient
        ramp = HEAT_RAMPS.get(e.get("ramp", "heat"), HEAT_RAMPS["heat"])
        rr = r.adjusted(0, 3 * k, 0, -3 * k)
        g = QLinearGradient(rr.topLeft(), rr.topRight())
        for pos, (cr, cg, cb, _a) in ramp:
            g.setColorAt(pos, QColor(cr, cg, cb, int(60 + 195 * pos)))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(g))
        p.drawRoundedRect(rr, 2 * k, 2 * k)
    elif kind == "text":
        draw_text(p, c.x(), c.y(), "Aa", dict(st, size=min(float(st.get("size", 10)), 10), angle=0, align="center"), k)


def _legend_grid(entries, title, k, d):
    """Layout: (width, height, column width, rows per column, title height, metrics)."""
    lt, tt = _lt(d), _ltitle(d)
    sc = float(d["legend_text_size"]) / 8.5
    pad, sw, gap = 8 * k, float(d["legend_swatch"]) * k, 7 * k
    row = max(17 * k * sc, sw * 0.72)
    cols = max(1, int(d.get("legend_columns", 1)))
    if d.get("legend_style") == "strip":
        cols = max(1, len(entries))
    cols = min(cols, max(1, len(entries)))
    rows = int(math.ceil(len(entries) / cols)) if entries else 0
    tw = max([text_bounds(e["text"], lt, k).width() for e in entries] + [0.0])
    colw = sw + gap + tw + (12 * k if cols > 1 else 0)
    if d.get("legend_style") == "strip":
        widths = [sw + gap + text_bounds(e["text"], lt, k).width() + 14 * k for e in entries]
        w = sum(widths) - 14 * k
    else:
        widths = None
        w = colw * cols - (12 * k if cols > 1 else 0)
    th = 0.0
    if title:
        tb = text_bounds(title, tt, k)
        w = max(w, tb.width())
        th = tb.height() + 5 * k
    h = th + row * rows
    return w + 2 * pad, h + 2 * pad - 2 * k, colw, rows, th, (pad, sw, gap, row, widths)


def legend_size(entries, title: str, k: float, d: dict | None = None) -> tuple[float, float]:
    from ..styles import DESIGN_DEFAULTS
    w, h, *_ = _legend_grid(entries, title, k, d or DESIGN_DEFAULTS)
    return w, h


def draw_legend(p: QPainter, rect: QRectF, entries, title: str, k: float, vector: bool, d: dict | None = None):
    from ..styles import DESIGN_DEFAULTS
    d = d or DESIGN_DEFAULTS
    style = d.get("legend_style", "panel")
    _w, _h, colw, rows, th, (pad, sw, gap, row, widths) = _legend_grid(entries, title, k, d)
    lt, tt = _lt(d), _ltitle(d)
    if style in ("panel", "card", "strip"):
        bg = qcolor(d["legend_bg"], float(d.get("legend_opacity", 0.92)))
        if style == "card":
            for i, a in ((3.0, 18), (1.6, 28)):
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(0, 0, 0, a))
                p.drawRoundedRect(rect.translated(0, i * k * 0.6).adjusted(-i * k * 0.3, 0, i * k * 0.3, 0),
                                  5 * k, 5 * k)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(bg)
            p.drawRoundedRect(rect, 5 * k, 5 * k)
        else:
            border = d.get("legend_border")
            p.setPen(QPen(qcolor(border), 0.8 * k) if border else Qt.PenStyle.NoPen)
            p.setBrush(bg)
            p.drawRect(rect)
    else:                                   # plain: text gets a halo instead of a box
        lt = dict(lt, halo=d["halo"], halo_width=1.3)
        tt = dict(tt, halo=d["halo"], halo_width=1.3)
    y0 = rect.top() + pad
    if title:
        tb = text_bounds(title, tt, k)
        draw_text(p, rect.left() + pad, y0 + tb.height() / 2, title, tt, k)
        y0 += th
    x = rect.left() + pad
    for i, e in enumerate(entries):
        if widths is not None:                       # strip: one row
            cx, cy = x, y0
            x += widths[i]
        else:
            col, r_ = divmod(i, rows) if rows else (0, 0)
            cx, cy = rect.left() + pad + col * colw, y0 + r_ * row
        if e["kind"] == "heading":
            draw_text(p, cx, cy + (row - 2 * k) / 2, e["text"], dict(lt, bold=True), k)
            continue
        sr = QRectF(cx, cy + 1.5 * k, sw, row - 4 * k)
        _swatch(p, e, sr, k, vector)
        draw_text(p, sr.right() + gap, cy + (row - 2 * k) / 2, e["text"], lt, k)


def _scale_text(d: dict) -> dict:
    return {"font": d["font"], "size": float(d["scale_text_size"]), "color": d["scale_dark"], "halo": d["halo"],
            "halo_width": 1.3, "align": "center"}


def nice_length(target: float) -> float:
    if target <= 0:
        return 1.0
    e = 10 ** math.floor(math.log10(target))
    for m in (5, 2, 1):
        if m * e <= target:
            return m * e
    return e


def scalebar_layout(frame: Frame, units: str = "metric", max_frac: float = 0.22):
    """(length in painter units, value, unit) for a scale bar no wider than max_frac of the frame."""
    m_per_unit = frame.meters_per_ref_px() / frame.k
    if units == "imperial":
        ft = m_per_unit / 0.3048
        target = frame.rect.width() * max_frac * ft
        if target >= 5280 * 0.5:
            v = nice_length(target / 5280)
            return v * 5280 / ft, v, "mi"
        v = nice_length(target)
        return v / ft, v, "ft"
    target = frame.rect.width() * max_frac * m_per_unit
    v = nice_length(target)
    if v >= 1000:
        return v / m_per_unit, v / 1000, "km"
    return v / m_per_unit, v, "m"


def _fmt(v: float) -> str:
    return f"{v:g}"


def _scale_labels(v: float, unit: str):
    return "0", _fmt(v / 2), f"{_fmt(v)} {unit}", _fmt(v)


def ratio_text(frame: Frame) -> str:
    """The representative fraction on paper, rounded: 1 : 50 000."""
    d = frame.meters_per_ref_px() * PX_PER_MM * 1000
    if not math.isfinite(d) or d <= 0:
        return ""
    e = 10 ** max(0, int(math.floor(math.log10(d))) - 1)
    return "1 : " + f"{int(round(d / e) * e):,}".replace(",", " ")


def scalebar_size(frame: Frame, cfg: dict, k: float, d: dict | None = None) -> tuple[float, float, tuple]:
    from ..styles import DESIGN_DEFAULTS
    d = d or DESIGN_DEFAULTS
    st = _scale_text(d)
    L, v, unit = scalebar_layout(frame, cfg.get("units", "metric"))
    zero, _mid, end, endv = _scale_labels(v, unit)
    w0 = text_bounds(zero, st, k).width()
    wv = text_bounds(endv, st, k).width()
    we = text_bounds(end, st, k).width()
    lh = text_bounds(end, st, k).height()
    w = 2 * k + w0 / 2 + L - wv / 2 + we + 2 * k
    h = 2 * k + lh + 2.5 * k + 4.2 * k + 3 * k
    if d.get("scale_ratio"):
        rt = text_bounds(ratio_text(frame), st, k)
        h += rt.height() + 2 * k
        w = max(w, rt.width() + 4 * k)
    return w, h, (L, v, unit)


def draw_scalebar(p: QPainter, rect: QRectF, frame: Frame, cfg: dict, k: float, d: dict | None = None):
    """A simple scale bar with round numbers – thin, and no longer than about a fifth of the map."""
    from ..styles import DESIGN_DEFAULTS
    d = d or DESIGN_DEFAULTS
    st = _scale_text(d)
    dark, light = d["scale_dark"], d["scale_light"]
    _w, _h, (L, v, unit) = scalebar_size(frame, cfg, k, d)
    zero, mid, end, endv = _scale_labels(v, unit)
    x0 = rect.left() + 2 * k + text_bounds(zero, st, k).width() / 2
    bh = 4.2 * k
    bottom = rect.bottom()
    if d.get("scale_ratio"):
        rt = ratio_text(frame)
        rb = text_bounds(rt, st, k)
        draw_text(p, rect.left() + 2 * k, bottom - 1 * k - rb.height() / 2, rt, dict(st, align="left"), k)
        bottom -= rb.height() + 2 * k
    ybar = bottom - 3 * k - bh
    style = cfg.get("style", "bar")
    if style == "line":
        for colr, wdt in ((light, 3.4), (dark, 1.3)):
            p.setPen(pen(colr, wdt * k, cap=Qt.PenCapStyle.SquareCap))
            p.drawLine(QPointF(x0, ybar + bh), QPointF(x0 + L, ybar + bh))
            p.drawLine(QPointF(x0, ybar), QPointF(x0, ybar + bh))
            p.drawLine(QPointF(x0 + L, ybar), QPointF(x0 + L, ybar + bh))
            p.drawLine(QPointF(x0 + L / 2, ybar + bh * 0.45), QPointF(x0 + L / 2, ybar + bh))
    elif style == "hollow":
        p.setPen(pen(dark, 0.9 * k, cap=Qt.PenCapStyle.SquareCap, join=Qt.PenJoinStyle.MiterJoin))
        p.setBrush(qcolor(light))
        p.drawRect(QRectF(x0, ybar + bh * 0.3, L, bh * 0.7))
        p.setBrush(qcolor(dark))
        p.drawRect(QRectF(x0, ybar + bh * 0.3, L / 2, bh * 0.7))
    else:
        p.setPen(pen(dark, 0.9 * k, cap=Qt.PenCapStyle.SquareCap, join=Qt.PenJoinStyle.MiterJoin))
        for i in range(4):
            p.setBrush(qcolor(dark) if i % 2 == 0 else qcolor(light))
            p.drawRect(QRectF(x0 + i * L / 4, ybar, L / 4, bh))
    ty = ybar - 2.5 * k - text_bounds(end, st, k).height() / 2
    draw_text(p, x0, ty, zero, st, k)
    draw_text(p, x0 + L / 2, ty, mid, st, k)
    draw_text(p, x0 + L - text_bounds(endv, st, k).width() / 2, ty, end, dict(st, align="left"), k)


def north_size(k: float, d: dict | None = None) -> tuple[float, float]:
    style = (d or {}).get("north_style", "simple")
    if style == "star":
        return 34 * k, 40 * k
    if style == "letter":
        return 16 * k, 30 * k
    if style == "classic":
        return 22 * k, 40 * k
    return 16 * k, 34 * k


def draw_north(p: QPainter, rect: QRectF, k: float, d: dict | None = None):
    """North arrows: kept simple and small, as the book advises – north is up on these maps."""
    from ..styles import DESIGN_DEFAULTS
    d = d or DESIGN_DEFAULTS
    col, halo = d["north_color"], d["halo"]
    style = d.get("north_style", "simple")
    nfont = {"font": "Georgia", "size": 10.0, "bold": True, "color": col, "halo": halo, "halo_width": 1.3}
    c = rect.center().x()
    if style == "letter":
        draw_text(p, c, rect.top() + 7 * k, "N", nfont, k)
        p.setPen(pen(halo, 3.2 * k))
        p.drawLine(QPointF(c, rect.top() + 14 * k), QPointF(c, rect.bottom() - 1 * k))
        p.setPen(pen(col, 1.4 * k))
        p.drawLine(QPointF(c, rect.top() + 14 * k), QPointF(c, rect.bottom() - 1 * k))
        return
    if style == "star":
        cy = rect.top() + 23 * k
        R, r = 15 * k, 3.6 * k
        pts = []
        for i in range(8):
            ang = math.radians(-90 + i * 45)
            rad = R if i % 2 == 0 else r * 1.6
            if i % 2 == 0 and i != 0:
                rad = R * 0.62
            pts.append(QPointF(c + rad * math.cos(ang), cy + rad * math.sin(ang)))
        p.setPen(pen(halo, 2.2 * k))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPolygon(QPolygonF(pts))
        for i in range(0, 8, 2):
            a, l_, rr = pts[i], pts[(i - 1) % 8], pts[(i + 1) % 8]
            ctr = QPointF(c, cy)
            p.setPen(pen(col, 0.8 * k))
            p.setBrush(qcolor(col))
            p.drawPolygon(QPolygonF([ctr, a, l_]))
            p.setBrush(qcolor(halo))
            p.drawPolygon(QPolygonF([ctr, a, rr]))
        draw_text(p, c, rect.top() + 4.5 * k, "N", dict(nfont, size=8.5), k)
        return
    top = rect.top() + 13 * k
    bottom = rect.bottom() - 1 * k
    w = (8 if style == "classic" else 6) * k
    notch = bottom - (9 if style == "classic" else 6) * k
    p.setPen(pen(halo, 2.4 * k))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawPolygon(QPolygonF([QPointF(c, top), QPointF(c - w, bottom), QPointF(c, notch), QPointF(c + w, bottom)]))
    p.setPen(pen(col, 0.9 * k))
    p.setBrush(qcolor(col))
    p.drawPolygon(QPolygonF([QPointF(c, top), QPointF(c - w, bottom), QPointF(c, notch)]))
    p.setBrush(qcolor(halo))
    p.drawPolygon(QPolygonF([QPointF(c, top), QPointF(c + w, bottom), QPointF(c, notch)]))
    if style == "classic":
        p.setPen(pen(col, 0.9 * k))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(QPointF(c, notch - 2 * k), 8.5 * k, 8.5 * k)
    draw_text(p, c, rect.top() + 6 * k, "N", nfont, k)


# ====================================================================== page
@dataclass
class PageGeometry:
    page: QRectF
    frame: QRectF
    title_pt: QPointF | None
    subtitle_pt: QPointF | None
    credits_rect: QRectF | None
    s: float                     # painter units per millimetre
    k: float                     # painter units per reference pixel
    title_inside: bool = False


def _subtitle(d: dict) -> dict:
    return {"font": d["font"], "size": 11.0, "color": d["muted"], "halo": "", "align": "left"}


def _credits(d: dict) -> dict:
    return {"font": d["font"], "size": float(d["credits_size"]), "color": d["credits_color"], "halo": "",
            "align": "left"}


def title_style(page: dict) -> dict:
    ts = page.get("title_style") or {}
    return {"font": ts.get("font", "Georgia"), "size": float(ts.get("size", 22)), "bold": ts.get("bold", True),
            "italic": ts.get("italic", False), "color": ts.get("color", "#1d1d1d"), "halo": "",
            "align": page.get("title_align", "left")}


def credits_text(doc) -> str:
    c = doc.page.get("credits") or {}
    parts = [c.get("text", "").strip()]
    if c.get("auto", True):
        a = attributions(doc)
        if a:
            parts.append("Sources: " + "; ".join(a))
    return "   ·   ".join(x for x in parts if x)


def page_geometry(doc, ox: float, oy: float, s: float) -> PageGeometry:
    from ..styles import design
    page = doc.page
    d = design(page)
    Wmm, Hmm = paper_mm(page)
    m = float(page.get("margin_mm", 12))
    k = s / PX_PER_MM
    prect = QRectF(ox, oy, Wmm * s, Hmm * s)
    inside = d.get("title_position") == "inside"
    top = m
    title_pt = sub_pt = None
    ts = title_style(page)
    if not inside:
        if page.get("title"):
            h = float(ts["size"]) * MM_PER_PT * 1.18
            title_pt = top + h / 2
            top += h
        if page.get("subtitle"):
            h = 11.0 * MM_PER_PT * 1.35
            sub_pt = top + h / 2 + 0.5
            top += h + 0.5
        if title_pt is not None or sub_pt is not None:
            top += 3.5
    bottom = m
    credits_rect = None
    if (page.get("credits") or {}).get("show", True) and credits_text(doc):
        h = float(d["credits_size"]) * MM_PER_PT * 1.5
        credits_rect = QRectF(ox + m * s, oy + (Hmm - m - h) * s, (Wmm - 2 * m) * s, h * s)
        bottom += h + 1.5
    frame = QRectF(ox + m * s, oy + top * s, (Wmm - 2 * m) * s, max(10.0, (Hmm - top - bottom)) * s)
    align = page.get("title_align", "left")
    tx = ox + (Wmm / 2 if align == "center" else m) * s
    return PageGeometry(prect, frame, QPointF(tx, oy + title_pt * s) if title_pt is not None else None,
                        QPointF(tx, oy + sub_pt * s) if sub_pt is not None else None, credits_rect, s, k, inside)


def element_rect(frame_rect: QRectF, cfg: dict, w: float, h: float) -> QRectF:
    """Rectangle of a legend / scale bar / north arrow from its anchor corner and position in the frame."""
    fx, fy = cfg.get("pos", [0.5, 0.5])
    a = cfg.get("anchor", "tl")
    x = frame_rect.left() + float(fx) * frame_rect.width()
    y = frame_rect.top() + float(fy) * frame_rect.height()
    if a[1] == "r":
        x -= w
    if a[0] == "b":
        y -= h
    x = max(frame_rect.left(), min(frame_rect.right() - w, x))
    y = max(frame_rect.top(), min(frame_rect.bottom() - h, y))
    return QRectF(x, y, w, h)


def anchor_for(frame_rect: QRectF, r: QRectF) -> tuple[str, list[float]]:
    """Anchor corner and position for an element placed at r (the corner nearest the frame's sides)."""
    c = r.center()
    v = "b" if c.y() > frame_rect.center().y() else "t"
    h = "r" if c.x() > frame_rect.center().x() else "l"
    x = r.right() if h == "r" else r.left()
    y = r.bottom() if v == "b" else r.top()
    return v + h, [round((x - frame_rect.left()) / frame_rect.width(), 4),
                   round((y - frame_rect.top()) / frame_rect.height(), 4)]


def draw_page(p: QPainter, doc, geo: PageGeometry, map_frame: Frame, draw_map, vector: bool = False,
              placeholders: bool = False) -> dict:
    """The page with its map and map elements. Returns {element: QRectF} for selecting them."""
    from ..styles import design
    page = doc.page
    d = design(page)
    k = geo.k
    rects = {}
    p.fillRect(geo.page, qcolor(page.get("background", "#ffffff")))
    draw_map(p, map_frame)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    if page.get("neatline", True):
        fw = float(d.get("frame_width", 0.3)) * geo.s
        p.setPen(pen(d["frame_color"], fw, cap=Qt.PenCapStyle.SquareCap, join=Qt.PenJoinStyle.MiterJoin))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRect(geo.frame)
        if d.get("frame_style") == "double":
            gap = 1.2 * geo.s
            p.setPen(pen(d["frame_color"], fw * 2.2, cap=Qt.PenCapStyle.SquareCap, join=Qt.PenJoinStyle.MiterJoin))
            p.drawRect(geo.frame.adjusted(-gap, -gap, gap, gap))
    # title and subtitle above the map
    if geo.title_pt is not None:
        sh = draw_text(p, geo.title_pt.x(), geo.title_pt.y(), page["title"], title_style(page), k)
        rects["title"] = sh.boundingRect()
    if geo.subtitle_pt is not None:
        st = dict(_subtitle(d), align=page.get("title_align", "left"))
        sh = draw_text(p, geo.subtitle_pt.x(), geo.subtitle_pt.y(), page["subtitle"], st, k)
        rects["title"] = rects["title"].united(sh.boundingRect()) if "title" in rects else sh.boundingRect()
    # map elements, clipped to the frame
    p.save()
    p.setClipRect(geo.frame)
    if geo.title_inside and (page.get("title") or page.get("subtitle")):
        rects["title"] = _inside_title(p, geo, page, d, k)
    lg = page.get("legend") or {}
    if lg.get("show", True):
        entries = legend_entries(doc)
        if entries:
            w, h = legend_size(entries, lg.get("title", ""), k, d)
            r = element_rect(geo.frame, lg, w, h)
            draw_legend(p, r, entries, lg.get("title", ""), k, vector, d)
            rects["legend"] = r
        elif placeholders:
            r = element_rect(geo.frame, lg, 120 * k, 30 * k)
            p.setPen(pen("#9a9a9a", 1 * k, "dash"))
            p.setBrush(QColor(255, 255, 255, 120))
            p.drawRect(r)
            draw_text(p, r.center().x(), r.center().y(), "Legend (empty)",
                      dict(_lt(d), align="center", color="#8a8a8a"), k)
            rects["legend"] = r
    sb = page.get("scalebar") or {}
    if sb.get("show", True):
        w, h, _ = scalebar_size(map_frame, sb, k, d)
        r = element_rect(geo.frame, sb, w, h)
        draw_scalebar(p, r, map_frame, sb, k, d)
        rects["scalebar"] = r
    nr = page.get("north") or {}
    if nr.get("show", True):
        w, h = north_size(k, d)
        r = element_rect(geo.frame, nr, w, h)
        draw_north(p, r, k, d)
        rects["north"] = r
    p.restore()
    # credits
    if geo.credits_rect is not None:
        cs = _credits(d)
        text = credits_text(doc)
        r = geo.credits_rect
        fm = QFontMetricsF(make_font(cs))
        sc = cs["size"] * PT * k / 100.0
        text = fm.elidedText(text, Qt.TextElideMode.ElideRight, r.width() / sc)
        draw_text(p, r.left(), r.center().y(), text, cs, k)
        rects["credits"] = r
    # pictures and logos, on top of everything
    for im in page.get("images") or []:
        r = page_image_rect(doc, im, geo)
        if r is None:
            continue
        p.save()
        p.setOpacity(float(im.get("opacity", 1.0)))
        _draw_picture(p, doc, im.get("img"), r)
        p.restore()
        rects["image:" + im["id"]] = r
    return rects


_PICS: dict = {}


def picture(doc, img_id):
    """(kind, object, aspect h/w) for a page image: an SVG renderer or a QImage."""
    data = doc.images.get(img_id)
    if data is None:
        return None
    key = (img_id, len(data))
    hit = _PICS.get(key)
    if hit is None:
        head = data[:300].lstrip()
        if head.startswith(b"<svg") or head.startswith(b"<?xml") or b"<svg" in head:
            r = QSvgRenderer(QByteArray(data))
            sz = r.defaultSize()
            hit = ("svg", r, (sz.height() / sz.width()) if sz.width() > 0 else 1.0)
        else:
            img = QImage.fromData(data)
            if img.isNull():
                return None
            hit = ("img", img, img.height() / max(1, img.width()))
        _PICS[key] = hit
    return hit


def page_image_rect(doc, im: dict, geo) -> QRectF | None:
    pic = picture(doc, im.get("img"))
    if pic is None:
        return None
    w = float(im.get("w", 30)) * geo.s
    return QRectF(geo.page.left() + float(im.get("x", 10)) * geo.s, geo.page.top() + float(im.get("y", 10)) * geo.s,
                  w, w * pic[2])


def _draw_picture(p, doc, img_id, r: QRectF):
    pic = picture(doc, img_id)
    if pic is None:
        return
    if pic[0] == "svg":
        pic[1].render(p, r)
    else:
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        p.drawImage(r, pic[1])


def _inside_title(p, geo, page, d, k) -> QRectF:
    """Title (and subtitle) on a panel in the map's top-left corner."""
    ts = dict(title_style(page), align="left")
    st = dict(_subtitle(d), align="left")
    pad = 9 * k
    tb = text_bounds(page.get("title") or " ", ts, k) if page.get("title") else QRectF()
    sb = text_bounds(page["subtitle"], st, k) if page.get("subtitle") else QRectF()
    w = max(tb.width(), sb.width()) + 2 * pad
    h = tb.height() + sb.height() + 2 * pad + (3 * k if page.get("subtitle") and page.get("title") else 0)
    r = QRectF(geo.frame.left() + 10 * k, geo.frame.top() + 10 * k, w, h)
    style = d.get("legend_style", "panel")
    if style != "plain":
        p.setPen(QPen(qcolor(d["legend_border"]), 0.8 * k) if style == "panel" and d.get("legend_border")
                 else Qt.PenStyle.NoPen)
        p.setBrush(qcolor(d["legend_bg"], float(d.get("legend_opacity", 0.92))))
        if style == "card":
            p.drawRoundedRect(r, 5 * k, 5 * k)
        else:
            p.drawRect(r)
    else:
        ts["halo"] = st["halo"] = d["halo"]
    y = r.top() + pad
    if page.get("title"):
        draw_text(p, r.left() + pad, y + tb.height() / 2, page["title"], ts, k)
        y += tb.height() + 3 * k
    if page.get("subtitle"):
        draw_text(p, r.left() + pad, y + sb.height() / 2, page["subtitle"], st, k)
    return r


__all__ = ["Frame", "TileSpec", "tile_specs", "draw_tile_layer", "BlockingTiles", "render_map",
           "draw_text", "text_bounds", "text_path", "draw_symbol", "draw_item", "draw_data_layer",
           "legend_entries", "draw_page", "page_geometry", "element_rect", "anchor_for", "attributions",
           "credits_text", "PX_PER_MM", "PT", "PAGE_ELEMENTS", "BLENDS", "COMP", "qcolor", "pen", "specs_for",
           "draw_scalebar", "scalebar_size", "item_points", "symbol_rect", "ratio_text", "draw_legend", "legend_size",
           "draw_north", "north_size"]
