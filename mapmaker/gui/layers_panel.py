"""The Layers panel: the layer stack, top layer first, like an image editor.

Each row has an eye (show / hide) and a lock. Annotation layers open to list their labels,
symbols, lines and areas, which can be hidden, selected and dragged between layers.
Drag rows to change the order; double-click a name to rename it. Blend mode and opacity
for the selected layer sit above the list."""
from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QFrame, QHBoxLayout, QLabel, QMenu, QToolButton,
                               QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from ..document import item_title
from .controls import SliderSpin
from .icons import icon, symbol_icon
from .painter import BLENDS
from .theme import T

ROLE = Qt.ItemDataRole.UserRole
KIND_ICON = {"basemap": "k_basemap", "terrain": "k_terrain", "service": "k_service", "data": "k_data",
             "annotations": "k_annotations", "color": "k_color", "grid": "k_grid"}
KIND_NAME = {"basemap": "Background map", "terrain": "Terrain", "service": "Map service", "data": "Data",
             "annotations": "Labels and symbols", "color": "Background colour", "grid": "Coordinate grid"}


class LayerTree(QTreeWidget):
    moveLayer = Signal(str, str, bool)            # layer id, target layer id, above target
    moveItem = Signal(str, str, str, bool)        # item id, target layer id, target item id ("" = top), above
    deletePressed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setColumnCount(3)
        self.setHeaderHidden(True)
        self.setTreePosition(1)
        self.setIndentation(14)
        self.setRootIsDecorated(True)
        self.setUniformRowHeights(True)
        self.setIconSize(QSize(18, 18))
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.setEditTriggers(QAbstractItemView.EditTrigger.EditKeyPressed | QAbstractItemView.EditTrigger.DoubleClicked)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        hdr = self.header()
        hdr.setStretchLastSection(False)
        from PySide6.QtWidgets import QHeaderView
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        hdr.resizeSection(0, 30)
        hdr.resizeSection(2, 28)

    def dropEvent(self, ev):
        src = self.currentItem()
        tgt = self.itemAt(ev.position().toPoint())
        pos = self.dropIndicatorPosition()
        ev.ignore()
        ev.setDropAction(Qt.DropAction.IgnoreAction)
        if src is None:
            return
        s = src.data(1, ROLE)
        above = pos != QAbstractItemView.DropIndicatorPosition.BelowItem
        if tgt is None:
            if s[0] == "layer":
                last = self.topLevelItem(self.topLevelItemCount() - 1)
                if last is not None and last is not src:
                    self.moveLayer.emit(s[1], last.data(1, ROLE)[1], False)
            return
        t = tgt.data(1, ROLE)
        if s[0] == "layer":
            target_layer = t[1]
            if t[0] == "item":
                above = True
            if target_layer != s[1]:
                self.moveLayer.emit(s[1], target_layer, above)
        else:
            if t[0] == "item":
                if t[2] != s[2]:
                    self.moveItem.emit(s[2], t[1], t[2], above)
            elif t[2] == "annotations":
                self.moveItem.emit(s[2], t[1], "", True)

    def keyPressEvent(self, ev):
        if ev.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace) and self.state() != QAbstractItemView.State.EditingState:
            self.deletePressed.emit()
            return
        super().keyPressEvent(ev)


