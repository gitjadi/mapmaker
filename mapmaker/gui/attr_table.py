"""The attribute table: the features of a data layer as rows – sort by a column, filter by text,
show only the selected features, and select rows to select the features on the map.

The table reads straight from the layer's columns, so it opens instantly even for
half a million features (only the rows on screen are ever turned into text)."""
from __future__ import annotations

import numpy as np
from PySide6.QtCore import QAbstractTableModel, QItemSelection, QItemSelectionModel, QModelIndex, Qt, QTimer
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QDockWidget, QHBoxLayout, QLabel, QLineEdit,
                               QPushButton, QTableView, QVBoxLayout, QWidget)

from ..table import query
from .icons import icon
from .theme import T


class _Model(QAbstractTableModel):
    def __init__(self):
        super().__init__()
        self.t = None
        self.rows = np.zeros(0, dtype=np.int64)

    def set(self, t, rows):
        self.beginResetModel()
        self.t = t
        self.rows = np.asarray(rows, dtype=np.int64)
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() or self.t is None else len(self.rows)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() or self.t is None else len(self.t.fields)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if self.t is None or not index.isValid():
            return None
        f = self.t.fields[index.column()]
        if role == Qt.ItemDataRole.DisplayRole:
            return self.t.cols[f].text(int(self.rows[index.row()]))
        if role == Qt.ItemDataRole.TextAlignmentRole and self.t.cols[f].kind == "num":
            return int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        return None

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if role != Qt.ItemDataRole.DisplayRole or self.t is None:
            return None
        if orientation == Qt.Orientation.Horizontal:
            return self.t.fields[section] if section < len(self.t.fields) else None
        return str(int(self.rows[section]) + 1) if section < len(self.rows) else None


