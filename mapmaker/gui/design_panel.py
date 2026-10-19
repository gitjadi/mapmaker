"""The Design tab: how the finished map looks – a theme (light, dark, parchment …) and the
styles of the frame, title, legend, scale bar, north arrow and sources line."""
from __future__ import annotations

from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox, QFormLayout, QGridLayout, QLabel, QScrollArea,
                               QSpinBox, QToolButton, QVBoxLayout, QWidget)

from ..styles import (DESIGN_DEFAULTS, FONTS, LEGEND_STYLES, NORTH_STYLES, SERIF, THEMES, design, text_style)
from ..tiles import BASEMAPS_BY_ID
from .controls import ColorButton
from .theme import T


def apply_theme(doc, key: str, restyle_labels: bool = True, switch_maps: bool = True):
    """Set a theme's colours on the page elements, and optionally restyle labels and background maps."""
    th = THEMES[key]
    d = dict(DESIGN_DEFAULTS)
    d.update(th["design"])
    d["theme"] = key
    page = doc.page
    page["design"] = d
    page["background"] = th["page"]
    page.setdefault("title_style", {})["color"] = th["title"]
    if d["font"] == SERIF:
        page["title_style"]["font"] = SERIF
    if switch_maps:
        for L in doc.layers:
            if L["kind"] == "basemap" and L.get("source") in ("esri-light", "esri-dark"):
                old = BASEMAPS_BY_ID.get(L["source"])
                L["source"] = th["basemap"]
                if old is not None and L["name"] == old.name:
                    L["name"] = BASEMAPS_BY_ID[th["basemap"]].name
                L["opacity"] = th.get("basemap_opacity", 1.0)
    if restyle_labels:
        def restyle(st: dict, preset: str | None):
            base = text_style(preset or "place")
            if th["label_color"] is None:
                st["color"] = base["color"]
            elif preset == "water" and th.get("water"):
                st["color"] = th["water"]
            else:
                st["color"] = th["label_color"]
            if st.get("halo"):
                st["halo"] = th["label_halo"]
        for L in doc.layers:
            for it in L.get("items", []):
                if it["type"] == "text" and not (it.get("style") or {}).get("box"):
                    restyle(it["style"], it.get("preset"))
                elif it["type"] == "symbol" and it.get("label_style"):
                    restyle(it["label_style"], "place")
            if L["kind"] == "data" and (L.get("label") or {}).get("style"):
                restyle(L["label"]["style"], "feature")


def _theme_icon(th: dict, size=(54, 38)) -> QIcon:
    w, h = size
    pm = QPixmap(QSize(w * 2, h * 2))
    pm.setDevicePixelRatio(2)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    d = dict(DESIGN_DEFAULTS)
    d.update(th["design"])
    p.setPen(QColor(0, 0, 0, 90))
    p.setBrush(QColor(th["page"]))
    p.drawRoundedRect(QRectF(0.5, 0.5, w - 1, h - 1), 3, 3)
    p.setPen(QColor(d["frame_color"]))
    p.setBrush(QColor(d["map_bg"]))
    p.drawRect(QRectF(5, 10, w - 10, h - 15))
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(th["title"]))
    p.drawRect(QRectF(5, 4, 20, 3))
    p.setBrush(QColor(d["legend_bg"]))
    p.drawRect(QRectF(w - 19, h - 14, 12, 8))
    p.setBrush(QColor(d["text"]))
    p.drawRect(QRectF(w - 17, h - 12, 8, 1.4))
    p.drawRect(QRectF(w - 17, h - 9, 6, 1.4))
    p.end()
    return QIcon(pm)