class LayersPanel(QWidget):
    def __init__(self, win, parent=None):
        super().__init__(parent)
        self.win = win
        self._building = False
        self._expanded: set = set()
        self._collapsed: set = set()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        head = QFrame()
        head.setObjectName("panelHead")
        hl = QVBoxLayout(head)
        hl.setContentsMargins(10, 6, 10, 6)
        hl.setSpacing(4)
        title = QLabel("Layers")
        hl.addWidget(title)
        row = QHBoxLayout()
        row.setSpacing(6)
        self.blend = QComboBox()
        for k, v in BLENDS.items():
            self.blend.addItem(v, k)
        self.blend.setToolTip("Blend mode – Multiply lets shading darken the layers below")
        self.opacity = SliderSpin(0, 100, 100)
        self.opacity.setToolTip("Opacity of the selected layer")
        row.addWidget(self.blend)
        row.addWidget(self.opacity, 1)
        hl.addLayout(row)
        lay.addWidget(head)
        self.tree = LayerTree()
        lay.addWidget(self.tree, 1)
        foot = QFrame()
        foot.setObjectName("panelFoot")
        fl = QHBoxLayout(foot)
        fl.setContentsMargins(6, 3, 6, 3)
        fl.setSpacing(2)

        def tb(name, tip, fn=None):
            b = QToolButton()
            b.setIcon(icon(name))
            b.setIconSize(QSize(18, 18))
            b.setToolTip(tip)
            b.setAutoRaise(True)
            if fn:
                b.clicked.connect(fn)
            fl.addWidget(b)
            return b
        self.add_btn = tb("add", "Add a layer")
        self.add_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.add_btn.setMenu(win.add_layer_menu(self))
        tb("k_annotations", "New layer for labels and symbols", win.new_annotation_layer)
        fl.addStretch(1)
        self.zoom_btn = tb("zoomto", "Zoom to the selected layer or item", lambda: win.zoom_to_selection())
        self.up_btn = tb("up", "Move up", lambda: win.move_selected(+1))
        self.down_btn = tb("down", "Move down", lambda: win.move_selected(-1))
        self.dup_btn = tb("duplicate", "Duplicate", lambda: win.duplicate_selected())
        self.del_btn = tb("delete", "Delete", lambda: win.delete_selected_layer_or_item())
        lay.addWidget(foot)

        t = self.tree
        t.itemClicked.connect(self._clicked)
        t.currentItemChanged.connect(self._current)
        t.itemChanged.connect(self._renamed)
        t.itemExpanded.connect(lambda it: self._track_expand(it, True))
        t.itemCollapsed.connect(lambda it: self._track_expand(it, False))
        t.customContextMenuRequested.connect(self._menu)
        t.moveLayer.connect(win.move_layer_to)
        t.moveItem.connect(win.move_item_to)
        t.deletePressed.connect(win.delete_selected_layer_or_item)
        self.opacity.valueChanged.connect(self._opacity)
        self.blend.currentIndexChanged.connect(self._blend)

    # ------------------------------------------------------------ building
    def refresh(self):
        doc = self.win.doc
        sel = self.win.canvas.sel
        self._building = True
        t = self.tree
        t.blockSignals(True)
        scroll = t.verticalScrollBar().value()
        t.clear()
        current = None
        for L in reversed(doc.layers):
            row = QTreeWidgetItem()
            row.setData(1, ROLE, ("layer", L["id"], L["kind"]))
            row.setText(1, L["name"])
            tip = KIND_NAME.get(L["kind"], L["kind"])
            if L["kind"] == "data":
                tb = doc.table(L)
                tip += f" – {(tb.n if tb is not None else 0):,} features".replace(",", " ")
            row.setToolTip(1, tip)
            row.setIcon(1, icon(KIND_ICON.get(L["kind"], "k_data"), color=T.muted if not L.get("visible", True)
                                else "#cfcfcf"))
            row.setIcon(0, icon("eye" if L.get("visible", True) else "eye_off", color="#d8d8d8"))
            row.setToolTip(0, "Show / hide")
            row.setIcon(2, icon("lock" if L.get("locked") else "unlock", color="#d8d8d8"))
            row.setToolTip(2, "Lock – a locked layer cannot be selected or changed on the map")
            flags = (Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEditable
                     | Qt.ItemFlag.ItemIsDragEnabled)
            if L["kind"] == "annotations":
                flags |= Qt.ItemFlag.ItemIsDropEnabled
            row.setFlags(flags)
            if not L.get("visible", True):
                row.setForeground(1, self.palette().placeholderText())
            t.addTopLevelItem(row)
            if L["id"] == sel.get("layer") and not sel.get("item"):
                current = row
            if L["kind"] == "annotations":
                for it in reversed(L.get("items", [])):
                    c = QTreeWidgetItem()
                    c.setData(1, ROLE, ("item", L["id"], it["id"]))
                    c.setText(1, item_title(it))
                    c.setIcon(1, self._item_icon(it))
                    c.setIcon(0, icon("eye" if not it.get("hidden") else "eye_off", color="#bdbdbd"))
                    c.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsDragEnabled)
                    if it.get("hidden"):
                        c.setForeground(1, self.palette().placeholderText())
                    row.addChild(c)
                    if it["id"] == sel.get("item"):
                        current = c
                expand = L["id"] in self._expanded or (L["id"] not in self._collapsed and len(L.get("items", [])) <= 12)
                if sel.get("item") and any(i["id"] == sel["item"] for i in L.get("items", [])):
                    expand = True
                row.setExpanded(expand)
        if current is not None:
            t.setCurrentItem(current)
        t.verticalScrollBar().setValue(scroll)
        t.blockSignals(False)
        self._building = False
        self._sync_controls()

    def _item_icon(self, it):
        if it["type"] == "symbol":
            return symbol_icon(it.get("symbol", "circle"), it.get("color", "#b03a2e"), 18, bool(it.get("badge")))
        return icon({"text": "i_text", "line": "i_line", "area": "i_area"}.get(it["type"], "i_text"),
                    color=(it.get("style") or {}).get("color") or (it.get("style") or {}).get("stroke") or "#cfcfcf")

    def _sync_controls(self):
        L = self.win.doc.layer(self.win.canvas.sel.get("layer"))
        on = L is not None
        for w in (self.blend, self.opacity, self.up_btn, self.down_btn, self.dup_btn, self.del_btn, self.zoom_btn):
            w.setEnabled(on)
        self.blend.blockSignals(True)
        self.blend.setCurrentIndex(max(0, self.blend.findData((L or {}).get("blend", "normal"))))
        self.blend.blockSignals(False)
        self.opacity.set_value(int(round(float((L or {}).get("opacity", 1.0)) * 100)))

    # ------------------------------------------------------------ events
    def _track_expand(self, it, on):
        d = it.data(1, ROLE)
        if d and d[0] == "layer" and not self._building:
            (self._expanded if on else self._collapsed).add(d[1])
            (self._collapsed if on else self._expanded).discard(d[1])

    def _clicked(self, it, col):
        d = it.data(1, ROLE)
        if not d:
            return
        if col == 0:
            if d[0] == "layer":
                self.win.toggle_visible(d[1])
            else:
                self.win.toggle_item_hidden(d[2])
        elif col == 2 and d[0] == "layer":
            self.win.toggle_locked(d[1])

    def _current(self, cur, _prev):
        if self._building or cur is None:
            return
        d = cur.data(1, ROLE)
        if d[0] == "layer":
            self.win.canvas.select(d[1], None, None)
        else:
            self.win.canvas.select(d[1], d[2], None)
        self._sync_controls()

    def _renamed(self, it, col):
        if self._building or col != 1:
            return
        d = it.data(1, ROLE)
        if d and d[0] == "layer":
            self.win.rename_layer(d[1], it.text(1))

    def _opacity(self, v):
        lid = self.win.canvas.sel.get("layer")
        if lid:
            self.win.set_layer_value(lid, "opacity", v / 100.0, "Opacity", merge=("opacity", lid))

    def _blend(self, _i):
        lid = self.win.canvas.sel.get("layer")
        if lid:
            self.win.set_layer_value(lid, "blend", self.blend.currentData(), "Blend mode")

    def _menu(self, pos):
        it = self.tree.itemAt(pos)
        if it is None:
            m = QMenu(self)
            m.addMenu(self.win.add_layer_menu(m))
            m.exec(self.tree.viewport().mapToGlobal(pos))
            return
        self.tree.setCurrentItem(it)
        d = it.data(1, ROLE)
        m = QMenu(self)
        w = self.win
        if d[0] == "layer":
            L = w.doc.layer(d[1])
            m.addAction("Rename", lambda: self.tree.editItem(it, 1))
            m.addAction("Hide" if L.get("visible", True) else "Show", lambda: w.toggle_visible(d[1]))
            m.addAction("Unlock" if L.get("locked") else "Lock", lambda: w.toggle_locked(d[1]))
            m.addSeparator()
            m.addAction("Zoom to layer", lambda: w.zoom_to_selection())
            m.addAction("Duplicate", lambda: w.duplicate_selected())
            m.addAction("Move up", lambda: w.move_selected(+1))
            m.addAction("Move down", lambda: w.move_selected(-1))
            m.addSeparator()
            m.addAction("Delete layer", lambda: w.delete_selected_layer_or_item())
        else:
            _L, item = w.doc.find_item(d[2])
            if item and item["type"] in ("text", "symbol"):
                m.addAction("Edit text" if item["type"] == "text" else "Edit name",
                            lambda: w.canvas.edit_item_text(d[2]))
            m.addAction("Show" if item and item.get("hidden") else "Hide", lambda: w.toggle_item_hidden(d[2]))
            m.addAction("Zoom to", lambda: w.zoom_to_selection())
            m.addAction("Duplicate", lambda: w.duplicate_selected())
            m.addSeparator()
            m.addAction("Delete", lambda: w.delete_selected_layer_or_item())
        m.exec(self.tree.viewport().mapToGlobal(pos))
