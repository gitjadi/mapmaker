"""Drawing imported data (table.Table) – fast enough for hundreds of thousands of features.

The same function draws onto any painter through a Mapping (Web Mercator -> painter pixels):
a 256-px map tile on a background thread (the screen), or the whole map frame (exports).
Points are drawn as vector shapes when there are few, and stamped with numpy from a
pre-drawn, anti-aliased marker image when there are many (one marker per pixel is enough)."""
from __future__ import annotations

import math
import threading
from collections import OrderedDict

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QImage, QPainter, QPainterPath, QPen, QPolygonF

from ..table import POINT, Table, class_colors, class_index

HIGHLIGHT = "#00b7ff"
VECTOR_POINT_LIMIT = 40000      # above this many points in view, points are stamped from an image


class Mapping:
    """painter x = (mercator x - x0) * scale; k = painter pixels per reference pixel (1/96 inch)."""

    def __init__(self, x0: float, y0: float, scale: float, w: float, h: float, k: float, ox: float = 0.0,
                 oy: float = 0.0):
        self.x0, self.y0, self.scale, self.w, self.h, self.k, self.ox, self.oy = x0, y0, scale, w, h, k, ox, oy

    def to_px(self, mx, my):
        return (mx - self.x0) * self.scale + self.ox, (my - self.y0) * self.scale + self.oy

    def box(self, margin_px: float = 0.0):
        m = margin_px / self.scale
        return (self.x0 - m, self.y0 - m, self.x0 + self.w / self.scale + m, self.y0 + self.h / self.scale + m)


def frame_mapping(frame) -> Mapping:
    w = frame.world
    r = frame.rect
    return Mapping(frame.cx - r.width() / 2 / w, frame.cy - r.height() / 2 / w, w, r.width(), r.height(), frame.k,
                   r.x(), r.y())


def tile_mapping(z: int, x: int, y: int, px: int, k: float) -> Mapping:
    n = 2 ** z
    return Mapping(x / n, y / n, px * n, px, px, k)


# ---------------------------------------------------------------------- styles
DASH = {"solid": None, "dash": [5.0, 3.0], "dot": [0.8, 2.2], "dashdot": [5.0, 2.5, 0.8, 2.5]}


def _qc(h, a=1.0):
    c = QColor(h or "#000000")
    if a < 1:
        c.setAlphaF(max(0.0, min(1.0, a)))
    return c


def _pen(color, width, dash="solid"):
    pe = QPen(_qc(color) if isinstance(color, str) else color)
    pe.setWidthF(max(0.01, width))
    pe.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    pe.setCapStyle(Qt.PenCapStyle.RoundCap if dash in (None, "solid", "dot") else Qt.PenCapStyle.FlatCap)
    if DASH.get(dash or "solid"):
        pe.setDashPattern(DASH[dash])
    return pe


# ---------------------------------------------------------------------- marker images
_SPRITES: OrderedDict = OrderedDict()
_SPRITE_LOCK = threading.Lock()


