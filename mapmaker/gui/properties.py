"""The Properties panel: settings for whatever is selected – a label, symbol, line or area,
a layer, a page element (title, legend, scale bar, north arrow, sources) or the page.

Every change is applied at once and can be undone (Ctrl+Z); dragging a slider or typing
makes one undo step."""
from __future__ import annotations

import copy

from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtWidgets import (QCheckBox, QGridLayout, QComboBox, QDoubleSpinBox, QFormLayout, QFrame, QHBoxLayout, QLabel,
                               QLineEdit, QPlainTextEdit, QPushButton, QScrollArea, QSizePolicy, QSpinBox, QToolButton,
                               QVBoxLayout, QWidget)

from .. import symbols as SYM
from ..document import PAPERS, PAGE_ELEMENTS
from ..styles import DASHES, FONTS, TEXT_PRESETS, text_style
from ..terrain import TERRAIN_STYLES
from ..tiles import BASEMAPS
from .controls import ColorButton, SliderSpin, SymbolPicker
from .theme import T


def _spin(lo, hi, val, step=1.0, dec=1, suffix=""):
    s = QDoubleSpinBox()
    s.setRange(lo, hi)
    s.setDecimals(dec)
    s.setSingleStep(step)
    s.setValue(float(val))
    s.setSuffix(suffix)
    s.setKeyboardTracking(False)
    return s


def _combo(items, current):
    c = QComboBox()
    for key, label in items:
        c.addItem(label, key)
    i = c.findData(current)
    c.setCurrentIndex(max(0, i))
    return c


def _font_combo(current):
    c = QComboBox()
    for group, fams in FONTS.items():
        for f in fams:
            c.addItem(f"{f}", f)
    if c.findData(current) < 0:
        c.addItem(current, current)
    c.setCurrentIndex(c.findData(current))
    c.setToolTip("A sans-serif face for most labels and one serif face (e.g. for water and titles) is plenty")
    return c


class TextStyleEditor(QWidget):
    """Edits a text style dict. changed(new style, merge key)."""
    changed = Signal(dict, str)

    def __init__(self, st: dict, with_preset=True, with_angle=False, with_box=False, with_align=False, parent=None):
        super().__init__(parent)
        self.st = copy.deepcopy(st)
        f = QFormLayout(self)
        f.setContentsMargins(0, 0, 0, 0)
        f.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        if with_preset:
            self.preset = QComboBox()
            self.preset.addItem("Custom", "")
            for k, (label, _s) in TEXT_PRESETS.items():
                self.preset.addItem(label, k)
            self.preset.setCurrentIndex(0)
            self.preset.setToolTip("Kinds of labels with matching type styles: water in blue italics, regions in "
                                   "spaced capitals …")
            self.preset.activated.connect(self._preset)
            f.addRow("Style", self.preset)
        self.font_combo = _font_combo(self.st.get("font", "Segoe UI"))
        self.font_combo.currentIndexChanged.connect(lambda: self._set("font", self.font_combo.currentData()))
        f.addRow("Font", self.font_combo)
        row = QHBoxLayout()
        self.size_spin = _spin(4, 96, self.st.get("size", 10), 0.5, 1, " pt")
        self.size_spin.valueChanged.connect(lambda v: self._set("size", v, "size"))
        self.bold = QToolButton()
        self.bold.setText("B")
        self.bold.setCheckable(True)
        self.bold.setChecked(bool(self.st.get("bold")))
        self.bold.setStyleSheet("font-weight:700;")
        self.bold.toggled.connect(lambda v: self._set("bold", v))
        self.italic = QToolButton()
        self.italic.setText("I")
        self.italic.setCheckable(True)
        self.italic.setChecked(bool(self.st.get("italic")))
        self.italic.setStyleSheet("font-style:italic;")
        self.italic.toggled.connect(lambda v: self._set("italic", v))
        self.caps = QToolButton()
        self.caps.setText("AA")
        self.caps.setToolTip("Capitals")
        self.caps.setCheckable(True)
        self.caps.setChecked(bool(self.st.get("caps")))
        self.caps.toggled.connect(lambda v: self._set("caps", v))
        row.addWidget(self.size_spin, 1)
        row.addWidget(self.bold)
        row.addWidget(self.italic)
        row.addWidget(self.caps)
        f.addRow("Size", row)
        self.color = ColorButton(self.st.get("color", "#262626"))
        self.color.colorChanged.connect(lambda c: self._set("color", c))
        f.addRow("Colour", self.color)
        hrow = QHBoxLayout()
        self.halo = ColorButton(self.st.get("halo", ""), allow_none=True, none_label="No halo")
        self.halo.setToolTip("A thin outline around the letters keeps labels readable on busy backgrounds")
        self.halo.colorChanged.connect(lambda c: self._set("halo", c))
        self.halo_w = _spin(0.2, 6, self.st.get("halo_width", 1.4), 0.1, 1, " pt")
        self.halo_w.valueChanged.connect(lambda v: self._set("halo_width", v, "halo_width"))
        hrow.addWidget(self.halo)
        hrow.addWidget(self.halo_w, 1)
        f.addRow("Halo", hrow)
        self.spacing = QSpinBox()
        self.spacing.setRange(-20, 200)
        self.spacing.setSuffix(" %")
        self.spacing.setValue(int(self.st.get("spacing", 0)))
        self.spacing.setKeyboardTracking(False)
        self.spacing.setToolTip("Spread the letters of area names across the area they name")
        self.spacing.valueChanged.connect(lambda v: self._set("spacing", v, "spacing"))
        f.addRow("Letter spacing", self.spacing)
        if with_angle:
            self.angle = _spin(-180, 180, self.st.get("angle", 0), 1, 0, "°")
            self.angle.setToolTip("Turn the label, e.g. to follow a river or a valley")
            self.angle.valueChanged.connect(lambda v: self._set("angle", v, "angle"))
            f.addRow("Angle", self.angle)
        if with_align:
            self.align = _combo([("left", "Left"), ("center", "Centre"), ("right", "Right")], self.st.get("align", "center"))
            self.align.currentIndexChanged.connect(lambda: self._set("align", self.align.currentData()))
            f.addRow("Lines", self.align)
        if with_box:
            self.box = ColorButton(self.st.get("box", ""), allow_none=True, none_label="No box")
            self.box.setToolTip("A box behind the text – for notes and callouts")
            self.box.colorChanged.connect(lambda c: self._set("box", c))
            f.addRow("Box", self.box)

    def _set(self, key, value, merge=""):
        self.st[key] = value
        self.changed.emit(copy.deepcopy(self.st), merge or key)

    def _preset(self, _i):
        k = self.preset.currentData()
        if not k:
            return
        st = text_style(k)
        st["angle"] = self.st.get("angle", 0.0)
        self.st = st
        self.changed.emit(copy.deepcopy(st), "preset:" + k)


