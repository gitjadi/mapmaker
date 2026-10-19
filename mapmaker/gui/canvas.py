"""The canvas: shows the map (or the whole page with its map frame) and lets you work on it
with tools, much like an image editor.

Tools: Select / move (V), Pan (H), Text (T), Symbol (S), Line (L), Area (A).
Hold Space to pan with any tool. Wheel zooms the map; Ctrl + wheel zooms the page view.

Map tiles are made on background threads and kept as images, so panning and zooming only
repaint cached tiles. Large imported layers are drawn into an image that is reused while
you pan or zoom and redrawn when you stop."""
from __future__ import annotations

import json
import logging
import math
import time

import numpy as np
from PySide6.QtCore import QObject, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QImage, QPainter, QPainterPath, QPen, QPolygonF
from PySide6.QtWidgets import QPlainTextEdit, QWidget

from ..document import (annotation_layer, area_item, item_bbox, line_item, merge_page, paper_mm, symbol_item,
                        text_item)
from ..render import lonlat_to_merc, merc_to_lonlat
from ..styles import default_area_style, default_line_style
from ..tiles import TileCache
from . import painter as P
from .theme import T
from . import datadraw as DD
from .tileimages import EMPTY, TileImages
from .workers import run_async

log = logging.getLogger("mapmaker")
MIN_ZOOM, MAX_ZOOM = 1.0, 19.5
SMALL_LAYER = 3000        # data layers with more features than this are drawn as tiles on background threads
FEW_LABELS = 300          # labels of layers with more features are placed on a background thread

TOOL_HINTS = {
    "select": "Click a label, symbol, line or area to select it; drag to move it. Drag the map to pan. "
              "Double-click text to edit it.",
    "pan": "Drag to move the map. Wheel to zoom.",
    "marquee": "Drag a rectangle to select features of the data layer chosen in the selection bar. Shift adds, "
               "Alt removes. Click a feature to select just it; Esc clears the selection.",
    "text": "Click on the map to write a label. Enter finishes, Shift+Enter starts a new line.",
    "symbol": "Click on the map to place the symbol chosen above.",
    "line": "Click to add points, double-click or Enter to finish, Backspace removes the last point, Esc cancels.",
    "area": "Click to add corners, double-click or Enter to close the area, Backspace removes the last point, Esc cancels.",
}
TOOL_CURSORS = {"select": Qt.CursorShape.ArrowCursor, "pan": Qt.CursorShape.OpenHandCursor,
                "marquee": Qt.CursorShape.CrossCursor,
                "text": Qt.CursorShape.IBeamCursor, "symbol": Qt.CursorShape.CrossCursor,
                "line": Qt.CursorShape.CrossCursor, "area": Qt.CursorShape.CrossCursor}


class _Bridge(QObject):
    tilesArrived = Signal()


class _InlineEditor(QPlainTextEdit):
    """Edits a label in place. Enter finishes, Shift+Enter adds a line, Esc cancels."""
    done = Signal(bool)          # True = keep the text

    def __init__(self, parent, multiline=True):
        super().__init__(parent)
        self.multiline = multiline
        self._finished = False
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setStyleSheet(f"QPlainTextEdit {{ background: rgba(255,255,255,235); color: #1d1d1d; "
                           f"border: 1.5px solid {T.select}; border-radius: 3px; padding: 1px 3px; }}")
        self.textChanged.connect(self._grow)

    def _grow(self):
        fm = self.fontMetrics()
        lines = self.toPlainText().split("\n") or [""]
        w = max(fm.horizontalAdvance(t) for t in lines) + 26
        h = fm.lineSpacing() * len(lines) + 14
        c = self.geometry().center()
        self.resize(max(90, w), h)
        self.move(int(c.x() - self.width() / 2), int(c.y() - self.height() / 2))

    def keyPressEvent(self, ev):
        k = ev.key()
        if k in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and not (
                self.multiline and ev.modifiers() & Qt.KeyboardModifier.ShiftModifier):
            self.finish(True)
            return
        if k == Qt.Key.Key_Escape:
            self.finish(False)
            return
        super().keyPressEvent(ev)

    def focusOutEvent(self, ev):
        super().focusOutEvent(ev)
        self.finish(True)

    def finish(self, keep: bool):
        if not self._finished:
            self._finished = True
            self.done.emit(keep)


