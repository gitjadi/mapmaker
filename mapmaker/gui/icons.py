"""Interface icons, drawn as small line SVGs on a 24-unit grid and coloured to fit the theme."""
from __future__ import annotations

from PySide6.QtCore import QByteArray, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QIcon, QImage, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

from .. import symbols as SYM

_ICONS = {
    # tools
    "select": '<path d="M5 3.5 18.5 12l-6 1.6 3.4 6.4-2.6 1.3-3.3-6.4L5.6 19z" fill="{c}" stroke="none"/>',
    "marquee": '<rect x="3.5" y="4.5" width="14" height="11" stroke-dasharray="2.6 2.2"/>'
               '<path d="M13.5 12.5l7 2.6-3.1 1.1-1.2 3.2z" fill="{c}"/>',
    "pan": '<path d="M8 11V5.5a1.5 1.5 0 0 1 3 0V11m0-1V4a1.5 1.5 0 0 1 3 0v6m0-.5V5.5a1.5 1.5 0 0 1 3 0V13'
           'm-9-1.5V9a1.5 1.5 0 0 0-3 0v5c0 4 2.7 7 6.5 7h1c3.3 0 5.5-2.7 5.5-6.5V11a1.5 1.5 0 0 0-3 0"/>',
    "text": '<path d="M5 6V4.5h14V6M12 4.5v15M9 19.5h6"/>',
    "symbol": '<path d="M12 21s-6.5-6.3-6.5-11a6.5 6.5 0 0 1 13 0c0 4.7-6.5 11-6.5 11z"/><circle cx="12" cy="10" r="2.3"/>',
    "line": '<path d="M4 18 9.5 9l5 6L20 5"/><circle cx="4" cy="18" r="1.6" fill="{c}"/><circle cx="9.5" cy="9" r="1.6" '
            'fill="{c}"/><circle cx="14.5" cy="15" r="1.6" fill="{c}"/><circle cx="20" cy="5" r="1.6" fill="{c}"/>',
    "area": '<path d="M4.5 8 11 4l8.5 4.5-2 10L7 20z" fill="{c}" fill-opacity=".25"/>',
    # layer kinds
    "k_basemap": '<path d="M3.5 6.5 9 4.5l6 2 5.5-2v13l-5.5 2-6-2-5.5 2z"/><path d="M9 4.5v13M15 6.5v13"/>',
    "k_terrain": '<path d="M2.5 19 9 8l4 6 2.5-3.5L21.5 19z"/><path d="M7.4 10.7 9 12.5l1.6-1.8" />',
    "k_service": '<circle cx="12" cy="12" r="8.5"/><path d="M3.5 12h17M12 3.5c2.6 2.6 3.5 5.4 3.5 8.5s-.9 5.9-3.5 8.5'
                 'c-2.6-2.6-3.5-5.4-3.5-8.5s.9-5.9 3.5-8.5z"/>',
    "k_data": '<ellipse cx="12" cy="6" rx="7.5" ry="2.8"/><path d="M4.5 6v12c0 1.5 3.4 2.8 7.5 2.8s7.5-1.3 7.5-2.8V6'
              'M4.5 12c0 1.5 3.4 2.8 7.5 2.8s7.5-1.3 7.5-2.8"/>',
    "k_annotations": '<path d="M5 6V4.5h9V6M9.5 4.5V15"/><path d="M17 21s-3.6-3.5-3.6-6.1a3.6 3.6 0 0 1 7.2 0'
                     'c0 2.6-3.6 6.1-3.6 6.1z" fill="{c}"/>',
    "k_grid": '<path d="M4 8.5h16M4 15.5h16M8.5 4v16M15.5 4v16"/>',
    "table": '<rect x="3.5" y="5" width="17" height="14" rx="1.5"/><path d="M3.5 9.5h17M3.5 14h17M9.5 9.5V19"/>',
    "image": '<rect x="3.5" y="5" width="17" height="14" rx="1.5"/><circle cx="9" cy="10" r="1.6"/>'
             '<path d="M4 17l5-4.5 3.5 3 3-2.5 4.5 4"/>',
    "series": '<rect x="7" y="3.5" width="12" height="15" rx="1"/><path d="M5 6v14.5h11"/>',
    "k_color": '<rect x="4" y="4" width="16" height="16" rx="2" fill="{c}" fill-opacity=".35"/>',
    # items
    "i_text": '<path d="M6 7V5.5h12V7M12 5.5v13M9.5 18.5h5"/>',
    "i_line": '<path d="M4 18 9.5 9l5 6L20 5"/>',
    "i_area": '<path d="M4.5 8 11 4l8.5 4.5-2 10L7 20z" fill="{c}" fill-opacity=".25"/>',
    # panel buttons
    "eye": '<path d="M2.5 12S6 5.5 12 5.5 21.5 12 21.5 12 18 18.5 12 18.5 2.5 12 2.5 12z"/><circle cx="12" cy="12" r="3"/>',
    "eye_off": '<path d="M2.5 12S6 5.5 12 5.5 21.5 12 21.5 12 18 18.5 12 18.5 2.5 12 2.5 12z" stroke-opacity=".35"/>'
               '<path d="M4 20 20 4"/>',
    "lock": '<rect x="5.5" y="10.5" width="13" height="9.5" rx="1.5" fill="{c}"/><path d="M8.5 10.5V8a3.5 3.5 0 0 1 7 0v2.5"/>',
    "unlock": '<rect x="5.5" y="10.5" width="13" height="9.5" rx="1.5" stroke-opacity=".35"/>'
              '<path d="M8.5 10.5V8a3.5 3.5 0 0 1 6.8-1.2" stroke-opacity=".35"/>',
    "add": '<path d="M12 5v14M5 12h14"/>',
    "delete": '<path d="M4.5 7h15M9.5 7V4.5h5V7M6.5 7l1 13h9l1-13M10 10.5v6M14 10.5v6"/>',
    "duplicate": '<rect x="8" y="8" width="12" height="12" rx="1.5"/><path d="M16 8V5.5A1.5 1.5 0 0 0 14.5 4h-9A1.5 1.5 0 0 0 4 5.5v9'
                 'A1.5 1.5 0 0 0 5.5 16H8"/>',
    "up": '<path d="M12 19V5M6 11l6-6 6 6"/>',
    "down": '<path d="M12 5v14M6 13l6 6 6-6"/>',
    "zoomto": '<circle cx="10.5" cy="10.5" r="6"/><path d="M15 15l5 5M8 10.5h5M10.5 8v5"/>',
    "page": '<rect x="5" y="3.5" width="14" height="17" rx="1"/><rect x="7.5" y="8" width="9" height="7.5" '
            'fill="{c}" fill-opacity=".3"/><path d="M7.5 5.8h6"/>',
    "map": '<path d="M3.5 6.5 9 4.5l6 2 5.5-2v13l-5.5 2-6-2-5.5 2z"/>',
    "undo": '<path d="M9 7 4.5 11.5 9 16"/><path d="M4.5 11.5H15a4.5 4.5 0 0 1 0 9h-2"/>',
    "redo": '<path d="M15 7l4.5 4.5L15 16"/><path d="M19.5 11.5H9a4.5 4.5 0 0 0 0 9h2"/>',
    "export": '<path d="M12 15V4M7.5 8.5 12 4l4.5 4.5M5 14v5.5h14V14"/>',
}