class PropertiesPanel(QWidget):
    def __init__(self, win, parent=None):
        super().__init__(parent)
        self.win = win
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        head = QFrame()
        head.setObjectName("panelHead")
        hl = QHBoxLayout(head)
        hl.setContentsMargins(10, 7, 10, 7)
        self.title = QLabel("Properties")
        hl.addWidget(self.title)
        hl.addStretch(1)
        self.kind = QLabel()
        self.kind.setStyleSheet(f"color:{T.muted}; font-weight:400;")
        hl.addWidget(self.kind)
        lay.addWidget(head)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        lay.addWidget(self.scroll, 1)
        self._key = None
        self._body = None

    # ------------------------------------------------------------ helpers
    def edit(self, label, fn, merge=None):
        self.win.edit(label, fn, merge=merge, from_props=True)

    def _new_body(self):
        body = QWidget()
        body.setObjectName("props")
        v = QVBoxLayout(body)
        v.setContentsMargins(12, 8, 12, 12)
        v.setSpacing(6)
        self._v = v
        return body

    def _section(self, title: str) -> QFormLayout:
        h = QLabel(title.upper())
        h.setObjectName("group")
        self._v.addWidget(h)
        f = QFormLayout()
        f.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        f.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        f.setHorizontalSpacing(8)
        f.setVerticalSpacing(6)
        self._v.addLayout(f)
        return f

    def _note(self, text: str):
        lab = QLabel(text)
        lab.setWordWrap(True)
        lab.setObjectName("hint")
        lab.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self._v.addWidget(lab)
        return lab

    def _line_edit(self, text, on_change, placeholder="", merge_key=None):
        e = QLineEdit(text)
        e.setPlaceholderText(placeholder)
        e.textEdited.connect(lambda t: on_change(t))
        return e

    # ------------------------------------------------------------ refresh
    def refresh(self, force: bool = False):
        sel = self.win.canvas.sel
        key = (sel.get("layer"), sel.get("item"), sel.get("element"), id(self.win.doc))
        if not force and key == self._key:
            return
        self._key = key
        old = self.scroll.takeWidget()
        if old is not None:
            old.deleteLater()
        body = self._new_body()
        doc = self.win.doc
        L, it = doc.find_item(sel.get("item")) if sel.get("item") else (doc.layer(sel.get("layer")), None)
        try:
            if it is not None:
                self._item(L, it)
            elif sel.get("element"):
                self._element(sel["element"])
            elif L is not None:
                self._layer(L)
            else:
                self._page()
        except Exception:
            import logging
            logging.getLogger("mapmaker").exception("Properties panel failed")
        self._v.addStretch(1)
        self.scroll.setWidget(body)

    # ------------------------------------------------------------ items
    def _item(self, L, it):
        t = it["type"]
        self.title.setText({"text": "Label", "symbol": "Symbol", "line": "Line", "area": "Area"}.get(t, "Item"))
        self.kind.setText(f"in {L['name']}")
        iid = it["id"]

        def item():
            return self.win.doc.find_item(iid)[1]

        def setter(key, label, merge=None, sub=None):
            def fn(v):
                def apply():
                    x = item()
                    if x is None:
                        return
                    (x[sub] if sub else x)[key] = v
                self.edit(label, apply, merge=(merge, iid) if merge else None)
            return fn

        if t == "text":
            f = self._section("Text")
            te = QPlainTextEdit(it.get("text", ""))
            te.setFixedHeight(64)
            timer = QTimer(te)
            timer.setSingleShot(True)
            timer.setInterval(350)
            timer.timeout.connect(lambda: setter("text", "Edit text", "text")(te.toPlainText()))
            te.textChanged.connect(timer.start)
            f.addRow(te)

            def style_changed(st, mk):
                def apply():
                    x = item()
                    if x is not None:
                        x["style"] = st
                        if mk.startswith("preset:"):
                            x["preset"] = mk.split(":", 1)[1]
                self.edit("Text style", apply, merge=("style:" + mk, iid))
                if mk.startswith("preset:"):
                    QTimer.singleShot(0, lambda: self.refresh(force=True))
            ed = TextStyleEditor(it.get("style") or {}, with_angle=True, with_box=True, with_align=True)
            ed.changed.connect(style_changed)
            self._section("Type")
            self._v.addWidget(ed)
        elif t == "symbol":
            f = self._section("Symbol")
            sp = SymbolPicker(it.get("symbol", "pin"))
            sp.symbolChanged.connect(setter("symbol", "Symbol"))
            f.addRow("Symbol", sp)
            cb = ColorButton(it.get("color", "#b03a2e"))
            cb.colorChanged.connect(setter("color", "Symbol colour"))
            f.addRow("Colour", cb)
            sz = _spin(6, 120, it.get("size", 22), 1, 0, " px")
            sz.valueChanged.connect(setter("size", "Symbol size", "size"))
            f.addRow("Size", sz)
            badge = QCheckBox("Round badge (white symbol on a coloured disc)")
            badge.setChecked(bool(it.get("badge")))
            badge.toggled.connect(setter("badge", "Badge"))
            f.addRow(badge)
            f = self._section("Name next to the symbol")
            name = QLineEdit(it.get("label", ""))
            name.setPlaceholderText("e.g. Uppsala domkyrka")
            name.textEdited.connect(setter("label", "Symbol name", "label"))
            f.addRow(name)
            self._note("Names sit above and to the right of their symbol – the clearest position.")

            def lstyle(st, mk):
                self.edit("Name style", lambda: (item() or {}).__setitem__("label_style", st), merge=("ls:" + mk, iid))
            ed = TextStyleEditor(it.get("label_style") or text_style("place"))
            ed.changed.connect(lstyle)
            self._v.addWidget(ed)
        elif t == "line":
            st = it.get("style") or {}
            f = self._section("Line")
            cb = ColorButton(st.get("color", "#b03a2e"))
            cb.colorChanged.connect(setter("color", "Line colour", sub="style"))
            f.addRow("Colour", cb)
            w = _spin(0.2, 30, st.get("width", 2.5), 0.25, 2, " px")
            w.valueChanged.connect(setter("width", "Line width", "width", sub="style"))
            f.addRow("Width", w)
            dash = _combo(list(DASHES.items()), st.get("dash", "solid"))
            dash.currentIndexChanged.connect(lambda: setter("dash", "Line pattern", sub="style")(dash.currentData()))
            f.addRow("Pattern", dash)
            op = SliderSpin(0, 100, int(float(st.get("opacity", 1.0)) * 100))
            op.valueChanged.connect(lambda v: setter("opacity", "Line opacity", "opacity", sub="style")(v / 100))
            f.addRow("Opacity", op)
            arrow = QCheckBox("Arrow at the end")
            arrow.setChecked(bool(st.get("arrow")))
            arrow.toggled.connect(setter("arrow", "Arrow", sub="style"))
            f.addRow(arrow)
            self._note(f"{len(it.get('coords', []))} points. Drag the white squares on the map to reshape it.")
        elif t == "area":
            st = it.get("style") or {}
            f = self._section("Fill")
            cb = ColorButton(st.get("fill", "#b03a2e"))
            cb.colorChanged.connect(setter("fill", "Fill colour", sub="style"))
            f.addRow("Colour", cb)
            op = SliderSpin(0, 100, int(float(st.get("fill_opacity", 0.22)) * 100))
            op.valueChanged.connect(lambda v: setter("fill_opacity", "Fill opacity", "fo", sub="style")(v / 100))
            f.addRow("Opacity", op)
            f = self._section("Outline")
            cb2 = ColorButton(st.get("stroke", "#b03a2e"))
            cb2.colorChanged.connect(setter("stroke", "Outline colour", sub="style"))
            f.addRow("Colour", cb2)
            w = _spin(0, 30, st.get("width", 1.8), 0.25, 2, " px")
            w.valueChanged.connect(setter("width", "Outline width", "width", sub="style"))
            f.addRow("Width", w)
            dash = _combo(list(DASHES.items()), st.get("dash", "solid"))
            dash.currentIndexChanged.connect(lambda: setter("dash", "Outline pattern", sub="style")(dash.currentData()))
            f.addRow("Pattern", dash)
            self._note(f"{len(it.get('coords', []))} corners. Drag the white squares on the map to reshape it.")
        # legend
        f = self._section("Legend")
        lg = QCheckBox("Show in the legend")
        lg.setChecked(bool(it.get("legend")))
        lg.toggled.connect(setter("legend", "Legend"))
        f.addRow(lg)
        default = SYM.label(it.get("symbol", "circle")) if t == "symbol" else L["name"]
        lt = QLineEdit(it.get("legend_text", ""))
        lt.setPlaceholderText(default)
        lt.textEdited.connect(setter("legend_text", "Legend text", "legend_text"))
        f.addRow("Text", lt)
        hide = QCheckBox("Hidden")
        hide.setChecked(bool(it.get("hidden")))
        hide.toggled.connect(setter("hidden", "Hide"))
        f = self._section("Item")
        f.addRow(hide)
        row = QHBoxLayout()
        b = QPushButton("Zoom to")
        b.clicked.connect(self.win.zoom_to_selection)
        d = QPushButton("Delete")
        d.clicked.connect(self.win.delete_selected_layer_or_item)
        row.addWidget(b)
        row.addWidget(d)
        row.addStretch(1)
        self._v.addLayout(row)

    # ------------------------------------------------------------ layers
    def _layer(self, L):
        from .layers_panel import KIND_NAME
        self.title.setText(L["name"])
        self.kind.setText(KIND_NAME.get(L["kind"], ""))
        lid = L["id"]
        win = self.win

        def lay():
            return win.doc.layer(lid)

        def setter(key, label, merge=None, sub=None, cast=None):
            def fn(v):
                if cast:
                    v = cast(v)

                def apply():
                    x = lay()
                    if x is not None:
                        (x.setdefault(sub, {}) if sub else x)[key] = v
                self.edit(label, apply, merge=(merge, lid) if merge else None)
            return fn

        f = self._section("Layer")
        name = QLineEdit(L["name"])
        name.textEdited.connect(setter("name", "Rename layer", "name"))
        f.addRow("Name", name)
        k = L["kind"]
        if k == "color":
            cb = ColorButton(L.get("color", "#ffffff"))
            cb.colorChanged.connect(setter("color", "Background colour"))
            f.addRow("Colour", cb)
            self._note("A plain colour under everything else – useful when your map is only your own data, "
                       "or as paper behind semi-transparent layers.")
        elif k == "basemap":
            src = _combo([(b.id, b.name) for b in BASEMAPS], L.get("source"))
            src.currentIndexChanged.connect(lambda: self._basemap_changed(lid, src.currentData()))
            f.addRow("Map", src)
            b = next((b for b in BASEMAPS if b.id == L.get("source")), None)
            if b is not None and b.labels:
                lab = QCheckBox("Place names on top")
                lab.setChecked(bool(L.get("labels")))
                lab.toggled.connect(setter("labels", "Place names"))
                f.addRow(lab)
            if b is not None and b.note:
                self._note(b.note + ".")
            self._note("Tip: a quiet, light background lets your own symbols and labels stand out (figure and "
                       "ground). Lower the opacity to fade it further.")
        elif k == "terrain":
            stl = _combo(list((s, v) for s, v in TERRAIN_STYLES.items()), L.get("style", "hillshade"))
            stl.currentIndexChanged.connect(lambda: self._terrain_changed(lid, stl.currentData()))
            f.addRow("Style", stl)
            if L.get("style") != "tint":
                s = SliderSpin(10, 100, int(float(L.get("strength", 0.8)) * 100))
                s.valueChanged.connect(lambda v: setter("strength", "Terrain strength", "strength")(round(v / 100, 2)))
                f.addRow("Strength", s)
                op = QCheckBox("Opaque grey picture (instead of shading)")
                op.setChecked(bool(L.get("opaque")))
                op.toggled.connect(setter("opaque", "Opaque terrain"))
                f.addRow(op)
                self._note("1 m laser elevation from Lantmäteriet in Sweden (Mapterhorn elsewhere). Shading uses the "
                           "Multiply blend mode, so the layers below show through.")
            else:
                self._note("Colours by height, from the global terrain model (about 30 m). Put a laser terrain layer "
                           "with Multiply above it for shaded relief.")
        elif k == "service":
            s = L.get("source") or {}
            url = QLineEdit(", ".join(s.get("urls", [])))
            url.setReadOnly(True)
            f.addRow("Address", url)
            if s.get("wms_layers"):
                wl = QLineEdit(s.get("wms_layers"))
                wl.editingFinished.connect(lambda: setter("wms_layers", "WMS layers", sub="source")(wl.text().strip()))
                f.addRow("WMS layers", wl)
            at = QLineEdit(s.get("attribution", ""))
            at.textEdited.connect(setter("attribution", "Attribution", "attr", sub="source"))
            f.addRow("Credit", at)
            err = win.tiles.errors.get(s.get("id", ""))
            if err:
                self._note("Last error from the service: " + err[:200])
        elif k == "data":
            self._data_layer(L, f, setter)
        elif k == "grid":
            sysc = _combo([("sweref", "SWEREF 99 TM (metres)"), ("wgs84", "Latitude / longitude")],
                          L.get("system", "sweref"))
            sysc.currentIndexChanged.connect(lambda: setter("system", "Grid system")(sysc.currentData()))
            f.addRow("Coordinates", sysc)
            stl = _combo([("lines", "Lines"), ("crosses", "Crosses at the intersections"),
                          ("ticks", "Ticks along the frame")], L.get("style", "lines"))
            stl.currentIndexChanged.connect(lambda: setter("style", "Grid style")(stl.currentData()))
            f.addRow("Style", stl)
            if L.get("system", "sweref") == "sweref":
                iv = _combo([(0, "Automatic"), (100, "100 m"), (250, "250 m"), (500, "500 m"), (1000, "1 km"),
                             (2000, "2 km"), (5000, "5 km"), (10000, "10 km"), (20000, "20 km"), (50000, "50 km"),
                             (100000, "100 km")], L.get("interval", 0))
            else:
                iv = _combo([(0, "Automatic"), (1 / 60, "1′"), (5 / 60, "5′"), (10 / 60, "10′"), (15 / 60, "15′"),
                             (30 / 60, "30′"), (1, "1°"), (2, "2°"), (5, "5°")], L.get("interval", 0))
            iv.currentIndexChanged.connect(lambda: setter("interval", "Grid interval")(iv.currentData()))
            f.addRow("Interval", iv)
            col = ColorButton(L.get("color", "#3c3c3c"))
            col.colorChanged.connect(setter("color", "Grid colour"))
            f.addRow("Colour", col)
            w = _spin(0.1, 5, L.get("width", 0.6), 0.1, 1, " px")
            w.valueChanged.connect(setter("width", "Grid line width", "gw"))
            f.addRow("Line width", w)
            dash = _combo(list(DASHES.items()), L.get("dash", "solid"))
            dash.currentIndexChanged.connect(lambda: setter("dash", "Grid pattern")(dash.currentData()))
            f.addRow("Pattern", dash)
            lab = QCheckBox("Write the coordinates")
            lab.setChecked(bool(L.get("labels", True)))
            lab.toggled.connect(setter("labels", "Grid labels"))
            f.addRow(lab)
            ls = _spin(4, 16, L.get("label_size", 7.0), 0.5, 1, " pt")
            ls.valueChanged.connect(setter("label_size", "Grid label size", "gls"))
            f.addRow("Label size", ls)
            self._note("A grid lets readers give and find positions. Keep it light – it supports the map rather "
                       "than competing with it.")
        elif k == "annotations":
            n = len(L.get("items", []))
            self._note(f"{n} item{'s' if n != 1 else ''}. New labels and symbols go into the selected annotation "
                       "layer – use separate layers to switch groups of them on and off together.")
        f = self._section("Display")
        vis = QCheckBox("Visible")
        vis.setChecked(L.get("visible", True))
        vis.toggled.connect(setter("visible", "Show / hide"))
        lock = QCheckBox("Locked (cannot be changed on the map)")
        lock.setChecked(bool(L.get("locked")))
        lock.toggled.connect(setter("locked", "Lock"))
        f.addRow(vis)
        f.addRow(lock)

    def _basemap_changed(self, lid, source):
        from ..tiles import BASEMAPS_BY_ID

        def apply():
            L = self.win.doc.layer(lid)
            if L is None:
                return
            old = BASEMAPS_BY_ID.get(L.get("source"))
            if old is not None and L["name"] == old.name:
                L["name"] = BASEMAPS_BY_ID[source].name
            L["source"] = source
        self.edit("Background map", apply)
        QTimer.singleShot(0, lambda: self.refresh(force=True))

    def _terrain_changed(self, lid, style):
        def apply():
            L = self.win.doc.layer(lid)
            if L is None:
                return
            from ..document import terrain_layer
            fresh = terrain_layer(style)
            if L["name"].startswith(("Laser terrain", "Terrain")):
                L["name"] = fresh["name"]
            L["style"] = style
            L["blend"] = fresh["blend"]
        self.edit("Terrain style", apply)
        QTimer.singleShot(0, lambda: self.refresh(force=True))

    def _data_layer(self, L, f, setter):
        t = self.win.doc.table(L)
        kinds = t.kinds_present() if t is not None else set()
        has_pt = "point" in kinds
        has_ln = "line" in kinds
        has_pg = "polygon" in kinds
        n = t.n if t is not None else 0
        st = L.get("style") or {}
        src = L.get("source") or {}
        self._note(f"{n:,} features".replace(",", " ") + (f" from {src.get('name') or src.get('path', '')}"
                                                                      if src else ""))
        if t is not None:
            self._show_as(L, t, setter, has_pt)
            self._thematic(L, t)
        if has_pt:
            g = self._section("Points")
            mk = QComboBox()
            mk.addItem("Dot", "circle")
            from .icons import symbol_icon
            for key in SYM.SYMBOLS:
                if key != "circle":
                    mk.addItem(symbol_icon(key, "#e0e0e0", 18), SYM.label(key), key)
            mk.setCurrentIndex(max(0, mk.findData(st.get("marker", "circle"))))
            mk.currentIndexChanged.connect(lambda: setter("marker", "Point symbol", sub="style")(mk.currentData()))
            g.addRow("Symbol", mk)
            c = ColorButton(st.get("color", "#d95f02"))
            c.colorChanged.connect(setter("color", "Point colour", sub="style"))
            g.addRow("Colour", c)
            sz = _spin(1, 60, st.get("size", 7), 0.5, 1, " px")
            sz.valueChanged.connect(setter("size", "Point size", "size", sub="style"))
            g.addRow("Size", sz)
            o = ColorButton(st.get("outline", "#ffffff"), allow_none=True, none_label="No outline")
            o.colorChanged.connect(setter("outline", "Outline colour", sub="style"))
            ow = _spin(0, 6, st.get("outline_width", 1.0), 0.25, 2, " px")
            ow.valueChanged.connect(setter("outline_width", "Outline width", "ow", sub="style"))
            row = QHBoxLayout()
            row.addWidget(o)
            row.addWidget(ow, 1)
            g.addRow("Outline", row)
        if has_ln:
            g = self._section("Lines")
            c = ColorButton(st.get("line_color", "#555555"))
            c.colorChanged.connect(setter("line_color", "Line colour", sub="style"))
            g.addRow("Colour", c)
            w = _spin(0.1, 20, st.get("line_width", 1.6), 0.25, 2, " px")
            w.valueChanged.connect(setter("line_width", "Line width", "lw", sub="style"))
            g.addRow("Width", w)
            d = _combo(list(DASHES.items()), st.get("dash", "solid"))
            d.currentIndexChanged.connect(lambda: setter("dash", "Line pattern", sub="style")(d.currentData()))
            g.addRow("Pattern", d)
        if has_pg:
            g = self._section("Areas")
            c = ColorButton(st.get("fill", "#888888"))
            c.colorChanged.connect(setter("fill", "Fill colour", sub="style"))
            g.addRow("Fill", c)
            op = SliderSpin(0, 100, int(float(st.get("fill_opacity", 0.25)) * 100))
            op.valueChanged.connect(lambda v: setter("fill_opacity", "Fill opacity", "fo", sub="style")(v / 100))
            g.addRow("Fill opacity", op)
            c2 = ColorButton(st.get("stroke", "#555555"))
            c2.colorChanged.connect(setter("stroke", "Outline colour", sub="style"))
            g.addRow("Outline", c2)
            w = _spin(0, 20, st.get("stroke_width", 1.2), 0.25, 2, " px")
            w.valueChanged.connect(setter("stroke_width", "Outline width", "sw", sub="style"))
            g.addRow("Outline width", w)
            d = _combo(list(DASHES.items()), st.get("stroke_dash", "solid"))
            d.currentIndexChanged.connect(lambda: setter("stroke_dash", "Outline pattern", sub="style")(d.currentData()))
            g.addRow("Pattern", d)
        g = self._section("Labels")
        fields = [fl[0] if isinstance(fl, (list, tuple)) else str(fl) for fl in (L.get("fields") or [])]
        if t is not None:
            fields = list(t.fields)
        lab = L.get("label") or {}
        fc = QComboBox()
        fc.addItem("No labels", None)
        for name in fields:
            fc.addItem(name, name)
        fc.setCurrentIndex(max(0, fc.findData(lab.get("field"))))
        fc.currentIndexChanged.connect(lambda: setter("field", "Label field", sub="label")(fc.currentData()))
        g.addRow("Label with", fc)
        if lab.get("field"):
            ed = TextStyleEditor(lab.get("style") or text_style("feature"))
            ed.changed.connect(lambda st_, mk: self.edit("Label style", lambda: self.win.doc.layer(L["id"])
                                                         .setdefault("label", {}).__setitem__("style", st_),
                                                         merge=("lst:" + mk, L["id"])))
            self._v.addWidget(ed)
            self._note("Labels that would overlap are left out, so zooming in shows more of them. With very many "
                       "features in view, labels wait until you zoom in.")
        g = self._section("Legend")
        lg = QCheckBox("Show in the legend")
        lg.setChecked(bool(L.get("legend", True)))
        lg.toggled.connect(setter("legend", "Legend"))
        g.addRow(lg)

    # ------------------------------------------------------------ heat maps, clusters, thematic styles
    def _show_as(self, L, t, setter, has_pt):
        st = L.get("style") or {}
        g = self._section("Show as")
        mode = _combo([("points", "Features"), ("heatmap", "Heat map (density)"), ("clusters", "Clusters (counts)")],
                      st.get("render", "points"))
        mode.currentIndexChanged.connect(lambda: (setter("render", "Show as", sub="style")(mode.currentData()),
                                                  QTimer.singleShot(0, lambda: self.refresh(force=True))))
        g.addRow("Draw", mode)
        r = st.get("render", "points")
        if r == "heatmap":
            rad = _spin(3, 150, st.get("heat_radius", 20), 1, 0, " px")
            rad.setToolTip("How far each feature spreads – larger gives a smoother surface")
            rad.valueChanged.connect(setter("heat_radius", "Heat radius", "hr", sub="style"))
            g.addRow("Radius", rad)
            it = _spin(0.2, 5, st.get("heat_intensity", 1.0), 0.1, 1, "×")
            it.setToolTip("Higher makes thin areas show up more")
            it.valueChanged.connect(setter("heat_intensity", "Heat intensity", "hi", sub="style"))
            g.addRow("Intensity", it)
            from ..styles import HEAT_RAMPS
            ramp = _combo([(k, k.capitalize()) for k in HEAT_RAMPS], st.get("heat_ramp", "heat"))
            ramp.currentIndexChanged.connect(lambda: setter("heat_ramp", "Heat colours", sub="style")(ramp.currentData()))
            g.addRow("Colours", ramp)
            wf = QComboBox()
            wf.addItem("Each feature counts once", None)
            for f in t.fields:
                if t.cols[f].kind == "num":
                    wf.addItem(f"Weighted by {f}", f)
            wf.setCurrentIndex(max(0, wf.findData(st.get("heat_weight"))))
            wf.currentIndexChanged.connect(lambda: setter("heat_weight", "Heat weight", sub="style")(wf.currentData()))
            g.addRow("Weight", wf)
            self._note("The colours are scaled to what is in view. Lower the layer opacity to see the map below.")
        elif r == "clusters":
            cs = _spin(20, 200, st.get("cluster_px", 56), 2, 0, " px")
            cs.setToolTip("Features closer than this on the map are gathered into one circle")
            cs.valueChanged.connect(setter("cluster_px", "Cluster size", "cp", sub="style"))
            g.addRow("Gather within", cs)
            self._note("Circles show how many features they hold; zoom in to split them up. They use the point "
                       "colour below.")

    def _thematic(self, L, t):
        from ..styles import QUALITATIVE, SEQUENTIAL
        from ..table import make_categories, make_graduated
        st = L.get("style") or {}
        cls = st.get("classify") or {"mode": "single"}
        lid = L["id"]
        win = self.win
        g = self._section("Colour by")
        mode = _combo([("single", "One colour"), ("categories", "Categories (a colour per value)"),
                       ("graduated", "Graduated (classes of a number)")], cls.get("mode", "single"))
        g.addRow("Style", mode)
        field = QComboBox()
        for f in t.fields:
            field.addItem(f + ("   (number)" if t.cols[f].kind == "num" else ""), f)
        field.setCurrentIndex(max(0, field.findData(cls.get("field"))))
        scheme = QComboBox()
        nclass = QSpinBox()
        nclass.setRange(2, 9)
        nclass.setValue(len(cls.get("classes") or []) or 5)
        method = _combo([("quantile", "Equal counts (quantiles)"), ("equal", "Equal steps")], cls.get("method", "quantile"))

        def fill_schemes():
            scheme.blockSignals(True)
            scheme.clear()
            names = QUALITATIVE if mode.currentData() == "categories" else SEQUENTIAL
            for k in names:
                scheme.addItem(k, k)
            scheme.setCurrentIndex(max(0, scheme.findData(cls.get("scheme"))))
            scheme.blockSignals(False)
        fill_schemes()

        def build():
            m = mode.currentData()
            f = field.currentData()
            if m == "single" or not f:
                new = None
            else:
                try:
                    new = (make_categories(t, f, scheme.currentData() or "Set2") if m == "categories" else
                           make_graduated(t, f, nclass.value(), method.currentData(), scheme.currentData() or "YlOrRd"))
                except Exception as e:
                    win.status(str(e), 6000)
                    return

            def apply():
                Lx = win.doc.layer(lid)
                if Lx is None:
                    return
                if new is None:
                    Lx["style"].pop("classify", None)
                else:
                    Lx["style"]["classify"] = new
            self.edit("Colour by " + (f or "") if new else "One colour", apply)
            QTimer.singleShot(0, lambda: self.refresh(force=True))

        def mode_changed():
            fill_schemes()
            if mode.currentData() == "graduated":
                nums = [i for i in range(field.count()) if t.cols[field.itemData(i)].kind == "num"]
                if nums and t.cols[field.currentData()].kind != "num":
                    field.setCurrentIndex(nums[0])
            build()
        mode.currentIndexChanged.connect(mode_changed)
        if cls.get("mode", "single") == "single":
            g.addRow("Field", field)
            field.currentIndexChanged.connect(lambda: None)
            self._note("Pick a style and a field to give each kind of feature its own colour – the legend lists "
                       "them for you. Qualitative colours tell kinds apart; light-to-dark ones show more and less.")
            return
        g.addRow("Field", field)
        g.addRow("Colours", scheme)
        if cls["mode"] == "graduated":
            g.addRow("Classes", nclass)
            g.addRow("Breaks", method)
            nclass.valueChanged.connect(build)
            method.currentIndexChanged.connect(build)
        field.currentIndexChanged.connect(build)
        scheme.currentIndexChanged.connect(build)
        # the classes: colour, shown or not, legend text
        grid = QGridLayout()
        grid.setHorizontalSpacing(6)
        grid.setVerticalSpacing(3)
        rows = list(enumerate(cls.get("classes") or []))
        other = cls.get("other") or {}
        if other.get("count") or cls["mode"] == "categories":
            rows.append(("other", other))

        def set_class(i, key, label):
            def fn(v):
                def apply():
                    c = win.doc.layer(lid)["style"]["classify"]
                    (c["other"] if i == "other" else c["classes"][i])[key] = v
                self.edit(label, apply, merge=("cls", lid, i, key))
            return fn
        for r, (i, c) in enumerate(rows[:40]):
            cb = ColorButton(c.get("color", "#b4b4b4"))
            cb.setIconSize(QSize(22, 14))
            cb.colorChanged.connect(set_class(i, "color", "Class colour"))
            show = QCheckBox()
            show.setChecked(bool(c.get("show", True)))
            show.setToolTip("Draw these features")
            show.toggled.connect(set_class(i, "show", "Show class"))
            lab = QLineEdit(c.get("label", ""))
            lab.textEdited.connect(set_class(i, "label", "Class name"))
            cnt = QLabel(f"{int(c.get('count', 0)):,}".replace(",", " "))
            cnt.setObjectName("hint")
            cnt.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            grid.addWidget(show, r, 0)
            grid.addWidget(cb, r, 1)
            grid.addWidget(lab, r, 2)
            grid.addWidget(cnt, r, 3)
        grid.setColumnStretch(2, 1)
        self._v.addLayout(grid)
        if len(rows) > 40:
            self._note(f"{len(rows) - 40} more classes are not listed.")
        self._note("Untick a class to leave it off the map and the legend. Change a name to change its legend text.")

    # ------------------------------------------------------------ page and elements
    def _page(self):
        self.title.setText("Map and page")
        self.kind.setText("")
        self._page_controls(None)

    def _image(self, name):
        win = self.win
        iid = name.split(":", 1)[1]
        im = next((i for i in win.doc.page.get("images") or [] if i["id"] == iid), None)
        if im is None:
            return
        self.title.setText("Picture")
        self.kind.setText("on the page")

        def set_(key, label):
            def fn(v):
                def apply():
                    x = next((i for i in win.doc.page.get("images") or [] if i["id"] == iid), None)
                    if x is not None:
                        x[key] = v
                self.edit(label, apply, merge=("img", iid, key))
            return fn
        f = self._section("Picture")
        if im.get("name"):
            f.addRow("File", QLabel(im["name"]))
        w = _spin(3, 400, im.get("w", 30), 1, 1, " mm")
        w.valueChanged.connect(set_("w", "Picture width"))
        f.addRow("Width", w)
        x = _spin(-50, 600, im.get("x", 10), 1, 1, " mm")
        x.valueChanged.connect(set_("x", "Move picture"))
        f.addRow("From left", x)
        y = _spin(-50, 600, im.get("y", 10), 1, 1, " mm")
        y.valueChanged.connect(set_("y", "Move picture"))
        f.addRow("From top", y)
        op = SliderSpin(0, 100, int(float(im.get("opacity", 1.0)) * 100))
        op.valueChanged.connect(lambda v: set_("opacity", "Picture opacity")(v / 100))
        f.addRow("Opacity", op)
        row = QHBoxLayout()
        rep_ = QPushButton("Replace…")
        rep_.clicked.connect(lambda: win.insert_image(replace=iid))
        d = QPushButton("Delete")
        d.clicked.connect(win.delete_selected_layer_or_item)
        row.addWidget(rep_)
        row.addWidget(d)
        row.addStretch(1)
        self._v.addLayout(row)
        self._note("Drag the picture on the page to move it; drag its bottom-right corner to resize it. "
                   "SVG logos stay sharp at any size.")

    def _element(self, name):
        if name.startswith("image:"):
            self._image(name)
            return
        self.title.setText(PAGE_ELEMENTS.get(name, name))
        self.kind.setText("on the page")
        self._page_controls(name)

    def _page_controls(self, focus):
        win = self.win
        page = win.doc.page

        def setp(path, label, merge=None):
            def fn(v):
                def apply():
                    d = win.doc.page
                    for k in path[:-1]:
                        d = d.setdefault(k, {})
                    d[path[-1]] = v
                self.edit(label, apply, merge=("page:" + merge) if merge else None)
            return fn

        if focus in (None, "title"):
            f = self._section("Title")
            t = QLineEdit(page.get("title", ""))
            t.setPlaceholderText("Map title")
            t.textEdited.connect(setp(["title"], "Title", "title"))
            f.addRow("Title", t)
            s = QLineEdit(page.get("subtitle", ""))
            s.setPlaceholderText("Optional – place, date, theme")
            s.textEdited.connect(setp(["subtitle"], "Subtitle", "subtitle"))
            f.addRow("Subtitle", s)
            ts = page.get("title_style") or {}
            fc = _font_combo(ts.get("font", "Georgia"))
            fc.currentIndexChanged.connect(lambda: setp(["title_style", "font"], "Title font")(fc.currentData()))
            f.addRow("Font", fc)
            sz = _spin(8, 72, ts.get("size", 22), 1, 0, " pt")
            sz.valueChanged.connect(setp(["title_style", "size"], "Title size", "tsize"))
            f.addRow("Size", sz)
            col = ColorButton(ts.get("color", "#1d1d1d"))
            col.colorChanged.connect(setp(["title_style", "color"], "Title colour"))
            f.addRow("Colour", col)
            al = _combo([("left", "Left"), ("center", "Centred")], page.get("title_align", "left"))
            al.currentIndexChanged.connect(lambda: setp(["title_align"], "Title position")(al.currentData()))
            f.addRow("Position", al)
            if focus == "title":
                self._note("Double-click the title on the page to edit it there.")
        if focus in (None, "legend"):
            f = self._section("Legend")
            lg = page.get("legend") or {}
            sh = QCheckBox("Show the legend")
            sh.setChecked(bool(lg.get("show", True)))
            sh.toggled.connect(setp(["legend", "show"], "Legend"))
            f.addRow(sh)
            tt = QLineEdit(lg.get("title", ""))
            tt.setPlaceholderText("None – or say what it explains")
            tt.textEdited.connect(setp(["legend", "title"], "Legend title", "ltitle"))
            f.addRow("Heading", tt)
            if focus == "legend":
                self._note("The legend lists data layers and the symbols, lines and areas marked ‘Show in the "
                           "legend’. A heading like “Legend” adds nothing – leave it empty or describe the content. "
                           "Drag it on the page to move it.")
        if focus in (None, "scalebar"):
            f = self._section("Scale bar")
            sb = page.get("scalebar") or {}
            sh = QCheckBox("Show the scale bar")
            sh.setChecked(bool(sb.get("show", True)))
            sh.toggled.connect(setp(["scalebar", "show"], "Scale bar"))
            f.addRow(sh)
            u = _combo([("metric", "Metres / kilometres"), ("imperial", "Feet / miles")], sb.get("units", "metric"))
            u.currentIndexChanged.connect(lambda: setp(["scalebar", "units"], "Scale units")(u.currentData()))
            f.addRow("Units", u)
            stl = _combo([("bar", "Alternating bar"), ("hollow", "Half-filled bar"), ("line", "Simple line")],
                         sb.get("style", "bar"))
            stl.currentIndexChanged.connect(lambda: setp(["scalebar", "style"], "Scale bar style")(stl.currentData()))
            f.addRow("Style", stl)
        if focus in (None, "north"):
            f = self._section("North arrow")
            nr = page.get("north") or {}
            sh = QCheckBox("Show the north arrow")
            sh.setChecked(bool(nr.get("show", True)))
            sh.toggled.connect(setp(["north", "show"], "North arrow"))
            f.addRow(sh)
            if focus == "north":
                self._note("North is up on this map, so the arrow is optional – many maps leave it out.")
        if focus in (None, "credits"):
            f = self._section("Sources and credits")
            cr = page.get("credits") or {}
            sh = QCheckBox("Show a line of sources under the map")
            sh.setChecked(bool(cr.get("show", True)))
            sh.toggled.connect(setp(["credits", "show"], "Credits"))
            f.addRow(sh)
            tx = QLineEdit(cr.get("text", ""))
            tx.setPlaceholderText("Your name, date, notes")
            tx.textEdited.connect(setp(["credits", "text"], "Credits text", "ctext"))
            f.addRow("Text", tx)
            au = QCheckBox("Add the sources of the layers automatically")
            au.setChecked(bool(cr.get("auto", True)))
            au.toggled.connect(setp(["credits", "auto"], "Automatic sources"))
            f.addRow(au)
        if focus is None:
            f = self._section("Page")
            pv = QCheckBox("Show the page (layout view)")
            pv.setChecked(bool(page.get("show")))
            pv.toggled.connect(lambda v: win.set_page_mode(v))
            f.addRow(pv)
            pp = _combo([(k, k) for k in PAPERS], page.get("paper", "A4"))
            pp.currentIndexChanged.connect(lambda: setp(["paper"], "Paper size")(pp.currentData()))
            f.addRow("Paper", pp)
            orient = _combo([(True, "Landscape"), (False, "Portrait")], bool(page.get("landscape", True)))
            orient.currentIndexChanged.connect(lambda: setp(["landscape"], "Orientation")(orient.currentData()))
            f.addRow("Orientation", orient)
            m = _spin(0, 60, page.get("margin_mm", 12), 1, 0, " mm")
            m.valueChanged.connect(setp(["margin_mm"], "Margin", "margin"))
            f.addRow("Margin", m)
            bg = ColorButton(page.get("background", "#ffffff"))
            bg.colorChanged.connect(setp(["background"], "Page colour"))
            f.addRow("Paper colour", bg)
            nl = QCheckBox("Frame line around the map")
            nl.setChecked(bool(page.get("neatline", True)))
            nl.toggled.connect(setp(["neatline"], "Frame line"))
            f.addRow(nl)
            self._note("Click the title, legend, scale bar or north arrow on the page to select them; drag to move. "
                       "Select a layer to change how it looks.")
