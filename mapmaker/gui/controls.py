"""Small controls for the panels: colour swatch buttons with cartographic palettes,
a symbol picker, and a slider with a number box."""
from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QColorDialog, QGridLayout, QHBoxLayout, QLabel, QMenu, QSlider, QSpinBox, QToolButton,
                               QVBoxLayout, QWidget, QWidgetAction)

from .. import symbols as SYM
from ..styles import PALETTES
from .icons import swatch_icon, symbol_icon
from .theme import T


class _Grid(QWidget):
    picked = Signal(str)

    def __init__(self, rows: list[tuple[str, list[tuple[str, str]]]], cols: int, size: int, parent=None):
        """rows: [(heading, [(value, tooltip, icon)])]"""
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 6, 8, 6)
        lay.setSpacing(3)
        for heading, cells in rows:
            if heading:
                h = QLabel(heading)
                h.setStyleSheet(f"color:{T.muted}; font-size:11px;")
                lay.addWidget(h)
            g = QGridLayout()
            g.setSpacing(3)
            for i, (value, tip, ic) in enumerate(cells):
                b = QToolButton()
                b.setIcon(ic)
                b.setIconSize(QSize(size, size))
                b.setToolTip(tip)
                b.setAutoRaise(True)
                b.setStyleSheet("QToolButton{padding:2px;border-radius:4px;} QToolButton:hover{background:%s;}" % T.hover)
                b.clicked.connect(lambda _=False, v=value: self.picked.emit(v))
                g.addWidget(b, i // cols, i % cols)
            lay.addLayout(g)


class ColorButton(QToolButton):
    """A colour swatch; click for map colours and ColorBrewer schemes, or any colour."""
    colorChanged = Signal(str)

    def __init__(self, color: str = "#000000", allow_none: bool = False, none_label: str = "None", parent=None):
        super().__init__(parent)
        self.setObjectName("swatch")
        self._color = color or ""
        self._allow_none = allow_none
        self._none_label = none_label
        self.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.setIconSize(QSize(34, 16))
        self.setFixedHeight(26)
        self._menu = QMenu(self)
        self.setMenu(self._menu)
        self._menu.aboutToShow.connect(self._build)
        self._update()

    def color(self) -> str:
        return self._color

    def set_color(self, c: str, emit: bool = False):
        self._color = c or ""
        self._update()
        if emit:
            self.colorChanged.emit(self._color)

    def _update(self):
        if self._color:
            self.setIcon(_wide_swatch(self._color))
            self.setToolTip(self._color)
        else:
            self.setIcon(_wide_swatch("", none=True))
            self.setToolTip(self._none_label)

    def _build(self):
        m = self._menu
        m.clear()
        rows = [(name, [(c, c, swatch_icon(c, 16)) for c in cols]) for name, cols in PALETTES]
        grid = _Grid(rows, 8, 16)
        grid.picked.connect(self._pick)
        wa = QWidgetAction(m)
        wa.setDefaultWidget(grid)
        m.addAction(wa)
        m.addSeparator()
        if self._allow_none:
            m.addAction(self._none_label, lambda: self._pick(""))
        m.addAction("More colours…", self._more)

    def _pick(self, c):
        self._menu.close()
        self.set_color(c, emit=True)

    def _more(self):
        c = QColorDialog.getColor(QColor(self._color or "#808080"), self, "Choose a colour")
        if c.isValid():
            self.set_color(c.name(), emit=True)


def _wide_swatch(color: str, none: bool = False):
    from PySide6.QtCore import QRectF
    from PySide6.QtGui import QIcon, QPainter, QPen, QPixmap
    pm = QPixmap(QSize(68, 32))
    pm.setDevicePixelRatio(2)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    r = QRectF(0.5, 0.5, 33, 15)
    if none:
        p.setPen(QPen(QColor(T.faint), 1))
        p.setBrush(QColor(T.base))
        p.drawRoundedRect(r, 3, 3)
        p.setPen(QPen(QColor("#d05050"), 1.4))
        p.drawLine(r.bottomLeft(), r.topRight())
    else:
        p.setPen(QPen(QColor(0, 0, 0, 90), 1))
        p.setBrush(QColor(color))
        p.drawRoundedRect(r, 3, 3)
    p.end()
    return QIcon(pm)


class SymbolPicker(QToolButton):
    symbolChanged = Signal(str)

    def __init__(self, key: str = "pin", parent=None):
        super().__init__(parent)
        self.setObjectName("swatch")
        self._key = key
        self.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.setIconSize(QSize(20, 20))
        self.setFixedHeight(28)
        self._menu = QMenu(self)
        self.setMenu(self._menu)
        self._menu.aboutToShow.connect(self._build)
        self._update()

    def key(self) -> str:
        return self._key

    def set_key(self, k: str, emit: bool = False):
        self._key = k
        self._update()
        if emit:
            self.symbolChanged.emit(k)

    def _update(self):
        self.setIcon(symbol_icon(self._key, T.text, 22))
        self.setToolTip(SYM.label(self._key))

    def _build(self):
        m = self._menu
        m.clear()
        rows = [(cat, [(k, SYM.label(k), symbol_icon(k, T.text, 22)) for k in keys]) for cat, keys in SYM.by_category()]
        grid = _Grid(rows, 7, 22)
        grid.picked.connect(self._pick)
        wa = QWidgetAction(m)
        wa.setDefaultWidget(grid)
        m.addAction(wa)

    def _pick(self, k):
        self._menu.close()
        self.set_key(k, emit=True)


class SliderSpin(QWidget):
    """Slider plus number box; value() in the slider's own units (e.g. 0–100 %)."""
    valueChanged = Signal(int)
    editingFinished = Signal()

    def __init__(self, lo: int = 0, hi: int = 100, value: int = 100, suffix: str = " %", parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(lo, hi)
        self.spin = QSpinBox()
        self.spin.setRange(lo, hi)
        self.spin.setSuffix(suffix)
        self.spin.setFixedWidth(68)
        self.spin.setAlignment(Qt.AlignmentFlag.AlignRight)
        lay.addWidget(self.slider, 1)
        lay.addWidget(self.spin)
        self.set_value(value)
        self.slider.valueChanged.connect(self._from_slider)
        self.spin.valueChanged.connect(self._from_spin)
        self.slider.sliderReleased.connect(self.editingFinished.emit)

    def value(self) -> int:
        return self.spin.value()

    def set_value(self, v: int):
        for w in (self.slider, self.spin):
            w.blockSignals(True)
            w.setValue(int(v))
            w.blockSignals(False)

    def _from_slider(self, v):
        self.spin.blockSignals(True)
        self.spin.setValue(v)
        self.spin.blockSignals(False)
        self.valueChanged.emit(v)

    def _from_spin(self, v):
        self.slider.blockSignals(True)
        self.slider.setValue(v)
        self.slider.blockSignals(False)
        self.valueChanged.emit(v)