class MapCanvas(QWidget):
    selectionChanged = Signal()     # read .sel
    changed = Signal(str)           # the document changed (label of the undo step)
    viewChanged = Signal()
    mouseMoved = Signal(float, float)
    hint = Signal(str)
    toolChanged = Signal(str)
    featureSelectionChanged = Signal()

    def __init__(self, doc, tiles: TileCache, parent=None):
        super().__init__(parent)
        self.doc = doc
        self.tiles = tiles
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMinimumSize(320, 240)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)
        self.tool = "select"
        self.opts = {"text": {"preset": "place"},
                     "symbol": {"symbol": "pin", "color": "#b03a2e", "size": 22.0, "badge": False},
                     "line": default_line_style(), "area": default_area_style()}
        self.sel = {"layer": None, "item": None, "element": None}
        self.page_scale = None            # page view zoom in px per mm; None = fit the window
        self.page_off = QPointF(0, 0)
        self._frame: P.Frame | None = None
        self._geo = None
        self._hits: list = []
        self._page_rects: dict = {}
        self._draft: list = []
        self._drag = None
        self._space = False
        self._hover = None
        self._mouse = None
        self._editor = None
        self._wanted: set = set()
        self._dprev: dict = {}            # (layer signature, z, x, y) -> the last data tile drawn there
        self._labels: dict = {}           # layer id -> labels drawn for one view (made on a worker thread)
        self._label_jobs: set = set()
        self._ltimer = QTimer(self)
        self._ltimer.setSingleShot(True)
        self._ltimer.timeout.connect(self._launch_labels)
        self.fsel: dict = {}              # data layer id -> boolean mask of selected features
        self.fsel_ver = 0
        self.fsel_layer = None            # the layer the Select-features tool works on (None = selected layer)
        self._marquee = None
        self._anim = QTimer(self)
        self._anim.setInterval(15)
        self._anim.timeout.connect(self._anim_step)
        self._anim_target = None
        self._bridge = _Bridge()
        self._bridge.tilesArrived.connect(self._schedule_repaint)
        tiles.on_ready = self._bridge.tilesArrived.emit
        self.timgs = TileImages(workers=3, budget_mb=220)
        self._repaint = QTimer(self)
        self._repaint.setSingleShot(True)
        self._repaint.setInterval(30)
        self._repaint.timeout.connect(self.update)
        self.timgs.ready.connect(self._schedule_repaint)
        self._heartbeat = False
        self.setCursor(TOOL_CURSORS["select"])

    # ================================================================ state
    def set_document(self, doc):
        self.finish_editing(True)
        self.doc = doc
        self.sel = {"layer": None, "item": None, "element": None}
        self._draft = []
        self._labels.clear()
        self.fsel.clear()
        self.fsel_ver += 1
        self.page_scale = None
        self.page_off = QPointF(0, 0)
        self.update()
        self.selectionChanged.emit()

    def page_mode(self) -> bool:
        return bool(self.doc.page.get("show"))

    def set_page_mode(self, on: bool):
        self.doc.page["show"] = bool(on)
        self.sel["element"] = None
        self._labels.clear()
        self.update()
        self.viewChanged.emit()

    def set_tool(self, tool: str):
        if tool == self.tool:
            return
        self.finish_editing(True)
        if self._draft:
            self._finish_draft(len(self._draft) >= (2 if self.tool == "line" else 3))
        self.tool = tool
        self.setCursor(TOOL_CURSORS.get(tool, Qt.CursorShape.ArrowCursor))
        self.hint.emit(TOOL_HINTS.get(tool, ""))
        self.toolChanged.emit(tool)
        self.update()

    def select(self, layer=None, item=None, element=None, emit=True):
        if (layer, item, element) == (self.sel["layer"], self.sel["item"], self.sel["element"]):
            return
        self.sel = {"layer": layer, "item": item, "element": element}
        self.update()
        if emit:
            self.selectionChanged.emit()

    def _schedule_repaint(self):
        if not self._repaint.isActive():
            self._repaint.start()

    # ================================================================ geometry
    def _fit_scale(self) -> float:
        W, H = paper_mm(self.doc.page)
        return max(0.25, min((self.width() - 56) / W, (self.height() - 56) / H))

    def page_scale_now(self) -> float:
        return self.page_scale or self._fit_scale()

    def _page_origin(self, s: float):
        W, H = paper_mm(self.doc.page)
        return (self.width() - W * s) / 2 + self.page_off.x(), (self.height() - H * s) / 2 + self.page_off.y()

    def frame(self) -> P.Frame:
        cx, cy, z = self.doc.view
        dpr = self.devicePixelRatioF()
        if self.page_mode():
            s = self.page_scale_now()
            ox, oy = self._page_origin(s)
            self._geo = P.page_geometry(self.doc, ox, oy, s)
            return P.Frame(self._geo.frame, cx, cy, z, self._geo.k, dpr)
        self._geo = None
        return P.Frame(QRectF(0, 0, self.width(), self.height()), cx, cy, z, 1.0, dpr)

    def in_frame(self, pos: QPointF) -> bool:
        f = self._frame or self.frame()
        return f.rect.contains(pos)

    def lonlat_at(self, pos: QPointF):
        f = self._frame or self.frame()
        return f.to_lonlat(pos.x(), pos.y())

    def zoom_percent(self) -> int:
        return int(round(self.page_scale_now() / P.PX_PER_MM * 100)) if self.page_mode() else 100

    def map_scale(self) -> float:
        """Scale denominator of the map on the page (or on screen at 96 dpi)."""
        f = self._frame or self.frame()
        return f.meters_per_ref_px() * P.PX_PER_MM * 1000

    # ================================================================ view
    def set_view(self, cx, cy, zoom):
        self.doc.view = [float(cx), float(cy), float(min(MAX_ZOOM, max(MIN_ZOOM, zoom)))]
        self.update()
        self.viewChanged.emit()

    def zoom_map(self, steps: float, pos: QPointF | None = None, animate: bool = True):
        """Zoom by `steps` levels keeping the point under `pos` in place – animated, like a web map."""
        f = self._frame or self.frame()
        active = self._anim.isActive() and self._anim_target is not None
        if active:
            target, pos, merc = self._anim_target[0], self._anim_target[1], self._anim_target[2]
        else:
            target = self.doc.view[2]
            pos = pos or f.rect.center()
            merc = f.to_merc(pos.x(), pos.y())
        nz = min(MAX_ZOOM, max(MIN_ZOOM, target + steps))
        if not animate:
            self._anim.stop()
            self._anim_target = None
            self._apply_zoom(nz, pos, merc)
            self.update()
            self.viewChanged.emit()
            return
        self._anim_target = (nz, pos, merc, self.doc.view[2], time.time())
        if not active:
            self._anim.start()

    def _apply_zoom(self, nz, pos, merc):
        f = self._frame or self.frame()
        world = 256 * 2.0 ** (nz + math.log2(f.k))
        self.doc.view = [float(merc[0] - (pos.x() - f.rect.center().x()) / world),
                         float(merc[1] - (pos.y() - f.rect.center().y()) / world), float(nz)]

    def _anim_step(self):
        if self._anim_target is None:
            self._anim.stop()
            return
        target, pos, merc, start, t0 = self._anim_target
        k = min(1.0, (time.time() - t0) / 0.18)
        k = 1 - (1 - k) ** 3
        self._apply_zoom(start + (target - start) * k, pos, merc)
        self.update()
        if k >= 1.0:
            self._anim.stop()
            self._anim_target = None
            self.viewChanged.emit()
            self.update()

    def moving(self) -> bool:
        return self._anim.isActive() or (self._drag is not None and self._drag.get("kind") in ("pan", "page"))

    def zoom_page(self, factor: float, pos: QPointF | None = None):
        s = self.page_scale_now()
        ns = max(0.3, min(60.0, s * factor))
        pos = pos or QPointF(self.width() / 2, self.height() / 2)
        ox, oy = self._page_origin(s)
        pmx, pmy = (pos.x() - ox) / s, (pos.y() - oy) / s
        self.page_scale = ns
        cox, coy = self._page_origin(ns)
        cox -= self.page_off.x()
        coy -= self.page_off.y()
        self.page_off = QPointF(pos.x() - pmx * ns - cox, pos.y() - pmy * ns - coy)
        self.update()
        self.viewChanged.emit()

    def fit_page(self):
        self.page_scale = None
        self.page_off = QPointF(0, 0)
        self.update()
        self.viewChanged.emit()

    def zoom_to_bbox(self, w, s, e, n, pad: float = 0.08, max_zoom: float = 16.5):
        f = self._frame or self.frame()
        x0, y1 = lonlat_to_merc(w, s)
        x1, y0 = lonlat_to_merc(e, n)
        cx, cy = float(x0 + x1) / 2, float(y0 + y1) / 2
        Wr, Hr = f.rect.width() / f.k * (1 - 2 * pad), f.rect.height() / f.k * (1 - 2 * pad)
        dx, dy = max(float(x1 - x0), 1e-12), max(float(y1 - y0), 1e-12)
        z = math.log2(max(1e-9, min(Wr / (dx * 256), Hr / (dy * 256))))
        self.set_view(cx, cy, min(max_zoom, z))

    def center_on(self, lon, lat, zoom=None):
        mx, my = lonlat_to_merc(lon, lat)
        self.set_view(float(mx), float(my), self.doc.view[2] if zoom is None else zoom)

    # ================================================================ painting
    def paintEvent(self, _ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        frame = self.frame()
        self._frame = frame
        self._hits = []
        self._wanted = set()
        moving = self._drag is not None and self._drag.get("kind") in ("pan", "page")
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, not moving)
        if self.page_mode():
            p.fillRect(self.rect(), QColor(T.paste))
            geo = self._geo
            for i, a in ((6, 18), (3, 30), (1, 50)):
                p.fillRect(geo.page.adjusted(-i + 2, -i + 3, i + 2, i + 3), QColor(0, 0, 0, a))
            try:
                self._page_rects = P.draw_page(p, self.doc, geo, frame, self._draw_map, placeholders=True)
            except Exception:
                log.exception("Drawing the page failed")
        else:
            p.fillRect(self.rect(), QColor(T.map_bg))
            self._page_rects = {}
            try:
                self._draw_map(p, frame)
            except Exception:
                log.exception("Drawing the map failed")
            self._paint_screen_furniture(p, frame)
        try:
            self._paint_selection(p, frame)
        except Exception:
            log.exception("Drawing the selection failed")
        self.timgs.set_wanted(self._wanted)
        self._paint_overlay(p, frame)
        self._paint_busy(p)
        p.end()

    def _draw_map(self, p: QPainter, frame: P.Frame):
        if self.page_mode():
            from ..styles import design
            p.fillRect(frame.rect, QColor(design(self.doc.page)["map_bg"]))
        else:
            p.fillRect(frame.rect, QColor(T.map_bg))
        skip = {self._editor.property("item_id")} if self._editor is not None and self._editor.property("item_id") else None
        P.render_map(p, self.doc, frame, self.tiles, self._draw_tiles, data_draw=self._draw_data, hits=self._hits,
                     skip=skip)

    def _draw_tiles(self, p, frame, spec):
        timgs = self.timgs

        def get(key, z, x, y):
            img = timgs.get(key)
            if img is None:
                timgs.request(key, lambda: spec.get(z, x, y), 0)
            return img
        tz = P.draw_tile_layer(p, frame, spec, get, timgs.get, self._wanted)
        if not self.moving():
            # one ring of tiles around the view, so small pans find them ready
            for x, y, *_ in P.tile_grid(frame.view, tz, margin=1):
                key = (spec.prefix, tz, x, y)
                if key not in self._wanted:
                    self._wanted.add(key)
                    timgs.request(key, lambda z=tz, x=x, y=y: spec.get(z, x, y), 1)

    # -- data layers: small ones directly, big ones as tiles made on worker threads
    def _draw_data(self, p, frame: P.Frame, L):
        t = self.doc.table(L)
        if t is None:
            return
        render = (L.get("style") or {}).get("render", "points")
        if render in ("heatmap", "clusters"):
            # whole-view drawings: made on a worker thread for each view, reused while moving
            self._cached_view(p, frame, L, t, "overlay")
        elif t.n <= SMALL_LAYER:
            DD.draw_data(p, DD.frame_mapping(frame), L, t)
        else:
            self._data_tiles(p, frame, L, t)
        if not (L.get("label") or {}).get("field") or render == "heatmap":
            return
        if t.n <= FEW_LABELS:
            DD.draw_labels(p, DD.frame_mapping(frame), L, t)
        else:
            self._cached_view(p, frame, L, t, "labels")

    def _data_tiles(self, p, frame: P.Frame, L, t, mask=None, highlight=False):
        dpr = frame.dpr
        zdev = frame.view.zoom + math.log2(dpr)
        tz = int(max(0, min(22, math.floor(zdev + 1e-6))))
        lvl = round(zdev - tz, 3)
        px = max(64, int(round(256 * 2.0 ** (zdev - tz))))
        kdev = frame.k * dpr
        style = json.dumps(L.get("style") or {}, sort_keys=True)
        sig = ("data", L.get("table"), style, highlight, self.fsel_ver if highlight else 0)
        key0 = (sig, lvl, round(kdev, 3))
        Lc = {"id": L["id"], "style": json.loads(style)}
        moving = self._anim.isActive()
        ox, oy = frame.rect.x(), frame.rect.y()
        timgs = self.timgs
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        for x, y, left, top, ts in P.tile_grid(frame.view, tz):
            rect = P._snap(ox + left, oy + top, ts, dpr)
            key = (key0, tz, x, y)
            self._wanted.add(key)
            img = timgs.get(key)
            if img is None:
                if not moving:
                    def job(z=tz, x=x, y=y):
                        r = DD.render_tile(Lc, t, z, x, y, px, kdev, mask, highlight)
                        return EMPTY if r is None else r
                    timgs.request(key, job, 0)
                self._data_gap(p, sig, tz, x, y, rect)
            else:
                self._dprev[(sig, tz, x, y)] = img
                if img is not EMPTY:
                    p.drawImage(rect, img)
        p.restore()
        if len(self._dprev) > 1500:
            for k in list(self._dprev)[:500]:
                del self._dprev[k]

    def _data_gap(self, p, sig, z, x, y, rect):
        """While a data tile is being made, show what was drawn there before (any zoom)."""
        prev = self._dprev
        img = prev.get((sig, z, x, y))
        if img is not None:
            if img is not EMPTY:
                p.drawImage(rect, img)
            return
        for up in (1, 2, 3):
            par = prev.get((sig, z - up, x >> up, y >> up))
            if par is not None:
                if par is not EMPTY:
                    k = 2 ** up
                    sw, sh = par.width() / k, par.height() / k
                    p.drawImage(rect, par, QRectF((x % k) * sw, (y % k) * sh, sw, sh))
                return
        hw, hh = rect.width() / 2, rect.height() / 2
        for dy in (0, 1):
            for dx in (0, 1):
                ch = prev.get((sig, z + 1, 2 * x + dx, 2 * y + dy))
                if ch is not None and ch is not EMPTY:
                    p.drawImage(QRectF(rect.x() + dx * hw, rect.y() + dy * hh, hw, hh), ch)

    def _view_key(self, L, frame, what):
        part = L.get("label") if what == "labels" else L.get("style")
        return (L.get("table"), what, json.dumps(part, sort_keys=True), (L.get("style") or {}).get("size"),
                round(frame.k, 4), round(frame.rect.width()), round(frame.rect.height()), frame.dpr)

    def _cached_view(self, p, frame: P.Frame, L, t, what: str):
        key = self._view_key(L, frame, what)
        view = (frame.cx, frame.cy, frame.zoom_ref)
        c = self._labels.get((L["id"], what))
        if c is not None and c["key"] == key and c["img"] is not None:
            if c["view"] == view:
                p.drawImage(frame.rect, c["img"])
                return
            ccx, ccy, cz = c["view"]
            if abs(cz - frame.zoom_ref) <= 3:
                sc = 2.0 ** (frame.zoom_ref - cz)
                w, h = frame.rect.width() * sc, frame.rect.height() * sc
                cxp, cyp = frame.merc_to_px(ccx, ccy)
                p.drawImage(QRectF(float(cxp) - w / 2, float(cyp) - h / 2, w, h), c["img"])
        if c is None or c["key"] != key or c["view"] != view:
            self._ltimer.start(60 if what == "overlay" else 160)

    def _launch_labels(self):
        """Start the whole-view drawings (labels, heat maps, clusters) that are out of date."""
        if self.moving():
            self._ltimer.start(120)
            return
        frame = self._frame or self.frame()
        for L in self.doc.layers:
            if L["kind"] != "data" or not L.get("visible", True):
                continue
            t = self.doc.table(L)
            if t is None:
                continue
            render = (L.get("style") or {}).get("render", "points")
            jobs = []
            if render in ("heatmap", "clusters"):
                jobs.append("overlay")
            if (L.get("label") or {}).get("field") and render != "heatmap" and t.n > FEW_LABELS:
                jobs.append("labels")
            for what in jobs:
                self._launch_view(L, t, frame, what)

    def _launch_view(self, L, t, frame, what):
        jid = (L["id"], what)
        if jid in self._label_jobs:
            return
        key = self._view_key(L, frame, what)
        view = (frame.cx, frame.cy, frame.zoom_ref)
        c = self._labels.get(jid)
        if c is not None and c["key"] == key and c["view"] == view:
            return
        self._label_jobs.add(jid)
        Lc = json.loads(json.dumps({"id": L["id"], "style": L.get("style"), "label": L.get("label")}))
        rect = QRectF(frame.rect)
        fr = (frame.cx, frame.cy, frame.zoom_ref, frame.k, frame.dpr)

        def work(Lc=Lc, t=t, rect=rect, fr=fr, what=what):
            cx, cy, z, k, dpr = fr
            img = QImage(max(1, int(rect.width() * dpr)), max(1, int(rect.height() * dpr)),
                         QImage.Format.Format_ARGB32_Premultiplied)
            img.setDevicePixelRatio(dpr)
            img.fill(Qt.GlobalColor.transparent)
            q = QPainter(img)
            q.setRenderHint(QPainter.RenderHint.Antialiasing)
            f2 = P.Frame(QRectF(0, 0, rect.width(), rect.height()), cx, cy, z, k, dpr)
            try:
                if what == "labels":
                    n = DD.draw_labels(q, DD.frame_mapping(f2), Lc, t)
                else:
                    n = DD.draw_data(q, DD.frame_mapping(f2), Lc, t)
            finally:
                q.end()
            return img if n and n > 0 else None

        def done(img, jid=jid, key=key, view=view):
            self._label_jobs.discard(jid)
            self._labels[jid] = {"key": key, "view": view, "img": img}
            self.update()

        def failed(_tb, jid=jid):
            self._label_jobs.discard(jid)
        run_async(work, on_done=done, on_error=failed)

    # -- feature selection
    def set_feature_selection(self, lid: str, mask):
        if mask is None or not mask.any():
            self.fsel.pop(lid, None)
        else:
            self.fsel[lid] = mask
        self.fsel_ver += 1
        self.update()
        self.featureSelectionChanged.emit()

    def clear_feature_selection(self, lid: str | None = None):
        if lid is None:
            self.fsel.clear()
        else:
            self.fsel.pop(lid, None)
        self.fsel_ver += 1
        self.update()
        self.featureSelectionChanged.emit()

    def selection_layer(self):
        """The data layer the Select-features tool works on."""
        L = self.doc.layer(self.fsel_layer) or self.doc.layer(self.sel.get("layer"))
        if L is not None and L["kind"] == "data":
            return L
        return next((x for x in reversed(self.doc.layers) if x["kind"] == "data" and x.get("visible", True)), None)

    def _paint_selection(self, p: QPainter, frame: P.Frame):
        for L in self.doc.layers:
            m = self.fsel.get(L["id"])
            if m is None or L["kind"] != "data" or not L.get("visible", True):
                continue
            t = self.doc.table(L)
            if t is None or len(m) != t.n:
                continue
            p.save()
            p.setClipRect(frame.rect)
            if int(m.sum()) <= 4000:
                DD.draw_data(p, DD.frame_mapping(frame), L, t, m, highlight=True)
            else:
                self._data_tiles(p, frame, L, t, mask=m, highlight=True)
            p.restore()

    def _paint_screen_furniture(self, p: QPainter, frame: P.Frame):
        """Map view only: a small scale bar and the sources, as a working aid (not part of the map)."""
        cfg = {"units": (self.doc.page.get("scalebar") or {}).get("units", "metric"), "style": "bar"}
        w, h, _ = P.scalebar_size(frame, cfg, 1.0)
        P.draw_scalebar(p, QRectF(10, self.height() - h - 8, w, h), frame, cfg, 1.0)
        text = " · ".join(P.attributions(self.doc))
        if text:
            f = QFont(self.font())
            f.setPointSizeF(7.5)
            p.setFont(f)
            fm = p.fontMetrics()
            text = fm.elidedText(text, Qt.TextElideMode.ElideLeft, int(self.width() * 0.6))
            tw = fm.horizontalAdvance(text) + 12
            r = QRectF(self.width() - tw, self.height() - fm.height() - 4, tw, fm.height() + 4)
            p.fillRect(r, QColor(255, 255, 255, 190))
            p.setPen(QColor("#4a4a4a"))
            p.drawText(r, Qt.AlignmentFlag.AlignCenter, text)

    def _paint_busy(self, p: QPainter):
        busy = self.tiles.busy() > 0 or self.timgs.pending() > 0
        if not busy:
            return
        f = QFont(self.font())
        f.setPointSizeF(8.5)
        p.setFont(f)
        fm = p.fontMetrics()
        msg = "Loading map…"
        w = fm.horizontalAdvance(msg) + 22
        r = QRectF(self.width() - w - 10, 10, w, fm.height() + 8)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(30, 30, 30, 200))
        p.drawRoundedRect(r, 9, 9)
        p.setPen(QColor("#dddddd"))
        p.drawText(r, Qt.AlignmentFlag.AlignCenter, msg)
        if not self._heartbeat:
            self._heartbeat = True
            QTimer.singleShot(300, self._beat)

    def _beat(self):
        self._heartbeat = False
        self.update()

    # -- selection, hover and drafts
    def _shape_of(self, item_id):
        for _lid, iid, shape in self._hits:
            if iid == item_id:
                return shape
        return None

    def _paint_overlay(self, p: QPainter, frame: P.Frame):
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        blue = QColor(T.select)
        # hover
        if self._hover and self._hover != self.sel["item"] and self.tool == "select" and self._drag is None:
            sh = self._shape_of(self._hover)
            if sh is not None:
                p.setPen(QPen(QColor(47, 140, 255, 150), 1))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawRect(sh.boundingRect().adjusted(-2, -2, 2, 2))
        # selected item
        it = self.doc.find_item(self.sel["item"])[1] if self.sel["item"] else None
        if it is not None and not it.get("hidden"):
            if it["type"] in ("line", "area"):
                pts = P.item_points(frame, it)
                if len(pts) >= 2:
                    poly = QPolygonF(pts)
                    if it["type"] == "area":
                        poly.append(pts[0])
                    p.setPen(QPen(blue, 1.2))
                    p.setBrush(Qt.BrushStyle.NoBrush)
                    p.drawPolyline(poly)
                for q in pts:
                    p.setPen(QPen(blue, 1.4))
                    p.setBrush(QColor("#ffffff"))
                    p.drawRect(QRectF(q.x() - 3.5, q.y() - 3.5, 7, 7))
            else:
                sh = self._shape_of(it["id"])
                if sh is not None:
                    pe = QPen(blue, 1.3)
                    pe.setDashPattern([4, 3])
                    p.setPen(pe)
                    p.setBrush(Qt.BrushStyle.NoBrush)
                    p.drawRect(sh.boundingRect().adjusted(-3, -3, 3, 3))
        # selected page element
        el = self.sel["element"]
        if el and el in self._page_rects:
            r = self._page_rects[el].adjusted(-3, -3, 3, 3)
            pe = QPen(blue, 1.3)
            pe.setDashPattern([4, 3])
            p.setPen(pe)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRect(r)
            for c in (r.topLeft(), r.topRight(), r.bottomLeft(), r.bottomRight()):
                p.setPen(QPen(blue, 1.2))
                p.setBrush(QColor("#ffffff"))
                p.drawRect(QRectF(c.x() - 3, c.y() - 3, 6, 6))
        # rectangle of the Select-features tool
        if self._marquee is not None:
            pe = QPen(QColor(DD.HIGHLIGHT), 1.2)
            pe.setDashPattern([4, 3])
            p.setPen(pe)
            p.setBrush(QColor(0, 183, 255, 30))
            p.drawRect(self._marquee.normalized())
        # line / area being drawn
        if self._draft:
            pts = [frame.pt(lo, la) for lo, la in self._draft]
            if self._mouse is not None:
                pts.append(self._mouse)
            st = self.opts[self.tool] if self.tool in ("line", "area") else default_line_style()
            if self.tool == "area" and len(pts) >= 3:
                path = QPainterPath()
                path.addPolygon(QPolygonF(pts))
                path.closeSubpath()
                p.fillPath(path, QBrush(P.qcolor(st.get("fill", "#888888"), float(st.get("fill_opacity", 0.22)))))
            col = st.get("color", st.get("stroke", "#b03a2e"))
            p.setPen(P.pen(col, float(st.get("width", 2.0)) * frame.k, st.get("dash", "solid")))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawPolyline(QPolygonF(pts))
            p.setPen(QPen(blue, 1.3))
            p.setBrush(QColor("#ffffff"))
            for q in pts[:len(self._draft)]:
                p.drawEllipse(q, 3.2, 3.2)
        p.restore()

    # ================================================================ hit testing
    def _hit_item(self, pos: QPointF):
        for lid, iid, shape in reversed(self._hits):
            L = self.doc.layer(lid)
            if L is None or L.get("locked"):
                continue
            if shape.contains(pos):
                return lid, iid
        return None

    def _hit_vertex(self, pos: QPointF):
        if not self.sel["item"]:
            return None
        L, it = self.doc.find_item(self.sel["item"])
        if it is None or it["type"] not in ("line", "area") or (L and L.get("locked")):
            return None
        for i, q in enumerate(P.item_points(self._frame, it)):
            if abs(q.x() - pos.x()) <= 6 and abs(q.y() - pos.y()) <= 6:
                return i
        return None

    def _hit_element(self, pos: QPointF):
        for name in reversed([n for n in self._page_rects if n.startswith("image:")]):
            if self._page_rects[name].adjusted(-3, -3, 3, 3).contains(pos):
                return name
        for name in ("north", "scalebar", "legend", "title", "credits"):
            r = self._page_rects.get(name)
            if r is not None and r.adjusted(-3, -3, 3, 3).contains(pos):
                return name
        return None

    def _page_image(self, el: str):
        return next((i for i in self.doc.page.get("images") or [] if "image:" + i["id"] == el), None)

    def _hit_data(self, pos: QPointF):
        f = self._frame
        mp = DD.frame_mapping(f)
        for L in reversed(self.doc.layers):
            if L["kind"] != "data" or not L.get("visible", True) or L.get("locked"):
                continue
            t = self.doc.table(L)
            i = DD.pick(t, mp, pos.x(), pos.y())
            if i is not None:
                return L, i
        return None

    def feature_at(self, pos: QPointF):
        """(data layer, attributes) under pos – for tooltips."""
        if self._frame is None or not self._frame.rect.contains(pos) or self.moving():
            return None
        h = self._hit_data(pos)
        if h is None:
            return None
        L, i = h
        return L, self.doc.table(L).row(i)

    # ================================================================ adding things
    def target_layer(self, create: bool = True):
        """The annotation layer new labels and symbols go into: the selected one, else the topmost."""
        L = self.doc.layer(self.sel["layer"])
        if L is not None and L["kind"] == "annotations" and not L.get("locked"):
            return L
        for L in reversed(self.doc.layers):
            if L["kind"] == "annotations" and not L.get("locked"):
                return L
        if not create:
            return None
        L = annotation_layer(self.doc.unique_name("Labels & symbols"))
        self.doc.add_layer(L)
        return L

    def _add_item(self, it, label):
        before = self.doc.state()
        L = self.target_layer()
        L["items"].append(it)
        self.doc.push_undo(label, before)
        self.select(L["id"], it["id"])
        self.changed.emit(label)
        return L

    def _place_text(self, pos):
        lon, lat = self.lonlat_at(pos)
        before = self.doc.state()
        L = self.target_layer()
        it = text_item(lon, lat, "", self.opts["text"].get("preset", "place"))
        L["items"].append(it)
        self.doc.touch()
        self.select(L["id"], it["id"])
        self.edit_item_text(it["id"], new=True, before=before)

    def _place_symbol(self, pos):
        lon, lat = self.lonlat_at(pos)
        o = self.opts["symbol"]
        it = symbol_item(lon, lat, o["symbol"], o["color"], o["size"], o["badge"])
        self._add_item(it, "Add symbol")

    def _finish_draft(self, keep: bool):
        pts, self._draft = self._draft, []
        tool = self.tool
        if keep:
            if tool == "line" and len(pts) >= 2:
                it = line_item(pts)
                it["style"] = dict(self.opts["line"])
                self._add_item(it, "Add line")
            elif tool == "area" and len(pts) >= 3:
                it = area_item(pts)
                it["style"] = dict(self.opts["area"])
                self._add_item(it, "Add area")
        self.update()

    # ================================================================ editing text in place
    def edit_item_text(self, item_id, new=False, before=None):
        self.finish_editing(True)
        L, it = self.doc.find_item(item_id)
        if it is None or it["type"] not in ("text", "symbol"):
            return
        self.update()
        f = self._frame or self.frame()
        c = f.pt(it["lon"], it["lat"])
        field_ = "text" if it["type"] == "text" else "label"
        st = it["style"] if it["type"] == "text" else it.get("label_style") or {}
        ed = _InlineEditor(self, multiline=it["type"] == "text")
        font = P.make_font(st, max(9.0, min(40.0, float(st.get("size", 10)) * P.PT * f.k)))
        ed.setFont(font)
        ed.setPlainText(it.get(field_) or "")
        ed.setProperty("item_id", item_id if it["type"] == "text" else None)
        fm = ed.fontMetrics()
        ed.resize(max(110, fm.horizontalAdvance(ed.toPlainText()) + 30), fm.lineSpacing() + 14)
        if it["type"] == "symbol":
            c = QPointF(c.x() + float(it.get("size", 22)) * f.k * 0.5 + ed.width() / 2, c.y() - 4)
        ed.move(int(c.x() - ed.width() / 2), int(c.y() - ed.height() / 2))
        ed.selectAll()
        ed.show()
        ed.setFocus()
        self._editor = ed
        state = {"before": before or self.doc.state(), "new": new, "item": item_id, "field": field_,
                 "old": it.get(field_) or ""}
        ed.done.connect(lambda keep: self._editor_done(ed, keep, state))
        self.update()

    def edit_title(self):
        self.finish_editing(True)
        if not self.page_mode():
            return
        geo = self._geo or (self.frame() and self._geo)
        r = self._page_rects.get("title")
        ed = _InlineEditor(self, multiline=False)
        ts = P.title_style(self.doc.page)
        ed.setFont(P.make_font(ts, max(12.0, min(48.0, ts["size"] * P.PT * geo.k))))
        ed.setPlainText(self.doc.page.get("title", ""))
        fm = ed.fontMetrics()
        ed.resize(max(240, fm.horizontalAdvance(ed.toPlainText()) + 40), fm.lineSpacing() + 14)
        if r is not None:
            ed.move(int(r.left() - 6), int(r.center().y() - ed.height() / 2))
        else:
            ed.move(int(geo.frame.left()), int(geo.frame.top() - ed.height() - 4))
        ed.selectAll()
        ed.show()
        ed.setFocus()
        self._editor = ed
        before = self.doc.state()

        def done(keep):
            text = ed.toPlainText().strip()
            self._editor = None
            ed.deleteLater()
            if keep and text != self.doc.page.get("title", ""):
                self.doc.page["title"] = text
                self.doc.push_undo("Edit title", before)
                self.changed.emit("Edit title")
            self.setFocus()
            self.update()
        ed.done.connect(done)

    def _editor_done(self, ed, keep, state):
        self._editor = None
        ed.hide()
        ed.deleteLater()
        L, it = self.doc.find_item(state["item"])
        if it is not None:
            text = ed.toPlainText().rstrip()
            if state["new"]:
                if keep and text.strip():
                    it["text"] = text
                    self.doc.push_undo("Add text", state["before"])
                    self.changed.emit("Add text")
                else:
                    self.doc.remove_item(it["id"])
                    self.doc.touch()
                    self.select(L["id"] if L else None, None)
                    self.changed.emit("")
            elif keep and text != state["old"]:
                if it["type"] == "text" and not text.strip():
                    self.doc.remove_item(it["id"])
                    self.select(L["id"], None)
                else:
                    it[state["field"]] = text
                self.doc.push_undo("Edit text", state["before"])
                self.changed.emit("Edit text")
        self.setFocus()
        self.update()

    def finish_editing(self, keep: bool = True):
        if self._editor is not None:
            self._editor.finish(keep)

    # ================================================================ deleting / nudging
    def delete_selection(self) -> bool:
        if self.sel["item"]:
            L, it = self.doc.find_item(self.sel["item"])
            if it is not None and not (L and L.get("locked")):
                self.doc.checkpoint("Delete " + it["type"])
                self.doc.remove_item(it["id"])
                self.select(L["id"], None)
                self.changed.emit("Delete")
                return True
        el = self.sel["element"]
        if el and el.startswith("image:"):
            self.doc.checkpoint("Delete picture")
            self.doc.page["images"] = [i for i in self.doc.page.get("images") or [] if "image:" + i["id"] != el]
            self.select(self.sel["layer"], None, None)
            self.changed.emit("Delete picture")
            return True
        if el in ("legend", "scalebar", "north", "credits"):
            self.doc.checkpoint("Hide " + el)
            self.doc.page[el]["show"] = False
            self.select(self.sel["layer"], None, None)
            self.changed.emit("Hide")
            return True
        if el == "title":
            self.doc.checkpoint("Remove title")
            self.doc.page["title"] = ""
            self.doc.page["subtitle"] = ""
            self.select(self.sel["layer"], None, None)
            self.changed.emit("Remove title")
            return True
        return False

    def nudge(self, dx: float, dy: float):
        L, it = self.doc.find_item(self.sel["item"])
        if it is None or (L and L.get("locked")):
            return
        self.doc.checkpoint("Nudge", merge=("nudge", it["id"]))
        f = self._frame or self.frame()
        _move_merc(it, dx / f.world, dy / f.world)
        self.changed.emit("Nudge")
        self.update()

    # ================================================================ mouse
    def mousePressEvent(self, ev):
        pos = ev.position()
        self.setFocus()
        if self._editor is not None:
            self.finish_editing(True)
        if self._frame is None:
            self._frame = self.frame()
        btn = ev.button()
        inside = self.in_frame(pos) or not self.page_mode()
        pan_like = btn == Qt.MouseButton.MiddleButton or (btn == Qt.MouseButton.LeftButton and
                                                           (self._space or self.tool == "pan"))
        if pan_like:
            self._start_pan(pos, inside)
            return
        if btn == Qt.MouseButton.RightButton and self._draft:
            self._finish_draft(True)
            return
        if btn != Qt.MouseButton.LeftButton:
            return
        if self.tool == "select":
            v = self._hit_vertex(pos)
            if v is not None:
                self._drag = {"kind": "vertex", "index": v, "start": pos, "before": self.doc.state(), "moved": False}
                return
            el = self._hit_element(pos) if self.page_mode() else None
            if el:
                self.select(self.sel["layer"], None, el)
                if el.startswith("image:"):
                    im = self._page_image(el)
                    r = self._page_rects[el]
                    corner = abs(pos.x() - r.right()) < 9 and abs(pos.y() - r.bottom()) < 9
                    self._drag = {"kind": "element", "name": el, "start": pos, "rect": QRectF(r),
                                  "before": self.doc.state(), "moved": False, "image": dict(im),
                                  "resize": corner}
                    return
                if el in ("legend", "scalebar", "north"):
                    self._drag = {"kind": "element", "name": el, "start": pos, "rect": QRectF(self._page_rects[el]),
                                  "before": self.doc.state(), "moved": False}
                return
            hit = self._hit_item(pos) if inside else None
            if hit:
                lid, iid = hit
                self.select(lid, iid)
                _L, it = self.doc.find_item(iid)
                self._drag = {"kind": "item", "start": pos, "before": self.doc.state(), "moved": False,
                              "orig": json.dumps(it), "id": iid}
                return
            if inside:
                d = self._hit_data(pos)
                if d is not None:
                    self.select(d[0]["id"], None, None)
                else:
                    self.select(self.sel["layer"], None, None)
            else:
                self.select(self.sel["layer"], None, None)
            self._start_pan(pos, inside)
            return
        if not inside:
            self._start_pan(pos, False)
            return
        if self.tool == "marquee":
            self._marquee = QRectF(pos, pos)
            self._drag = {"kind": "marquee", "start": pos, "mods": ev.modifiers()}
            return
        if self.tool == "text":
            hit = self._hit_item(pos)
            _L, it = self.doc.find_item(hit[1]) if hit else (None, None)
            if it is not None and it["type"] == "text":
                self.select(hit[0], hit[1])
                self.edit_item_text(hit[1])
            else:
                self._place_text(pos)
        elif self.tool == "symbol":
            self._place_symbol(pos)
        elif self.tool in ("line", "area"):
            lon, lat = self.lonlat_at(pos)
            if self._draft:
                q = self._frame.pt(*self._draft[-1])
                if abs(q.x() - pos.x()) + abs(q.y() - pos.y()) < 3:
                    return
                if self.tool == "area" and len(self._draft) >= 3:
                    q0 = self._frame.pt(*self._draft[0])
                    if math.hypot(q0.x() - pos.x(), q0.y() - pos.y()) < 8:
                        self._finish_draft(True)
                        return
            self._draft.append([lon, lat])
            self.update()

    def _start_pan(self, pos, inside):
        kind = "pan" if inside else ("page" if self.page_mode() else "pan")
        self._drag = {"kind": kind, "start": pos, "view": list(self.doc.view), "off": QPointF(self.page_off)}
        self.setCursor(Qt.CursorShape.ClosedHandCursor)

    def mouseMoveEvent(self, ev):
        pos = ev.position()
        self._mouse = pos
        f = self._frame or self.frame()
        if f.rect.contains(pos):
            lon, lat = f.to_lonlat(pos.x(), pos.y())
            self.mouseMoved.emit(lon, lat)
        else:
            self.mouseMoved.emit(float("nan"), float("nan"))
        d = self._drag
        if d is None:
            if self.tool == "select":
                hit = self._hit_item(pos)
                hv = hit[1] if hit else None
                el = self._hit_element(pos) if self.page_mode() else None
                over = hv is not None or self._hit_vertex(pos) is not None or (
                    el in ("legend", "scalebar", "north") or (el or "").startswith("image:"))
                cur = Qt.CursorShape.SizeAllCursor if over else Qt.CursorShape.ArrowCursor
                if el and el.startswith("image:"):
                    r = self._page_rects[el]
                    if abs(pos.x() - r.right()) < 9 and abs(pos.y() - r.bottom()) < 9:
                        cur = Qt.CursorShape.SizeFDiagCursor
                self.setCursor(cur)
                if hv != self._hover:
                    self._hover = hv
                    self.update()
            if self._draft:
                self.update()
            return
        delta = pos - d["start"]
        if d["kind"] == "marquee":
            self._marquee = QRectF(d["start"], pos)
            self.update()
            return
        if d["kind"] == "pan":
            world = f.world
            cx, cy, z = d["view"]
            self.doc.view = [cx - delta.x() / world, cy - delta.y() / world, z]
            self.update()
            self.viewChanged.emit()
        elif d["kind"] == "page":
            self.page_off = d["off"] + delta
            self.update()
        elif d["kind"] in ("item", "vertex", "element"):
            if not d["moved"] and abs(delta.x()) + abs(delta.y()) < 3:
                return
            d["moved"] = True
            if d["kind"] == "item":
                it = self.doc.find_item(d["id"])[1]
                if it is not None:
                    orig = json.loads(d["orig"])
                    for k in ("lon", "lat", "coords"):
                        if k in orig:
                            it[k] = orig[k]
                    _move_merc(it, delta.x() / f.world, delta.y() / f.world)
            elif d["kind"] == "vertex":
                it = self.doc.find_item(self.sel["item"])[1]
                if it is not None:
                    lon, lat = f.to_lonlat(pos.x(), pos.y())
                    it["coords"][d["index"]] = [lon, lat]
            elif d.get("image") is not None:
                im = self._page_image(d["name"])
                if im is not None:
                    s_ = self._geo.s
                    if d["resize"]:
                        im["w"] = round(max(3.0, d["image"]["w"] + delta.x() / s_), 2)
                    else:
                        im["x"] = round(d["image"]["x"] + delta.x() / s_, 2)
                        im["y"] = round(d["image"]["y"] + delta.y() / s_, 2)
            else:
                r = d["rect"].translated(delta)
                anchor, posf = P.anchor_for(self._geo.frame, r)
                cfg = self.doc.page[d["name"]]
                cfg["anchor"], cfg["pos"] = anchor, posf
            self.doc.touch()
            self.update()

    def mouseReleaseEvent(self, ev):
        d = self._drag
        self._drag = None
        self.setCursor(TOOL_CURSORS.get(self.tool, Qt.CursorShape.ArrowCursor) if not self._space
                       else Qt.CursorShape.OpenHandCursor)
        if d is None:
            return
        if d["kind"] == "marquee":
            self._finish_marquee(d, ev.position())
            return
        if d["kind"] == "pan":
            self.viewChanged.emit()
        elif d["kind"] in ("item", "vertex", "element") and d["moved"]:
            label = {"item": "Move", "vertex": "Move point", "element": "Move " + d.get("name", "")}[d["kind"]]
            if d.get("image") is not None:
                label = "Resize picture" if d.get("resize") else "Move picture"
            self.doc.push_undo(label, d["before"])
            self.changed.emit(label)
        self.update()

    def _finish_marquee(self, d, pos):
        r = QRectF(d["start"], pos).normalized()
        self._marquee = None
        L = self.selection_layer()
        if L is None:
            self.hint.emit("Add a data layer first – the Select-features tool selects its features.")
            self.update()
            return
        t = self.doc.table(L)
        f = self._frame or self.frame()
        if r.width() < 4 and r.height() < 4:
            i = DD.pick(t, DD.frame_mapping(f), pos.x(), pos.y())
            hit = np.zeros(t.n, dtype=bool)
            if i is not None:
                hit[i] = True
        else:
            x0, y0 = f.to_merc(r.left(), r.top())
            x1, y1 = f.to_merc(r.right(), r.bottom())
            hit = DD.in_rect(t, x0, y0, x1, y1)
        mods = d.get("mods") or Qt.KeyboardModifier.NoModifier
        old = self.fsel.get(L["id"])
        if old is None or len(old) != t.n:
            old = np.zeros(t.n, dtype=bool)
        if mods & Qt.KeyboardModifier.ShiftModifier:
            new = old | hit
        elif mods & (Qt.KeyboardModifier.AltModifier | Qt.KeyboardModifier.ControlModifier):
            new = old & ~hit
        else:
            new = hit
        self.fsel_layer = L["id"]
        self.set_feature_selection(L["id"], new)

    def mouseDoubleClickEvent(self, ev):
        pos = ev.position()
        if self._draft:
            self._finish_draft(True)
            return
        if ev.button() != Qt.MouseButton.LeftButton:
            return
        if self.tool in ("select", "text"):
            if self.page_mode() and self._hit_element(pos) == "title":
                self.edit_title()
                return
            hit = self._hit_item(pos)
            if hit:
                _L, it = self.doc.find_item(hit[1])
                if it is not None and it["type"] in ("text", "symbol"):
                    self.select(*hit)
                    self.edit_item_text(hit[1])
                    return
        if self.tool in ("select", "pan") and self.in_frame(pos):
            self.zoom_map(1.0, pos)

    def wheelEvent(self, ev):
        steps = ev.angleDelta().y() / 120.0
        if not steps:
            return
        pos = ev.position()
        ctrl = bool(ev.modifiers() & Qt.KeyboardModifier.ControlModifier)
        if self.page_mode() and (ctrl or not self.in_frame(pos)):
            self.zoom_page(1.18 ** steps, pos)
        else:
            self.zoom_map(steps * 0.6, pos)

    def keyPressEvent(self, ev):
        k = ev.key()
        mods = ev.modifiers()
        if k == Qt.Key.Key_Space and not ev.isAutoRepeat():
            self._space = True
            self.setCursor(Qt.CursorShape.OpenHandCursor)
            return
        if self._draft:
            if k in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                self._finish_draft(True)
                return
            if k == Qt.Key.Key_Escape:
                self._finish_draft(False)
                return
            if k in (Qt.Key.Key_Backspace, Qt.Key.Key_Delete):
                self._draft.pop()
                self.update()
                return
        if k in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            if self.delete_selection():
                return
        if k == Qt.Key.Key_Escape:
            if self.tool == "marquee" and self.fsel:
                self.clear_feature_selection()
                return
            if self.sel["item"] or self.sel["element"]:
                self.select(self.sel["layer"], None, None)
            elif self.tool != "select":
                self.set_tool("select")
            return
        if k in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and self.sel["item"]:
            self.edit_item_text(self.sel["item"])
            return
        step = 10 if mods & Qt.KeyboardModifier.ShiftModifier else 1
        arrows = {Qt.Key.Key_Left: (-step, 0), Qt.Key.Key_Right: (step, 0), Qt.Key.Key_Up: (0, -step),
                  Qt.Key.Key_Down: (0, step)}
        if k in arrows:
            if self.sel["item"]:
                self.nudge(*arrows[k])
            else:
                dx, dy = arrows[k]
                f = self._frame or self.frame()
                cx, cy, z = self.doc.view
                self.set_view(cx + dx * 8 / f.world, cy + dy * 8 / f.world, z)
            return
        if k in (Qt.Key.Key_Plus, Qt.Key.Key_Equal) and not mods & Qt.KeyboardModifier.ControlModifier:
            self.zoom_map(1.0)
            return
        if k == Qt.Key.Key_Minus and not mods & Qt.KeyboardModifier.ControlModifier:
            self.zoom_map(-1.0)
            return
        super().keyPressEvent(ev)

    def keyReleaseEvent(self, ev):
        if ev.key() == Qt.Key.Key_Space and not ev.isAutoRepeat():
            self._space = False
            self.setCursor(TOOL_CURSORS.get(self.tool, Qt.CursorShape.ArrowCursor))
            return
        super().keyReleaseEvent(ev)

    def leaveEvent(self, ev):
        self._mouse = None
        if self._hover:
            self._hover = None
            self.update()
        self.mouseMoved.emit(float("nan"), float("nan"))
        super().leaveEvent(ev)

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        self.update()

    def shutdown(self):
        self.timgs.shutdown()


def _move_merc(it: dict, dmx: float, dmy: float):
    """Move an item by a distance in Web Mercator units (keeps shapes true on the map)."""
    import numpy as np
    if "lon" in it:
        mx, my = lonlat_to_merc(it["lon"], it["lat"])
        lon, lat = merc_to_lonlat(mx + dmx, my + dmy)
        it["lon"], it["lat"] = float(lon), float(lat)
    if it.get("coords"):
        a = np.asarray(it["coords"], dtype=float)
        mx, my = lonlat_to_merc(a[:, 0], a[:, 1])
        lon, lat = merc_to_lonlat(mx + dmx, my + dmy)
        it["coords"] = [[float(x), float(y)] for x, y in zip(lon, lat)]


__all__ = ["MapCanvas", "TOOL_HINTS", "item_bbox", "merge_page"]