class DesignPanel(QWidget):
    def __init__(self, win, parent=None):
        super().__init__(parent)
        self.win = win
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        lay.addWidget(self.scroll)
        self._doc_id = None

    # ------------------------------------------------------------ helpers
    def _section(self, title):
        h = QLabel(title.upper())
        h.setObjectName("group")
        self._v.addWidget(h)
        f = QFormLayout()
        f.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        f.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        f.setVerticalSpacing(6)
        self._v.addLayout(f)
        return f

    def _note(self, text):
        lab = QLabel(text)
        lab.setWordWrap(True)
        lab.setObjectName("hint")
        self._v.addWidget(lab)

    def _set(self, key, label):
        def fn(v):
            def apply():
                d = self.win.doc.page.setdefault("design", {})
                d[key] = v
            self.win.edit(label, apply, merge=("design", key), from_props=True)
        return fn

    def _setp(self, path, label):
        def fn(v):
            def apply():
                d = self.win.doc.page
                for k in path[:-1]:
                    d = d.setdefault(k, {})
                d[path[-1]] = v
            self.win.edit(label, apply, merge=("page", tuple(path)), from_props=True)
        return fn

    def _color(self, d, key, label, allow_none=False):
        b = ColorButton(d.get(key) or "", allow_none=allow_none)
        b.colorChanged.connect(self._set(key, label))
        return b

    def _combo(self, items: dict, current, fn):
        c = QComboBox()
        for k, v in items.items():
            c.addItem(v, k)
        c.setCurrentIndex(max(0, c.findData(current)))
        c.currentIndexChanged.connect(lambda: fn(c.currentData()))
        return c

    def _dspin(self, lo, hi, val, step, dec, suffix, fn):
        s = QDoubleSpinBox()
        s.setRange(lo, hi)
        s.setDecimals(dec)
        s.setSingleStep(step)
        s.setValue(float(val))
        s.setSuffix(suffix)
        s.setKeyboardTracking(False)
        s.valueChanged.connect(fn)
        return s

    # ------------------------------------------------------------ build
    def refresh(self, force=False):
        if not force and self._doc_id == id(self.win.doc):
            return
        self._doc_id = id(self.win.doc)
        old = self.scroll.takeWidget()
        if old is not None:
            old.deleteLater()
        body = QWidget()
        body.setObjectName("props")
        self._v = QVBoxLayout(body)
        self._v.setContentsMargins(12, 8, 12, 12)
        self._v.setSpacing(6)
        page = self.win.doc.page
        d = design(page)

        # themes
        h = QLabel("THEME")
        h.setObjectName("group")
        self._v.addWidget(h)
        grid = QGridLayout()
        grid.setSpacing(6)
        for i, (key, th) in enumerate(THEMES.items()):
            b = QToolButton()
            b.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
            b.setIcon(_theme_icon(th))
            b.setIconSize(QSize(54, 38))
            b.setText(th["label"])
            b.setCheckable(True)
            b.setChecked(d.get("theme") == key)
            b.setStyleSheet(f"QToolButton {{ padding: 4px; border-radius: 6px; }} QToolButton:checked "
                            f"{{ background: {T.accent_dim}; border: 1px solid {T.accent}; }}")
            b.clicked.connect(lambda _=False, k=key: self._theme(k))
            grid.addWidget(b, i // 3, i % 3)
        self._v.addLayout(grid)
        self.restyle = QCheckBox("Also restyle labels to suit the theme")
        self.restyle.setChecked(True)
        self.maps = QCheckBox("Also switch light/dark background maps")
        self.maps.setChecked(True)
        self._v.addWidget(self.restyle)
        self._v.addWidget(self.maps)
        self._note("A theme sets every colour below at once; you can then adjust any of them. Dark maps suit "
                   "screens and presentations; light ones print best.")

        f = self._section("Paper and frame")
        bg = ColorButton(page.get("background", "#ffffff"))
        bg.colorChanged.connect(self._setp(["background"], "Paper colour"))
        f.addRow("Paper", bg)
        f.addRow("Map background", self._color(d, "map_bg", "Map background"))
        nl = QCheckBox("Frame line around the map")
        nl.setChecked(bool(page.get("neatline", True)))
        nl.toggled.connect(self._setp(["neatline"], "Frame line"))
        f.addRow(nl)
        f.addRow("Frame", self._combo({"single": "Single line", "double": "Double line"}, d["frame_style"],
                                      self._set("frame_style", "Frame style")))
        f.addRow("Line colour", self._color(d, "frame_color", "Frame colour"))
        f.addRow("Line width", self._dspin(0.05, 3, d["frame_width"], 0.05, 2, " mm", self._set("frame_width",
                                                                                                 "Frame width")))

        f = self._section("Title")
        f.addRow("Position", self._combo({"above": "Above the map", "inside": "On a panel inside the map"},
                                         d["title_position"], self._set("title_position", "Title position")))
        ts = page.get("title_style") or {}
        fc = QComboBox()
        for fams in FONTS.values():
            for fam in fams:
                fc.addItem(fam, fam)
        fc.setCurrentIndex(max(0, fc.findData(ts.get("font", "Georgia"))))
        fc.currentIndexChanged.connect(lambda: self._setp(["title_style", "font"], "Title font")(fc.currentData()))
        f.addRow("Font", fc)
        f.addRow("Size", self._dspin(8, 72, ts.get("size", 22), 1, 0, " pt",
                                     self._setp(["title_style", "size"], "Title size")))
        tcol = ColorButton(ts.get("color", "#1d1d1d"))
        tcol.colorChanged.connect(self._setp(["title_style", "color"], "Title colour"))
        f.addRow("Colour", tcol)
        f.addRow("Subtitle colour", self._color(d, "muted", "Subtitle colour"))

        f = self._section("Map elements")
        ef = QComboBox()
        for fams in FONTS.values():
            for fam in fams:
                ef.addItem(fam, fam)
        ef.setCurrentIndex(max(0, ef.findData(d["font"])))
        ef.currentIndexChanged.connect(lambda: self._set("font", "Element font")(ef.currentData()))
        f.addRow("Font", ef)
        f.addRow("Text colour", self._color(d, "text", "Element text colour"))
        f.addRow("Halo", self._color(d, "halo", "Halo colour"))
        self._note("Font and colours of the legend, scale bar and north arrow. The halo keeps their text readable "
                   "where it sits straight on the map.")

        f = self._section("Legend")
        f.addRow("Style", self._combo(LEGEND_STYLES, d["legend_style"], self._set("legend_style", "Legend style")))
        cols = QSpinBox()
        cols.setRange(1, 6)
        cols.setValue(int(d["legend_columns"]))
        cols.valueChanged.connect(self._set("legend_columns", "Legend columns"))
        f.addRow("Columns", cols)
        f.addRow("Background", self._color(d, "legend_bg", "Legend background"))
        op = QSpinBox()
        op.setRange(0, 100)
        op.setSuffix(" %")
        op.setValue(int(round(float(d["legend_opacity"]) * 100)))
        op.valueChanged.connect(lambda v: self._set("legend_opacity", "Legend opacity")(v / 100))
        f.addRow("Opacity", op)
        f.addRow("Border", self._color(d, "legend_border", "Legend border", allow_none=True))
        f.addRow("Text size", self._dspin(5, 20, d["legend_text_size"], 0.5, 1, " pt",
                                          self._set("legend_text_size", "Legend text size")))
        f.addRow("Symbol width", self._dspin(10, 60, d["legend_swatch"], 1, 0, " px",
                                             self._set("legend_swatch", "Legend symbol width")))

        f = self._section("Scale bar")
        sb = page.get("scalebar") or {}
        f.addRow("Style", self._combo({"bar": "Alternating bar", "hollow": "Half-filled bar", "line": "Simple line"},
                                      sb.get("style", "bar"), self._setp(["scalebar", "style"], "Scale bar style")))
        f.addRow("Units", self._combo({"metric": "Metres / kilometres", "imperial": "Feet / miles"},
                                      sb.get("units", "metric"), self._setp(["scalebar", "units"], "Scale units")))
        f.addRow("Dark colour", self._color(d, "scale_dark", "Scale bar colour"))
        f.addRow("Light colour", self._color(d, "scale_light", "Scale bar colour"))
        f.addRow("Text size", self._dspin(5, 16, d["scale_text_size"], 0.5, 1, " pt",
                                          self._set("scale_text_size", "Scale text size")))
        rt = QCheckBox("Also write the scale (1 : 50 000)")
        rt.setChecked(bool(d["scale_ratio"]))
        rt.toggled.connect(self._set("scale_ratio", "Scale ratio"))
        f.addRow(rt)

        f = self._section("North arrow")
        f.addRow("Style", self._combo(NORTH_STYLES, d["north_style"], self._set("north_style", "North arrow style")))
        f.addRow("Colour", self._color(d, "north_color", "North arrow colour"))

        f = self._section("Sources line")
        f.addRow("Colour", self._color(d, "credits_color", "Sources colour"))
        f.addRow("Size", self._dspin(4, 12, d["credits_size"], 0.5, 1, " pt", self._set("credits_size", "Sources size")))
        self._v.addStretch(1)
        self.scroll.setWidget(body)

    def _theme(self, key):
        self.win.edit("Theme: " + THEMES[key]["label"],
                      lambda: apply_theme(self.win.doc, key, self.restyle.isChecked(), self.maps.isChecked()))
        if not self.win.doc.page.get("show"):
            self.win.status("The theme styles the page – switch to Page view (Ctrl+L) to see all of it.", 6000)
        self.refresh(force=True)