def marker_sprite(st: dict, size_px: float, highlight: bool = False) -> np.ndarray:
    """The marker as a premultiplied RGBA float image (s, s, 4), anchor at the centre."""
    marker = st.get("marker", "circle")
    key = (marker, st.get("color"), st.get("outline"), round(float(st.get("outline_width", 1.0)), 2),
           round(size_px, 2), highlight)
    with _SPRITE_LOCK:
        hit = _SPRITES.get(key)
    if hit is not None:
        return hit
    pad = 3
    symbol = marker != "circle" and not highlight
    big = size_px * (1.5 if symbol else 1.0)
    # a symbol's anchor may be at its foot (a pin), so leave room for it in every direction
    s = int(math.ceil((2 * big if symbol else big) + 2 * pad + (6 if highlight else 0)))
    s += s % 2 == 0
    img = QImage(s, s, QImage.Format.Format_RGBA8888_Premultiplied)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    c = QPointF(s / 2, s / 2)
    if highlight:
        p.setPen(_pen(HIGHLIGHT, 2.2))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(c, size_px / 2 + 2.5, size_px / 2 + 2.5)
    elif marker == "circle":
        ow = float(st.get("outline_width", 1.0))
        k = size_px / max(float(st.get("size", 7.0)), 0.1)
        p.setPen(_pen(st.get("outline", "#ffffff"), ow * k) if ow > 0 and st.get("outline") else Qt.PenStyle.NoPen)
        p.setBrush(_qc(st.get("color", "#d95f02")))
        p.drawEllipse(c, size_px / 2, size_px / 2)
    else:
        from .painter import draw_symbol
        draw_symbol(p, c.x(), c.y(), marker, st.get("color", "#d95f02"), big, False, vector=True,
                    halo=st.get("outline") or None)
    p.end()
    ptr = img.constBits()
    arr = np.frombuffer(ptr, dtype=np.uint8, count=img.sizeInBytes()).reshape(s, img.bytesPerLine() // 4, 4)[:, :s]
    out = arr.astype(np.float32) / 255.0
    with _SPRITE_LOCK:
        _SPRITES[key] = out
        while len(_SPRITES) > 64:
            _SPRITES.popitem(last=False)
    return out


def stamp_points(w: int, h: int, xs: np.ndarray, ys: np.ndarray, sprite: np.ndarray,
                 cell: float = 1.0, origin=(0.0, 0.0)) -> QImage | None:
    """Composite the sprite at every (x, y) into a transparent w x h image. Where markers crowd,
    only one per `cell` pixels is drawn – the rest would be hidden under it anyway.
    origin: the image's top-left in whole-world device pixels. Thinning and pixel snapping use
    world positions, so a marker on the border of two map tiles is drawn identically in both."""
    if not len(xs):
        return None
    s = sprite.shape[0]
    half = s // 2
    cell = max(1.0, cell)
    m = half + 2 + cell              # whole thinning cells near the edge, so both neighbours pick the same marker
    ok = (xs > -m) & (xs < w + m) & (ys > -m) & (ys < h + m)
    xs, ys = xs[ok], ys[ok]
    if not len(xs):
        return None
    ox, oy = float(origin[0]), float(origin[1])
    gx = np.floor((xs + ox) / cell).astype(np.int64)
    gy = np.floor((ys + oy) / cell).astype(np.int64)
    key = gy * 4_294_967_311 + gx
    _u, last = np.unique(key[::-1], return_index=True)
    sel = len(key) - 1 - last
    sel.sort()
    pad = s + 1                     # room for a marker centred just outside the image
    W, H = w + 2 * pad, h + 2 * pad
    ix = (np.floor(xs[sel] + ox + 0.5) - round(ox)).astype(np.int64) + pad
    iy = (np.floor(ys[sel] + oy + 0.5) - round(oy)).astype(np.int64) + pad
    inside = (ix >= half) & (ix < W - half) & (iy >= half) & (iy < H - half)
    ix, iy = ix[inside], iy[inside]
    img = np.zeros((H, W, 4), dtype=np.float32)
    flat = img.reshape(-1, 4)
    base = iy * W + ix
    for dy in range(s):
        for dx in range(s):
            px = sprite[dy, dx]
            a = px[3]
            if a <= 0.002:
                continue
            idx = base + ((dy - half) * W + (dx - half))
            flat[idx] = px + flat[idx] * (1.0 - a)
    out = np.ascontiguousarray((np.clip(img[pad:pad + h, pad:pad + w], 0, 1) * 255 + 0.5).astype(np.uint8))
    q = QImage(out.data, w, h, 4 * w, QImage.Format.Format_RGBA8888_Premultiplied)
    return q.copy()


# ---------------------------------------------------------------------- drawing
def _poly(mp: Mapping, arr: np.ndarray) -> QPolygonF:
    x, y = mp.to_px(arr[:, 0], arr[:, 1])
    q = np.round(np.column_stack([x, y]) * 2.5)
    keep = np.ones(len(q), dtype=bool)
    keep[1:] = np.any(q[1:] != q[:-1], axis=1)
    keep[-1] = True
    return QPolygonF([QPointF(a, b) for a, b in zip(x[keep].tolist(), y[keep].tolist())])


def draw_data(p: QPainter, mp: Mapping, L: dict, t: Table, mask: np.ndarray | None = None, vector: bool = False,
              highlight: bool = False, shape_limit: int = 150000, tiled: bool = False) -> int:
    """Draw a data layer's features (or, highlight=True, only the outlines of the masked ones).
    Features are coloured by class when the layer is styled by an attribute. Returns how many
    features were drawn."""
    if t is None or not t.n:
        return 0
    st = L.get("style") or {}
    render = st.get("render", "points")
    if not highlight and render == "heatmap":
        return draw_heatmap(p, mp, L, t, mask)
    if not highlight and render == "clusters":
        return draw_clusters(p, mp, L, t, mask, vector)
    k = mp.k
    prep = t.prepared()
    size = float(st.get("size", 7.0)) * k
    margin = max(size * 1.6, float(st.get("line_width", 1.5)) * k, float(st.get("stroke_width", 1.2)) * k) + 6
    box = mp.box(margin)
    drawn = 0
    ci = None if highlight else class_index(t, st.get("classify"))
    colors = class_colors(st["classify"]) if ci is not None else None
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    # lines and areas; those smaller than about a pixel and a half are drawn as dots, so a layer
    # of many small areas still shows where they are when zoomed out
    if len(prep.shapes):
        all_ids = prep.shapes_in(box)
        if mask is not None and len(all_ids):
            all_ids = all_ids[mask[all_ids]]
        if ci is not None and len(all_ids):
            all_ids = all_ids[ci[all_ids] >= 0]
        if len(all_ids):
            bb = prep.sbb[np.searchsorted(prep.shapes, all_ids)]
            ext = np.maximum(bb[:, 2] - bb[:, 0], bb[:, 3] - bb[:, 1]) * mp.scale
            big = ext >= 1.5
            ids, tiny = all_ids[big][:shape_limit], all_ids[~big]
        else:
            ids = tiny = all_ids
        if len(tiny):
            base = st.get("stroke") if t.kind[tiny[0]] == 2 else st.get("line_color")
            for color, sub in _groups(tiny, ci, colors, HIGHLIGHT if highlight else (base or "#555555")):
                xs, ys = mp.to_px(prep.mx[sub], prep.my[sub])
                dpr = _device_ratio(p)
                dot = {"marker": "circle", "size": 1.0, "outline": None, "color": color}
                img = stamp_points(int(math.ceil(mp.w * dpr)), int(math.ceil(mp.h * dpr)), (xs - mp.ox) * dpr,
                                   (ys - mp.oy) * dpr, marker_sprite(dot, max(1.6, 2.2 * k) * dpr),
                                   origin=_origin(mp, dpr))
                if img is not None:
                    p.drawImage(QRectF(mp.ox, mp.oy, img.width() / dpr, img.height() / dpr), img)
            drawn += len(tiny)
        if len(ids):
            kinds = t.kind[ids]
            cm, rs, fr = prep.cm, t.ring_start, t.feat_ring
            if highlight:
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.setPen(_pen(HIGHLIGHT, 2.4 * max(k, 0.8)))
            fo = float(st.get("fill_opacity", 0.25))
            sw = float(st.get("stroke_width", 1.2))
            for i, kind in zip(ids.tolist(), kinds.tolist()):
                cc = colors[ci[i]] if ci is not None else None
                rings = [cm[rs[r]:rs[r + 1]] for r in range(fr[i], fr[i + 1])]
                if kind == 2:
                    path = QPainterPath()
                    path.setFillRule(Qt.FillRule.OddEvenFill)
                    for r in rings:
                        path.addPolygon(_poly(mp, r))
                        path.closeSubpath()
                    if highlight:
                        p.drawPath(path)
                    else:
                        if fo > 0:
                            p.fillPath(path, QBrush(_qc(cc or st.get("fill", "#888888"), fo)))
                        if sw > 0:
                            p.strokePath(path, _pen(cc or st.get("stroke", "#555555"), sw * k,
                                                    st.get("stroke_dash", "solid")))
                else:
                    if not highlight:
                        p.setBrush(Qt.BrushStyle.NoBrush)
                        p.setPen(_pen(cc or st.get("line_color", "#555555"), float(st.get("line_width", 1.5)) * k,
                                      st.get("dash", "solid")))
                    for r in rings:
                        p.drawPolyline(_poly(mp, r))
                drawn += 1
    # points
    if len(prep.points):
        ids = prep.points_in(box)
        if mask is not None and len(ids):
            ids = ids[mask[ids]]
        if ci is not None and len(ids):
            ids = ids[ci[ids] >= 0]
        if len(ids):
            drawn += len(ids)
            if highlight and len(ids) <= 6000 and not tiled:
                xs, ys = mp.to_px(prep.mx[ids], prep.my[ids])
                p.setPen(_pen(HIGHLIGHT, 2.2 * max(k, 0.8)))
                p.setBrush(Qt.BrushStyle.NoBrush)
                r = size / 2 + 2.5 * max(k, 0.8)
                for x, y in zip(xs.tolist(), ys.tolist()):
                    p.drawEllipse(QPointF(x, y), r, r)
                return drawn
            few = len(ids) <= (VECTOR_POINT_LIMIT if vector else 3000)
            for color, sub in _groups(ids, ci, colors, st.get("color", "#d95f02")):
                xs, ys = mp.to_px(prep.mx[sub], prep.my[sub])
                sst = dict(st, color=color)
                if few and not highlight and not tiled:
                    _vector_points(p, xs, ys, sst, size)
                else:
                    dpr = _device_ratio(p)
                    sprite = marker_sprite(sst, size * dpr, highlight)
                    w, h = int(math.ceil(mp.w * dpr)), int(math.ceil(mp.h * dpr))
                    img = stamp_points(w, h, (xs - mp.ox) * dpr, (ys - mp.oy) * dpr, sprite,
                                       cell=size * dpr * 0.45 if (tiled or len(ids) > 3000) else 1.0,
                                       origin=_origin(mp, dpr))
                    if img is not None:
                        p.drawImage(QRectF(mp.ox, mp.oy, w / dpr, h / dpr), img)
    return drawn


def _groups(ids, ci, colors, default):
    """(colour, ids) per class, last class first so the first class in the legend ends up on top."""
    if ci is None:
        return [(default, ids)]
    c = ci[ids]
    out = []
    for kk in range(len(colors) - 1, -1, -1):
        sub = ids[c == kk]
        if len(sub):
            out.append((colors[kk], sub))
    return out


# ---------------------------------------------------------------------- heat maps and clusters
def draw_heatmap(p: QPainter, mp: Mapping, L: dict, t: Table, mask=None) -> int:
    """Density of the features as a smooth colour surface (kernel density, Gaussian kernel)."""
    from scipy.ndimage import gaussian_filter

    from ..styles import HEAT_RAMPS
    st = L.get("style") or {}
    k = mp.k
    radius = max(2.0, float(st.get("heat_radius", 20)) * k)
    prep = t.prepared()
    box = mp.box(radius * 3)
    x0, y0, x1, y1 = box
    ok = np.isfinite(prep.mx) & (prep.mx >= x0) & (prep.mx <= x1) & (prep.my >= y0) & (prep.my <= y1)
    if mask is not None:
        ok &= mask
    ci = class_index(t, st.get("classify"))
    if ci is not None:
        ok &= ci >= 0
    ids = np.nonzero(ok)[0]
    if not len(ids):
        return 0
    dpr = _device_ratio(p)
    res = max(1.0, radius * dpr / 5.0)                   # grid cell in device pixels
    pad = int(math.ceil(radius * 3 * dpr / res))
    gw = int(math.ceil(mp.w * dpr / res)) + 2 * pad
    gh = int(math.ceil(mp.h * dpr / res)) + 2 * pad
    xs, ys = mp.to_px(prep.mx[ids], prep.my[ids])
    gx = np.floor((xs - mp.ox) * dpr / res).astype(np.int64) + pad
    gy = np.floor((ys - mp.oy) * dpr / res).astype(np.int64) + pad
    inside = (gx >= 0) & (gx < gw) & (gy >= 0) & (gy < gh)
    w = None
    wf = st.get("heat_weight")
    if wf and wf in t.cols:
        from ..table import numeric_values
        w = np.nan_to_num(numeric_values(t, wf)[ids][inside], nan=0.0)
    grid = np.zeros((gh, gw), dtype=np.float64)
    np.add.at(grid, (gy[inside], gx[inside]), 1.0 if w is None else w)
    grid = gaussian_filter(grid, sigma=radius * dpr / res / 2.0, mode="constant")
    grid = grid[pad:gh - pad, pad:gw - pad]
    nz = grid[grid > grid.max() * 1e-4] if grid.max() > 0 else grid[:0]
    if not len(nz):
        return 0
    vmax = float(np.percentile(nz, 99.5)) / max(0.05, float(st.get("heat_intensity", 1.0)))
    v = np.clip(grid / max(vmax, 1e-12), 0, 1)
    ramp = HEAT_RAMPS.get(st.get("heat_ramp", "heat"), HEAT_RAMPS["heat"])
    pos = np.array([r[0] for r in ramp])
    rgba = np.zeros(v.shape + (4,), dtype=np.float32)
    for ch in range(4):
        rgba[..., ch] = np.interp(v, pos, [r[1][ch] for r in ramp])
    a = rgba[..., 3:4] / 255.0
    out = np.ascontiguousarray(np.concatenate([rgba[..., :3] * a, rgba[..., 3:4]], axis=2).astype(np.uint8))
    h, wd = out.shape[:2]
    img = QImage(out.data, wd, h, 4 * wd, QImage.Format.Format_RGBA8888_Premultiplied).copy()
    p.save()
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    p.drawImage(QRectF(mp.ox, mp.oy, wd * res / dpr, h * res / dpr), img)
    p.restore()
    return len(ids)


def _short(n: int) -> str:
    if n >= 10000:
        return f"{n / 1000:.0f}k"
    if n >= 1000:
        return f"{n / 1000:.1f}k".replace(".0k", "k")
    return str(n)


def draw_clusters(p: QPainter, mp: Mapping, L: dict, t: Table, mask=None, vector=False) -> int:
    """Nearby features gathered into circles sized by how many they hold (a grid in map space,
    so the groups stay put while you pan)."""
    from .painter import draw_text
    st = L.get("style") or {}
    k = mp.k
    cell = max(20.0, float(st.get("cluster_px", 56))) * k
    prep = t.prepared()
    x0, y0, x1, y1 = mp.box(cell)
    ok = np.isfinite(prep.mx) & (prep.mx >= x0) & (prep.mx <= x1) & (prep.my >= y0) & (prep.my <= y1)
    if mask is not None:
        ok &= mask
    ci = class_index(t, st.get("classify"))
    if ci is not None:
        ok &= ci >= 0
    ids = np.nonzero(ok)[0]
    if not len(ids):
        return 0
    gxw = prep.mx[ids] * mp.scale / cell
    gyw = prep.my[ids] * mp.scale / cell
    key = np.floor(gyw).astype(np.int64) * 4_294_967_311 + np.floor(gxw).astype(np.int64)
    order = np.argsort(key, kind="stable")
    ks = key[order]
    starts = np.concatenate([[0], np.nonzero(np.diff(ks))[0] + 1])
    counts = np.diff(np.concatenate([starts, [len(ks)]]))
    sx = np.add.reduceat(prep.mx[ids][order], starts) / counts
    sy = np.add.reduceat(prep.my[ids][order], starts) / counts
    px, py = mp.to_px(sx, sy)
    color = st.get("color", "#d95f02")
    single = counts == 1
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    if single.any():
        _vector_points(p, px[single], py[single], st, float(st.get("size", 7.0)) * k)
    text = {"font": "Segoe UI", "size": 8.0, "bold": True, "color": "#ffffff", "halo": "", "align": "center"}
    big = np.nonzero(~single)[0]
    for j in big[np.argsort(counts[big])].tolist():
        n = int(counts[j])
        r = (9.0 + 4.2 * math.log10(n) ** 1.3 * 2.2) * k
        p.setPen(_pen("#ffffff", 1.6 * k))
        p.setBrush(_qc(color, 0.88))
        p.drawEllipse(QPointF(float(px[j]), float(py[j])), r, r)
        draw_text(p, float(px[j]), float(py[j]), _short(n), dict(text, size=7.5 + min(3.0, math.log10(n))), k)
    return len(ids)


def _origin(mp: Mapping, dpr: float):
    return mp.x0 * mp.scale * dpr, mp.y0 * mp.scale * dpr


def _device_ratio(p: QPainter) -> float:
    dev = p.device()
    try:
        r = dev.devicePixelRatioF()
    except Exception:
        r = 1.0
    sc = math.hypot(p.transform().m11(), p.transform().m12()) or 1.0
    return max(0.25, r * sc)


def _vector_points(p: QPainter, xs, ys, st, size):
    marker = st.get("marker", "circle")
    if marker == "circle":
        k = size / max(float(st.get("size", 7.0)), 0.1)
        ow = float(st.get("outline_width", 1.0))
        p.setPen(_pen(st.get("outline", "#ffffff"), ow * k) if ow > 0 and st.get("outline") else Qt.PenStyle.NoPen)
        p.setBrush(_qc(st.get("color", "#d95f02")))
        r = size / 2
        for x, y in zip(xs.tolist(), ys.tolist()):
            p.drawEllipse(QPointF(x, y), r, r)
    else:
        from .painter import draw_symbol
        for x, y in zip(xs.tolist(), ys.tolist()):
            draw_symbol(p, x, y, marker, st.get("color", "#d95f02"), size * 1.5, False, True,
                        st.get("outline") or None)


def render_tile(L: dict, t: Table, z: int, x: int, y: int, px: int, k: float, mask=None, highlight=False):
    """One map tile of a data layer, as a QImage (None if empty). Runs on a worker thread."""
    img = QImage(px, px, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    try:
        n = draw_data(p, tile_mapping(z, x, y, px, k), L, t, mask, highlight=highlight, tiled=True)
    finally:
        p.end()
    return img if n else None


# ---------------------------------------------------------------------- labels
def draw_labels(p: QPainter, mp: Mapping, L: dict, t: Table, mask=None, limit: int = 2500, max_candidates=60000) -> int:
    """Label the features in view with the chosen field; labels that would overlap are left out."""
    from .painter import draw_text, text_bounds
    lab = L.get("label") or {}
    field_ = lab.get("field")
    if not field_ or field_ not in t.cols or not t.n:
        return 0
    prep = t.prepared()
    k = mp.k
    x0, y0, x1, y1 = mp.box(0)
    ok = (prep.mx >= x0) & (prep.mx <= x1) & (prep.my >= y0) & (prep.my <= y1)
    if mask is not None:
        ok &= mask
    idx = np.nonzero(ok)[0]
    if len(idx) > max_candidates:
        return -1
    col = t.cols[field_]
    xs, ys = mp.to_px(prep.mx[idx], prep.my[idx])
    st = dict(lab.get("style") or {})
    marker_r = float((L.get("style") or {}).get("size", 7.0)) * k / 2
    placed: dict = {}
    cell = 60.0 * k
    count = 0
    for i, x, y in zip(idx.tolist(), xs.tolist(), ys.tolist()):
        text = col.text(i)
        if not text:
            continue
        if t.kind[i] == POINT:
            st["align"] = "left"
            ax, ay = x + marker_r + 2 * k, y - marker_r * 0.9
        else:
            st["align"] = "center"
            ax, ay = x, y
        b = text_bounds(text, st, k).translated(ax, ay)
        gx0, gy0, gx1, gy1 = int(b.left() // cell), int(b.top() // cell), int(b.right() // cell), int(b.bottom() // cell)
        clash = False
        for gx in range(gx0, gx1 + 1):
            for gy in range(gy0, gy1 + 1):
                if any(o.intersects(b) for o in placed.get((gx, gy), ())):
                    clash = True
                    break
            if clash:
                break
        if clash:
            continue
        for gx in range(gx0, gx1 + 1):
            for gy in range(gy0, gy1 + 1):
                placed.setdefault((gx, gy), []).append(b)
        draw_text(p, ax, ay, text, st, k)
        count += 1
        if count >= limit:
            break
    return count


# ---------------------------------------------------------------------- picking
def pick(t: Table, mp: Mapping, x: float, y: float, tol: float = 6.0, mask=None) -> int | None:
    """The feature under painter position (x, y), or None. Points first, then lines, then areas."""
    if t is None or not t.n:
        return None
    prep = t.prepared()
    st_r = tol / mp.scale
    mx = (x - mp.ox) / mp.scale + mp.x0
    my = (y - mp.oy) / mp.scale + mp.y0
    if len(prep.points):
        p = prep.points
        d = np.hypot(prep.mx[p] - mx, prep.my[p] - my)
        if mask is not None:
            d = np.where(mask[p], d, np.inf)
        j = int(np.argmin(d))
        if d[j] <= st_r * 1.6:
            return int(p[j])
    box = (mx - st_r, my - st_r, mx + st_r, my + st_r)
    ids = prep.shapes_in(box)
    if mask is not None and len(ids):
        ids = ids[mask[ids]]
    cm, rs, fr = prep.cm, t.ring_start, t.feat_ring
    pt = QPointF(x, y)
    for i in ids[::-1].tolist():
        rings = [cm[rs[r]:rs[r + 1]] for r in range(fr[i], fr[i + 1])]
        if t.kind[i] == 1:
            for r in rings:
                if _near(r, mx, my, st_r):
                    return i
        else:
            path = QPainterPath()
            path.setFillRule(Qt.FillRule.OddEvenFill)
            for r in rings:
                path.addPolygon(_poly(mp, r))
            if path.contains(pt):
                return i
    return None


def in_rect(t: Table, x0, y0, x1, y1) -> np.ndarray:
    """Features whose representative point (or any point, for points) lies in a Mercator rectangle."""
    prep = t.prepared()
    ok = (prep.mx >= x0) & (prep.mx <= x1) & (prep.my >= y0) & (prep.my <= y1)
    if len(prep.shapes):
        b = prep.sbb
        inside = (b[:, 0] >= x0) & (b[:, 2] <= x1) & (b[:, 1] >= y0) & (b[:, 3] <= y1)
        ok[prep.shapes] |= inside
    return ok


def _near(a: np.ndarray, x, y, t) -> bool:
    if len(a) < 2:
        return False
    p0, p1 = a[:-1], a[1:]
    d = p1 - p0
    L2 = (d ** 2).sum(1)
    L2[L2 == 0] = 1e-30
    u = np.clip(((x - p0[:, 0]) * d[:, 0] + (y - p0[:, 1]) * d[:, 1]) / L2, 0, 1)
    return bool((np.hypot(p0[:, 0] + u * d[:, 0] - x, p0[:, 1] + u * d[:, 1] - y) <= t).any())


__all__ = ["Mapping", "frame_mapping", "tile_mapping", "draw_data", "render_tile", "draw_labels", "pick", "in_rect",
           "HIGHLIGHT", "draw_heatmap", "draw_clusters"]