def svg(name: str, color: str = "#d8d8d8", width: float = 1.7) -> bytes:
    body = _ICONS.get(name, _ICONS["add"]).replace("{c}", color)
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{color}" '
            f'stroke-width="{width}" stroke-linecap="round" stroke-linejoin="round">{body}</svg>').encode()


def pixmap(name: str, size: int = 20, color: str = "#d8d8d8", dpr: float = 2.0) -> QPixmap:
    img = QImage(int(size * dpr), int(size * dpr), QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    QSvgRenderer(QByteArray(svg(name, color))).render(p, QRectF(0, 0, size * dpr, size * dpr))
    p.end()
    pm = QPixmap.fromImage(img)
    pm.setDevicePixelRatio(dpr)
    return pm


_cache: dict = {}


def icon(name: str, color: str = "#d8d8d8", active: str | None = None, size: int = 20) -> QIcon:
    key = (name, color, active, size)
    ic = _cache.get(key)
    if ic is None:
        ic = QIcon()
        ic.addPixmap(pixmap(name, size, color), QIcon.Mode.Normal, QIcon.State.Off)
        ic.addPixmap(pixmap(name, size, "#6e6e6e"), QIcon.Mode.Disabled, QIcon.State.Off)
        if active:
            ic.addPixmap(pixmap(name, size, active), QIcon.Mode.Normal, QIcon.State.On)
            ic.addPixmap(pixmap(name, size, active), QIcon.Mode.Active, QIcon.State.On)
        _cache[key] = ic
    return ic


def symbol_icon(key: str, color: str = "#e4e4e4", size: int = 22, badge: bool = False) -> QIcon:
    """A map symbol as an icon (for pickers and the layer list)."""
    ck = ("sym", key, color, size, badge)
    ic = _cache.get(ck)
    if ic is None:
        from .painter import draw_symbol
        dpr = 2.0
        img = QImage(int(size * dpr), int(size * dpr), QImage.Format.Format_ARGB32_Premultiplied)
        img.fill(Qt.GlobalColor.transparent)
        p = QPainter(img)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        s = size * dpr
        if badge:
            draw_symbol(p, s / 2, s / 2, key, color, s * 0.92, True, vector=True)
        else:
            ax, ay = SYM.anchor(key)
            draw_symbol(p, s / 2 + (ax - 0.5) * s, s / 2 + (ay - 0.5) * s, key, color, s, False, vector=True, halo=None)
        p.end()
        pm = QPixmap.fromImage(img)
        pm.setDevicePixelRatio(dpr)
        ic = QIcon(pm)
        _cache[ck] = ic
    return ic


def swatch_icon(color: str, size: int = 16, outline: str = "#5a5a5a") -> QIcon:
    pm = QPixmap(QSize(size * 2, size * 2))
    pm.setDevicePixelRatio(2)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(QColor(outline))
    p.setBrush(QColor(color))
    p.drawRoundedRect(QRectF(1, 1, size - 2, size - 2), 3, 3)
    p.end()
    return QIcon(pm)
