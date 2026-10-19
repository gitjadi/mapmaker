"""The selection bar under the menus: select features of a data layer by their attributes, as in
ArcGIS's "Select by attributes" – choose a layer, a field, a condition and a value, then Apply.

The method decides how the result combines with what is already selected (new selection, add,
remove, or select within it), so "graves in Uppsala county" is two quick steps. The selection
can be inverted, zoomed to, made into a new layer, or removed from its layer."""
from __future__ import annotations

import numpy as np
from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import QComboBox, QCompleter, QLabel, QPushButton, QSizePolicy, QToolBar, QToolButton

from ..table import NO_VALUE_OPS, OPS, query
from .icons import icon
from .theme import T

METHODS = [("new", "New selection"), ("add", "Add to selection"), ("remove", "Remove from selection"),
           ("within", "Select within selection")]


class SelectionBar(QToolBar):
    def __init__(self, win):
        super().__init__("Selection")
        self.win = win
        self.setObjectName("selbar")
        self.setMovable(False)
        self.setIconSize(QSize(18, 18))
        self._building = False
        lab = QLabel("Select in")
        self.addWidget(lab)
        self.layer = QComboBox()
        self.layer.setMinimumContentsLength(14)
        self.layer.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.layer.setToolTip("The data layer to select features in")
        self.addWidget(self.layer)
        self.addWidget(QLabel("where"))
        self.field = QComboBox()
        self.field.setMinimumContentsLength(12)
        self.field.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.field.setToolTip("The attribute to test")
        self.addWidget(self.field)
        self.op = QComboBox()
        for k, v in OPS:
            self.op.addItem(v, k)
        self.addWidget(self.op)
        self.value = QComboBox()
        self.value.setEditable(True)
        self.value.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.value.setMinimumContentsLength(16)
        self.value.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.value.lineEdit().setPlaceholderText("value – pick or type")
        self.value.setToolTip("Pick one of the values in the layer (most common first) or type your own. "
                              "For “is one of”, separate values with commas.")
        self.addWidget(self.value)
        self.method = QComboBox()
        for k, v in METHODS:
            self.method.addItem(v, k)
        self.addWidget(self.method)
        self.apply_btn = QPushButton("Apply")
        self.apply_btn.setProperty("primary", True)
        self.apply_btn.setToolTip("Select the features that match (Enter in the value box)")
        self.addWidget(self.apply_btn)
        self.count = QLabel()
        self.count.setStyleSheet(f"color:{T.muted}; padding: 0 10px;")
        self.count.setMinimumWidth(150)
        self.addWidget(self.count)
        self.addSeparator()

        def btn(text, tip, fn, icon_name=None):
            b = QToolButton()
            b.setText(text)
            b.setToolTip(tip)
            if icon_name:
                b.setIcon(icon(icon_name))
                b.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
            b.clicked.connect(fn)
            self.addWidget(b)
            return b
        self.invert_btn = btn("Invert", "Select the features that are not selected, and vice versa", self.invert)
        self.clear_btn = btn("Clear", "Select nothing (Esc with the Select-features tool)", self.clear)
        self.zoom_btn = btn("Zoom to", "Zoom to the selected features", self.zoom, "zoomto")
        self.new_btn = btn("New layer from selection", "Copy the selected features into a new layer you can style "
                           "on its own", self.new_layer, "add")
        self.remove_btn = btn("Remove from layer", "Take the selected features out of the layer (Ctrl+Z undoes it)",
                              self.remove, "delete")
        spacer = QLabel()
        spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.spacer_action = self.addWidget(spacer)

        self.layer.currentIndexChanged.connect(self._layer_changed)
        self.field.currentIndexChanged.connect(self._field_changed)
        self.op.currentIndexChanged.connect(self._op_changed)
        self.value.activated.connect(self._value_picked)
        self.value.lineEdit().returnPressed.connect(self.apply)
        self.apply_btn.clicked.connect(self.apply)
        win.canvas.featureSelectionChanged.connect(self.update_count)

    # ------------------------------------------------------------ state
    def current_layer(self):
        return self.win.doc.layer(self.layer.currentData())

    def refresh(self):
        """Rebuild the layer list (after layers are added, removed or renamed)."""
        doc = self.win.doc
        data = [L for L in reversed(doc.layers) if L["kind"] == "data"]
        cur = self.layer.currentData()
        want = self.win.canvas.sel.get("layer")
        if not any(L["id"] == cur for L in data):
            cur = None
        if want and any(L["id"] == want for L in data):
            cur = want
        sig = [(L["id"], L["name"]) for L in data]
        if sig != getattr(self, "_sig", None) or cur != self.layer.currentData():
            self._sig = sig
            self._building = True
            self.layer.clear()
            for L in data:
                self.layer.addItem(icon("k_data"), L["name"], L["id"])
            if not data:
                self.layer.addItem("No data layers yet", None)
            i = self.layer.findData(cur) if cur else 0
            self.layer.setCurrentIndex(max(0, i))
            self._building = False
            self._layer_changed()
        self._enable()

    def _enable(self):
        L = self.current_layer()
        on = L is not None
        for w in (self.field, self.op, self.value, self.method, self.apply_btn):
            w.setEnabled(on)
        m = self._mask()
        has = m is not None and bool(m.any())
        for w in (self.clear_btn, self.zoom_btn, self.new_btn, self.remove_btn):
            w.setEnabled(has)
        self.invert_btn.setEnabled(on)
        self.update_count()

    def _layer_changed(self, *_):
        if self._building:
            return
        L = self.current_layer()
        self.win.canvas.fsel_layer = L["id"] if L else None
        t = self.win.doc.table(L) if L else None
        prev = self.field.currentText()
        self.field.blockSignals(True)
        self.field.clear()
        if t is not None:
            for f in t.fields:
                self.field.addItem(f, f)
            i = self.field.findData(prev)
            self.field.setCurrentIndex(i if i >= 0 else 0)
        self.field.blockSignals(False)
        self._field_changed()
        self._enable()

    def _field_changed(self, *_):
        L = self.current_layer()
        t = self.win.doc.table(L) if L else None
        f = self.field.currentData()
        self.value.blockSignals(True)
        text = self.value.currentText()
        self.value.clear()
        if t is not None and f in t.cols:
            counts = t.cols[f].counts(400)
            for v, c in counts:
                self.value.addItem(f"{v}   ({c:,})".replace(",", " "), str(v))
            comp = QCompleter([str(v) for v, _ in counts], self.value)
            comp.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
            comp.setFilterMode(Qt.MatchFlag.MatchContains)
            self.value.setCompleter(comp)
        self.value.setEditText(text)
        self.value.blockSignals(False)

    def _value_picked(self, i):
        v = self.value.itemData(i)
        if v is not None:
            self.value.setEditText(str(v))

    def _op_changed(self, *_):
        self.value.setEnabled(self.op.currentData() not in NO_VALUE_OPS and self.current_layer() is not None)

    def _mask(self):
        L = self.current_layer()
        if L is None:
            return None
        return self.win.canvas.fsel.get(L["id"])

    def update_count(self):
        L = self.current_layer()
        t = self.win.doc.table(L) if L else None
        if t is None:
            self.count.setText("")
            return
        m = self._mask()
        n = int(m.sum()) if m is not None and len(m) == t.n else 0
        self.count.setText(f"{n:,} of {t.n:,} selected".replace(",", " "))
        has = n > 0
        for w in (self.clear_btn, self.zoom_btn, self.new_btn, self.remove_btn):
            w.setEnabled(has)

    # ------------------------------------------------------------ actions
    def apply(self):
        L = self.current_layer()
        t = self.win.doc.table(L) if L else None
        if t is None:
            return
        field_ = self.field.currentData()
        op = self.op.currentData()
        val = self.value.currentText().strip()
        # a picked list entry shows its count – use the bare value
        i = self.value.findText(val)
        if i >= 0 and self.value.itemData(i) is not None:
            val = self.value.itemData(i)
        try:
            hit = query(t, field_, op, val)
        except Exception as e:
            self.win.status(f"Could not select: {e}", 6000)
            return
        old = self._mask()
        if old is None or len(old) != t.n:
            old = np.zeros(t.n, dtype=bool)
        method = self.method.currentData()
        new = {"new": hit, "add": old | hit, "remove": old & ~hit, "within": old & hit}[method]
        self.win.canvas.set_feature_selection(L["id"], new)
        self._last_value = val if op not in NO_VALUE_OPS else self.op.currentText()
        self.win.status(f"{int(hit.sum()):,} features where {field_} {self.op.currentText()} {val}".replace(",", " "),
                        6000)

    def invert(self):
        L = self.current_layer()
        t = self.win.doc.table(L) if L else None
        if t is None:
            return
        m = self._mask()
        m = np.zeros(t.n, dtype=bool) if m is None or len(m) != t.n else m
        self.win.canvas.set_feature_selection(L["id"], ~m)

    def clear(self):
        L = self.current_layer()
        if L is not None:
            self.win.canvas.clear_feature_selection(L["id"])

    def zoom(self):
        L = self.current_layer()
        m = self._mask()
        t = self.win.doc.table(L) if L else None
        if t is None or m is None or not m.any():
            return
        bb = t.bbox_of(np.nonzero(m)[0])
        if bb:
            w, s, e, n = bb
            if e - w < 1e-6 and n - s < 1e-6:
                self.win.canvas.center_on(w, s, max(self.win.doc.view[2], 14))
            else:
                self.win.canvas.zoom_to_bbox(w, s, e, n)

    def new_layer(self):
        L = self.current_layer()
        m = self._mask()
        t = self.win.doc.table(L) if L else None
        if t is None or m is None or not m.any():
            return
        self.win.layer_from_selection(L, m, getattr(self, "_last_value", ""))

    def remove(self):
        L = self.current_layer()
        m = self._mask()
        t = self.win.doc.table(L) if L else None
        if t is None or m is None or not m.any():
            return
        self.win.remove_selected_features(L, m)