class AttributeTable(QDockWidget):
    def __init__(self, win):
        super().__init__("Attribute table", win)
        self.win = win
        self.setObjectName("attrtable")
        self.setAllowedAreas(Qt.DockWidgetArea.BottomDockWidgetArea | Qt.DockWidgetArea.RightDockWidgetArea)
        body = QWidget()
        lay = QVBoxLayout(body)
        lay.setContentsMargins(6, 4, 6, 6)
        lay.setSpacing(4)
        bar = QHBoxLayout()
        bar.setSpacing(6)
        self.layer = QComboBox()
        self.layer.setMinimumContentsLength(16)
        self.show_sel = QComboBox()
        self.show_sel.addItem("All features", "all")
        self.show_sel.addItem("Selected features", "sel")
        self.filter = QLineEdit()
        self.filter.setPlaceholderText("Find rows containing…")
        self.filter.setClearButtonEnabled(True)
        self.count = QLabel()
        self.count.setStyleSheet(f"color:{T.muted};")
        zoom = QPushButton(icon("zoomto"), " Zoom to selected")
        sel_rows = QPushButton("Select these rows")
        sel_rows.setToolTip("Select every feature listed (e.g. all rows matching the search)")
        bar.addWidget(QLabel("Layer"))
        bar.addWidget(self.layer)
        bar.addWidget(QLabel("Show"))
        bar.addWidget(self.show_sel)
        bar.addWidget(self.filter, 1)
        bar.addWidget(sel_rows)
        bar.addWidget(zoom)
        bar.addWidget(self.count)
        lay.addLayout(bar)
        self.model = _Model()
        self.view = QTableView()
        self.view.setModel(self.model)
        self.view.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.view.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.view.setAlternatingRowColors(True)
        self.view.setWordWrap(False)
        self.view.verticalHeader().setDefaultSectionSize(22)
        self.view.horizontalHeader().setSectionsClickable(True)
        self.view.horizontalHeader().setSortIndicatorShown(True)
        self.view.horizontalHeader().sectionClicked.connect(self._sort)
        self.view.doubleClicked.connect(self._zoom_row)
        lay.addWidget(self.view, 1)
        self.setWidget(body)
        self._sort_col = None
        self._sort_desc = False
        self._syncing = False
        self._ftimer = QTimer(self)
        self._ftimer.setSingleShot(True)
        self._ftimer.setInterval(250)
        self._ftimer.timeout.connect(self.reload)
        self.layer.currentIndexChanged.connect(self._layer_changed)
        self.show_sel.currentIndexChanged.connect(self.reload)
        self.filter.textChanged.connect(lambda: self._ftimer.start())
        zoom.clicked.connect(win.selbar.zoom)
        sel_rows.clicked.connect(self._select_listed)
        self.view.selectionModel().selectionChanged.connect(self._rows_selected)
        win.canvas.featureSelectionChanged.connect(self._map_selection_changed)

    # ------------------------------------------------------------ data
    def current(self):
        L = self.win.doc.layer(self.layer.currentData())
        return L, (self.win.doc.table(L) if L else None)

    def refresh(self):
        doc = self.win.doc
        data = [L for L in reversed(doc.layers) if L["kind"] == "data"]
        sig = [(L["id"], L["name"], L.get("table")) for L in data]
        want = self.win.selbar.layer.currentData()
        if sig == getattr(self, "_sig", None) and (want is None or want == self.layer.currentData()):
            return
        self._sig = sig
        cur = want or self.layer.currentData()
        self.layer.blockSignals(True)
        self.layer.clear()
        for L in data:
            self.layer.addItem(L["name"], L["id"])
        self.layer.setCurrentIndex(max(0, self.layer.findData(cur)))
        self.layer.blockSignals(False)
        self.reload()

    def _layer_changed(self):
        self._sort_col = None
        self.reload()
        lid = self.layer.currentData()
        i = self.win.selbar.layer.findData(lid)
        if i >= 0 and self.win.selbar.layer.currentIndex() != i:
            self.win.selbar.layer.setCurrentIndex(i)

    def reload(self):
        L, t = self.current()
        if t is None:
            self.model.set(None, [])
            self.count.setText("")
            return
        mask = np.ones(t.n, dtype=bool)
        sel = self.win.canvas.fsel.get(L["id"])
        if self.show_sel.currentData() == "sel":
            mask = sel.copy() if sel is not None and len(sel) == t.n else np.zeros(t.n, dtype=bool)
        text = self.filter.text().strip()
        if text:
            hit = np.zeros(t.n, dtype=bool)
            for f in t.fields:
                try:
                    hit |= query(t, f, "contains", text)
                except Exception:
                    pass
            mask &= hit
        rows = np.nonzero(mask)[0]
        if self._sort_col is not None and self._sort_col < len(t.fields):
            rows = rows[np.argsort(self._sort_key(t, t.fields[self._sort_col])[rows], kind="stable")]
            if self._sort_desc:
                rows = rows[::-1]
        self.model.set(t, rows)
        nsel = int(sel.sum()) if sel is not None and len(sel) == t.n else 0
        self.count.setText(f"{len(rows):,} rows · {nsel:,} selected".replace(",", " "))
        self._select_rows_of_selection()

    @staticmethod
    def _sort_key(t, f):
        col = t.cols[f]
        if col.kind == "num":
            return np.where(np.isfinite(col.values), col.values, np.inf)
        order = sorted(range(len(col.cats)), key=lambda i: str(col.cats[i]).casefold())
        rank = np.empty(len(col.cats) + 1, dtype=np.int64)
        rank[order] = np.arange(len(order))
        rank[-1] = len(order) + 1
        return rank[col.codes]

    def _sort(self, section):
        if self._sort_col == section:
            self._sort_desc = not self._sort_desc
        else:
            self._sort_col, self._sort_desc = section, False
        self.view.horizontalHeader().setSortIndicator(
            section, Qt.SortOrder.DescendingOrder if self._sort_desc else Qt.SortOrder.AscendingOrder)
        self.reload()

    # ------------------------------------------------------------ selection both ways
    def _rows_selected(self, *_):
        if self._syncing:
            return
        L, t = self.current()
        if t is None:
            return
        rows = sorted({i.row() for i in self.view.selectionModel().selectedRows()})
        mask = np.zeros(t.n, dtype=bool)
        if rows:
            mask[self.model.rows[rows]] = True
        self._syncing = True
        try:
            self.win.canvas.fsel_layer = L["id"]
            self.win.canvas.set_feature_selection(L["id"], mask)
        finally:
            self._syncing = False

    def _map_selection_changed(self):
        if self._syncing or not self.isVisible():
            return
        if self.show_sel.currentData() == "sel":
            self.reload()
        else:
            self._select_rows_of_selection()
            L, t = self.current()
            if t is not None:
                sel = self.win.canvas.fsel.get(L["id"])
                nsel = int(sel.sum()) if sel is not None and len(sel) == t.n else 0
                self.count.setText(f"{self.model.rowCount():,} rows · {nsel:,} selected".replace(",", " "))

    def _select_rows_of_selection(self):
        """Highlight the rows of the features selected on the map (up to a few thousand)."""
        L, t = self.current()
        if t is None:
            return
        sel = self.win.canvas.fsel.get(L["id"])
        sm = self.view.selectionModel()
        self._syncing = True
        try:
            sm.clearSelection()
            if sel is None or len(sel) != t.n:
                return
            pos = np.nonzero(sel[self.model.rows])[0]
            if not len(pos) or len(pos) > 5000:
                return
            qs = QItemSelection()
            last = self.model.columnCount() - 1
            # contiguous runs become one range each
            breaks = np.nonzero(np.diff(pos) != 1)[0]
            starts = np.concatenate([[pos[0]], pos[breaks + 1]])
            ends = np.concatenate([pos[breaks], [pos[-1]]])
            for a, b in zip(starts.tolist(), ends.tolist()):
                qs.select(self.model.index(a, 0), self.model.index(b, last))
            sm.select(qs, QItemSelectionModel.SelectionFlag.Select)
            self.view.scrollTo(self.model.index(int(pos[0]), 0))
        finally:
            self._syncing = False

    def _select_listed(self):
        L, t = self.current()
        if t is None:
            return
        mask = np.zeros(t.n, dtype=bool)
        mask[self.model.rows] = True
        self.win.canvas.fsel_layer = L["id"]
        self.win.canvas.set_feature_selection(L["id"], mask)

    def _zoom_row(self, index):
        L, t = self.current()
        if t is None:
            return
        i = int(self.model.rows[index.row()])
        bb = t.bbox_of(np.array([i]))
        if bb:
            w, s, e, n = bb
            if e - w < 1e-6 and n - s < 1e-6:
                self.win.canvas.center_on(w, s, max(self.win.doc.view[2], 15))
            else:
                self.win.canvas.zoom_to_bbox(w, s, e, n)
