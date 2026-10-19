"""The main window: tools on the left, the canvas in the middle, Properties and Layers on the
right, tool options along the top – the familiar layout of an image editor, for maps."""
from __future__ import annotations

import copy
import logging
import math
import os
from pathlib import Path

from PySide6.QtCore import QSettings, QSize, Qt, QTimer
from PySide6.QtGui import QAction, QActionGroup, QDesktopServices, QIcon, QKeySequence
from PySide6.QtCore import QUrl
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QInputDialog, QLabel,
                               QMainWindow, QMenu, QMessageBox, QPushButton, QSizePolicy, QSplitter, QToolBar,
                               QToolButton, QToolTip, QWidget)

from .. import __version__
from ..document import (SUFFIX, Document, annotation_layer, basemap_layer, color_layer, data_layer, grid_layer, item_bbox,
                        new_id, push_recent, recent_list, service_layer,
                        terrain_layer)
from ..paths import data_dir, maps_dir
from ..styles import DASHES, preset_names
from ..terrain import TERRAIN_STYLES
from ..tiles import BASEMAPS, TileCache
from .canvas import TOOL_HINTS, MapCanvas
from .controls import ColorButton, SliderSpin, SymbolPicker
from .icons import icon
from .layers_panel import LayersPanel
from .properties import PropertiesPanel
from .theme import T
from .workers import run_async

log = logging.getLogger("mapmaker")
TOOLS = [("select", "Select and move", "V"), ("marquee", "Select features", "M"), ("pan", "Pan", "H"),
         ("text", "Text", "T"), ("symbol", "Symbol", "S"), ("line", "Line", "L"), ("area", "Area", "A")]


class MainWindow(QMainWindow):
    def __init__(self, tiles: TileCache | None = None):
        super().__init__()
        self.settings = QSettings("MapMaker", "MapMaker")
        from ..paths import cache_dir
        self.tiles = tiles or TileCache(cache_dir())
        self.doc = Document.new()
        self.last_export = None
        self.setWindowIcon(QIcon(str(Path(__file__).with_name("icon.png"))))
        self.resize(1440, 900)
        self.canvas = MapCanvas(self.doc, self.tiles)
        self.props = PropertiesPanel(self)
        self.layers = LayersPanel(self)
        from PySide6.QtWidgets import QTabWidget

        from .design_panel import DesignPanel
        self.design = DesignPanel(self)
        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.tabs.addTab(self.props, "Properties")
        self.tabs.addTab(self.design, "Design")
        self.tabs.currentChanged.connect(lambda i: self.design.refresh(force=True) if i == 1 else None)
        right = QSplitter(Qt.Orientation.Vertical)
        right.addWidget(self.tabs)
        right.addWidget(self.layers)
        right.setStretchFactor(0, 5)
        right.setStretchFactor(1, 4)
        right.setChildrenCollapsible(False)
        right.setMinimumWidth(300)
        self.right = right
        split = QSplitter(Qt.Orientation.Horizontal)
        split.addWidget(self.canvas)
        split.addWidget(right)
        split.setStretchFactor(0, 1)
        split.setStretchFactor(1, 0)
        split.setSizes([1100, 340])
        split.setChildrenCollapsible(False)
        self.split = split
        self.setCentralWidget(split)
        self._build_tools()
        from .selection_bar import SelectionBar
        self.selbar = SelectionBar(self)
        self.addToolBar(Qt.ToolBarArea.TopToolBarArea, self.selbar)
        self.addToolBarBreak(Qt.ToolBarArea.TopToolBarArea)
        from .attr_table import AttributeTable
        self.attr = AttributeTable(self)
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, self.attr)
        self.attr.hide()
        self.attr.visibilityChanged.connect(lambda v: v and self.attr.refresh())
        tb_btn = QToolButton()
        tb_btn.setIcon(icon("table"))
        tb_btn.setText(" Table")
        tb_btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        tb_btn.setToolTip("Show the attribute table of the layer (Ctrl+T)")
        tb_btn.clicked.connect(self.toggle_table)
        self.selbar.insertWidget(self.selbar.spacer_action, tb_btn)
        self._build_options()
        self._build_menus()
        self._build_status()
        self.canvas.selectionChanged.connect(self._selection_changed)
        self.canvas.changed.connect(lambda label: self.after_change(label))
        self.canvas.viewChanged.connect(self._view_changed)
        self.canvas.mouseMoved.connect(self._mouse_moved)
        self.canvas.hint.connect(lambda t: self.hint_lab.setText(t))
        self.canvas.toolChanged.connect(self._tool_changed)
        self._hover_timer = QTimer(self)
        self._hover_timer.setSingleShot(True)
        self._hover_timer.timeout.connect(self._hover_feature)
        self._restore()
        self.set_tool("select")
        self.after_change("", rebuild_props=True)

    # ================================================================ building the window
    def _build_tools(self):
        tb = QToolBar("Tools")
        tb.setObjectName("tools")
        tb.setMovable(False)
        tb.setOrientation(Qt.Orientation.Vertical)
        tb.setIconSize(QSize(22, 22))
        self.addToolBar(Qt.ToolBarArea.LeftToolBarArea, tb)
        self.tool_group = QActionGroup(self)
        self.tool_actions = {}
        for key, name, sc in TOOLS:
            a = QAction(icon(key, "#d8d8d8", active="#ffffff", size=22), f"{name}", self)
            a.setToolTip(f"{name}  ({sc})\n{TOOL_HINTS.get(key, '')}")
            a.setShortcut(QKeySequence(sc))
            a.setCheckable(True)
            a.triggered.connect(lambda _=False, k=key: self.set_tool(k))
            self.tool_group.addAction(a)
            tb.addAction(a)
            self.tool_actions[key] = a
        tb.addSeparator()

    def _build_options(self):
        tb = QToolBar("Tool options")
        tb.setObjectName("options")
        tb.setMovable(False)
        tb.setIconSize(QSize(18, 18))
        self.addToolBar(Qt.ToolBarArea.TopToolBarArea, tb)
        self.options = tb
        self.opt_widgets: dict[str, list] = {}
        c = self.canvas
        # text
        preset = QComboBox()
        for k, label in preset_names():
            preset.addItem(label, k)
        preset.setToolTip("The kind of label – each has a type style following map conventions")
        preset.currentIndexChanged.connect(lambda: c.opts["text"].__setitem__("preset", preset.currentData()))
        self.opt_widgets["text"] = [self._opt_label("Label style"), preset]
        # symbol
        sp = SymbolPicker(c.opts["symbol"]["symbol"])
        sp.symbolChanged.connect(lambda k: c.opts["symbol"].__setitem__("symbol", k))
        col = ColorButton(c.opts["symbol"]["color"])
        col.colorChanged.connect(lambda v: c.opts["symbol"].__setitem__("color", v))
        size = QDoubleSpinBox()
        size.setRange(6, 120)
        size.setDecimals(0)
        size.setSuffix(" px")
        size.setValue(c.opts["symbol"]["size"])
        size.valueChanged.connect(lambda v: c.opts["symbol"].__setitem__("size", v))
        badge = QCheckBox("Badge")
        badge.setToolTip("White symbol on a coloured disc")
        badge.toggled.connect(lambda v: c.opts["symbol"].__setitem__("badge", v))
        self.opt_widgets["symbol"] = [self._opt_label("Symbol"), sp, self._opt_label("Colour"), col,
                                      self._opt_label("Size"), size, badge]
        # line
        lcol = ColorButton(c.opts["line"]["color"])
        lcol.colorChanged.connect(lambda v: c.opts["line"].__setitem__("color", v))
        lw = QDoubleSpinBox()
        lw.setRange(0.25, 30)
        lw.setSingleStep(0.5)
        lw.setSuffix(" px")
        lw.setValue(c.opts["line"]["width"])
        lw.valueChanged.connect(lambda v: c.opts["line"].__setitem__("width", v))
        ld = QComboBox()
        for k, v in DASHES.items():
            ld.addItem(v, k)
        ld.currentIndexChanged.connect(lambda: c.opts["line"].__setitem__("dash", ld.currentData()))
        arrow = QCheckBox("Arrow")
        arrow.toggled.connect(lambda v: c.opts["line"].__setitem__("arrow", v))
        self.opt_widgets["line"] = [self._opt_label("Colour"), lcol, self._opt_label("Width"), lw,
                                    self._opt_label("Pattern"), ld, arrow]
        # area
        fcol = ColorButton(c.opts["area"]["fill"])

        def set_fill(v):
            c.opts["area"]["fill"] = v
            c.opts["area"]["stroke"] = v
        fcol.colorChanged.connect(set_fill)
        fop = SliderSpin(0, 100, int(c.opts["area"]["fill_opacity"] * 100))
        fop.setFixedWidth(180)
        fop.valueChanged.connect(lambda v: c.opts["area"].__setitem__("fill_opacity", v / 100))
        self.opt_widgets["area"] = [self._opt_label("Colour"), fcol, self._opt_label("Fill opacity"), fop]
        self._opt_actions = {}
        # always: view switch on the right
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self._spacer = spacer
        self.zoom_lab = QLabel()
        self.zoom_lab.setStyleSheet(f"color:{T.muted}; padding: 0 8px;")
        self.fit_btn = QToolButton()
        self.fit_btn.setText("Fit")
        self.fit_btn.setToolTip("Fit the page in the window (Ctrl+0)")
        self.fit_btn.clicked.connect(self.canvas.fit_page)
        self.map_btn = QPushButton(icon("map"), " Map")
        self.page_btn = QPushButton(icon("page"), " Page")
        for b, seg in ((self.map_btn, "first"), (self.page_btn, "last")):
            b.setCheckable(True)
            b.setProperty("seg", seg)
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.map_btn.setToolTip("Map view – work on the map itself (Ctrl+L)")
        self.page_btn.setToolTip("Page layout – the map on paper with title, legend and scale bar (Ctrl+L)")
        self.map_btn.clicked.connect(lambda: self.set_page_mode(False))
        self.page_btn.clicked.connect(lambda: self.set_page_mode(True))
        self._fixed_right = [spacer, self.zoom_lab, self.fit_btn, self.map_btn, self.page_btn]

    def _opt_label(self, text):
        lab = QLabel(text)
        return lab

    def _install_options(self):
        """Add every tool's option widgets once; switching tools only shows or hides them."""
        tb = self.options
        self._opt_actions = {}
        self._hint_lab = QLabel()
        self._hint_lab.setObjectName("hint")
        self._opt_actions["_hint"] = [tb.addWidget(self._hint_lab)]
        for tool, widgets in self.opt_widgets.items():
            self._opt_actions[tool] = [tb.addWidget(w) for w in widgets]
        for w in self._fixed_right:
            tb.addWidget(w)

    def _show_options(self, tool):
        if not self._opt_actions:
            self._install_options()
        for key, acts in self._opt_actions.items():
            on = key == tool or (key == "_hint" and not self.opt_widgets.get(tool))
            for a in acts:
                a.setVisible(on)
        self._hint_lab.setText(TOOL_HINTS.get(tool, "") if not self.opt_widgets.get(tool) else "")

    def _build_menus(self):
        mb = self.menuBar()
        # File
        m = mb.addMenu("&File")
        self._act(m, "New map", self.new_map, QKeySequence.StandardKey.New)
        self._act(m, "Open…", self.open_map, QKeySequence.StandardKey.Open)
        self.recent_menu = m.addMenu("Open recent")
        self.recent_menu.aboutToShow.connect(self._fill_recent)
        m.addSeparator()
        self._act(m, "Save", self.save, QKeySequence.StandardKey.Save)
        self._act(m, "Save as…", self.save_as, QKeySequence("Ctrl+Shift+S"))
        m.addSeparator()
        self._act(m, "Import data file…", self.import_file, QKeySequence("Ctrl+I"))
        self._act(m, "Download from an ArcGIS service…", self.download_arcgis)
        m.addSeparator()
        self._act(m, "Export…", self.export, QKeySequence("Ctrl+E"), "export")
        m.addSeparator()
        self._act(m, "Exit", self.close, QKeySequence("Ctrl+Q"))
        # Edit
        m = mb.addMenu("&Edit")
        self.undo_act = self._act(m, "Undo", self.undo, QKeySequence.StandardKey.Undo, "undo")
        self.redo_act = self._act(m, "Redo", self.redo, QKeySequence("Ctrl+Shift+Z"), "redo")
        self.redo_act.setShortcuts([QKeySequence("Ctrl+Shift+Z"), QKeySequence("Ctrl+Y")])
        m.addSeparator()
        self._act(m, "Duplicate", self.duplicate_selected, QKeySequence("Ctrl+J"), "duplicate")
        self._act(m, "Delete", self.delete_selected_layer_or_item, None, "delete")
        self._act(m, "Deselect", lambda: self.canvas.select(self.canvas.sel["layer"], None, None), QKeySequence("Ctrl+D"))
        # View
        m = mb.addMenu("&Select")
        m.addAction(self.tool_actions["marquee"])
        self._act(m, "Select by attributes", lambda: (self.selbar.value.setFocus(), self.selbar.value.lineEdit()
                                                       .selectAll()), QKeySequence("Ctrl+F"))
        m.addSeparator()
        self._act(m, "Select all features", self._select_all, QKeySequence("Ctrl+Shift+A"))
        self._act(m, "Invert selection", self.selbar.invert, QKeySequence("Ctrl+Shift+I"))
        self._act(m, "Clear selection", lambda: self.canvas.clear_feature_selection(), QKeySequence("Ctrl+Shift+D"))
        m.addSeparator()
        self._act(m, "Zoom to selected features", self.selbar.zoom)
        self._act(m, "New layer from selection", self.selbar.new_layer, QKeySequence("Ctrl+Shift+J"), "add")
        self._act(m, "Remove selected features from the layer", self.selbar.remove, None, "delete")
        m = mb.addMenu("&View")
        self.page_act = self._act(m, "Page layout", lambda: self.set_page_mode(not self.doc.page.get("show")),
                                  QKeySequence("Ctrl+L"), "page")
        self.page_act.setCheckable(True)
        m.addSeparator()
        self._act(m, "Zoom in", lambda: self.canvas.zoom_map(1.0), QKeySequence("Ctrl++"))
        self._act(m, "Zoom out", lambda: self.canvas.zoom_map(-1.0), QKeySequence("Ctrl+-"))
        self._act(m, "Fit page in window", self.canvas.fit_page, QKeySequence("Ctrl+0"))
        self._act(m, "Zoom to selection", self.zoom_to_selection, QKeySequence("Ctrl+Shift+0"), "zoomto")
        self._act(m, "Whole of Sweden", lambda: self.canvas.set_view(*Document().view))
        self._act(m, "Go to coordinates…", self.goto, QKeySequence("Ctrl+G"))
        m.addSeparator()
        self._act(m, "Attribute table", self.toggle_table, QKeySequence("Ctrl+T"), "table")
        m = mb.addMenu("&Page")
        m.addAction(self.page_act)
        self._act(m, "Insert picture or logo…", self.insert_image, QKeySequence("Ctrl+Shift+P"), "image")
        m.addSeparator()
        self._act(m, "Map series (a page per area)…", self.map_series, None, "series")
        # Layer
        m = mb.addMenu("&Layer")
        m.addMenu(self.add_layer_menu(m))
        self._act(m, "New layer for labels and symbols", self.new_annotation_layer, QKeySequence("Ctrl+Shift+N"),
                  "k_annotations")
        m.addSeparator()
        self._act(m, "Rename layer", self.rename_selected, QKeySequence("F2"))
        self._act(m, "Show / hide layer", lambda: self._with_layer(self.toggle_visible), QKeySequence("Ctrl+,"))
        self._act(m, "Lock / unlock layer", lambda: self._with_layer(self.toggle_locked), QKeySequence("Ctrl+/"))
        m.addSeparator()
        self._act(m, "Move up", lambda: self.move_selected(+1), QKeySequence("Ctrl+]"), "up")
        self._act(m, "Move down", lambda: self.move_selected(-1), QKeySequence("Ctrl+["), "down")
        self._act(m, "Bring to top", lambda: self.move_selected(+999), QKeySequence("Ctrl+Shift+]"))
        self._act(m, "Send to bottom", lambda: self.move_selected(-999), QKeySequence("Ctrl+Shift+["))
        m.addSeparator()
        self._act(m, "Delete layer", self.delete_selected_layer_or_item, None, "delete")
        # Tools
        m = mb.addMenu("&Tools")
        for key, name, sc in TOOLS:
            m.addAction(self.tool_actions[key])
        # Help
        m = mb.addMenu("&Help")
        self._act(m, "Keyboard shortcuts", self.show_shortcuts, QKeySequence("F1"))
        self._act(m, "Open the data folder (logs, map tile cache)",
                  lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(data_dir()))))
        self._act(m, "About MapMaker", self.about)

    def _select_all(self):
        L = self.selbar.current_layer()
        t = self.doc.table(L) if L else None
        if t is not None:
            import numpy as np
            self.canvas.set_feature_selection(L["id"], np.ones(t.n, dtype=bool))

    def _act(self, menu, text, fn, shortcut=None, icon_name=None):
        a = QAction(text, self)
        if icon_name:
            a.setIcon(icon(icon_name))
        if shortcut is not None:
            a.setShortcut(shortcut)
        a.triggered.connect(lambda _=False: fn())
        menu.addAction(a)
        return a

    def _build_status(self):
        sb = self.statusBar()
        self.hint_lab = QLabel()
        self.hint_lab.setObjectName("hint")
        self.hint_lab.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.coord_lab = QLabel()
        self.scale_lab = QLabel()
        for w in (self.coord_lab, self.scale_lab):
            w.setStyleSheet(f"color:{T.muted}; padding: 0 8px;")
        sb.addWidget(self.hint_lab, 1)
        sb.addPermanentWidget(self.coord_lab)
        sb.addPermanentWidget(self.scale_lab)

    def status(self, text: str, ms: int = 5000):
        self.statusBar().showMessage(text, ms)

    def add_layer_menu(self, parent) -> QMenu:
        m = QMenu("Add layer", parent)
        m.setIcon(icon("add"))
        bm = m.addMenu(icon("k_basemap"), "Background map")
        for b in BASEMAPS:
            a = bm.addAction(b.name, lambda s=b.id: self.add_background(basemap_layer(s)))
            if b.note:
                a.setToolTip(b.note)
        bm.setToolTipsVisible(True)
        tm = m.addMenu(icon("k_terrain"), "Terrain")
        for k, v in TERRAIN_STYLES.items():
            tm.addAction(v, lambda s=k: self.add_background(terrain_layer(s)))
        m.addAction(icon("k_color"), "Background colour", lambda: self.add_background(color_layer()))
        gm = m.addMenu(icon("k_grid"), "Coordinate grid")
        gm.addAction("SWEREF 99 TM (metres)", lambda: self._add(grid_layer("sweref"), "Add grid"))
        gm.addAction("Latitude / longitude", lambda: self._add(grid_layer("wgs84"), "Add grid"))
        m.addAction(icon("k_service"), "Map service (WMS or tiles)…", self.add_service)
        m.addSeparator()
        m.addAction(icon("k_data"), "Import data file…", self.import_file)
        m.addAction(icon("k_data"), "Download from an ArcGIS service…", self.download_arcgis)
        m.addSeparator()
        m.addAction(icon("k_annotations"), "Layer for labels and symbols", self.new_annotation_layer)
        return m

    # ================================================================ state changes
    def edit(self, label, fn, merge=None, from_props=False):
        self.doc.checkpoint(label, merge)
        fn()
        self.after_change(label, rebuild_props=not from_props)

    def after_change(self, label: str = "", rebuild_props: bool = True):
        self._validate_selection()
        self.layers.refresh()
        self.props.refresh(force=rebuild_props)
        self.selbar.refresh()
        if self.attr.isVisible():
            self.attr.refresh()
        if self.tabs.currentIndex() == 1:
            self.design.refresh(force=rebuild_props)
        self.canvas.update()
        self._update_title()
        self._update_undo()

    def _validate_selection(self):
        s = self.canvas.sel
        if s["item"] and self.doc.find_item(s["item"])[1] is None:
            s["item"] = None
        if s["layer"] and self.doc.layer(s["layer"]) is None:
            s["layer"] = None
        if s["item"]:
            L, _it = self.doc.find_item(s["item"])
            s["layer"] = L["id"]

    def _selection_changed(self):
        self.layers.refresh()
        self.props.refresh()
        self.selbar.refresh()

    def _update_title(self):
        name = self.doc.path.stem if self.doc.path else "Untitled map"
        self.setWindowTitle(f"{name}{' •' if self.doc.dirty else ''} – MapMaker")

    def _update_undo(self):
        self.undo_act.setEnabled(self.doc.can_undo())
        self.redo_act.setEnabled(self.doc.can_redo())
        self.undo_act.setText("Undo " + self.doc.undo_label() if self.doc.can_undo() else "Undo")
        self.redo_act.setText("Redo " + self.doc.redo_label() if self.doc.can_redo() else "Redo")

    def _view_changed(self):
        page = self.canvas.page_mode()
        self.map_btn.setChecked(not page)
        self.page_btn.setChecked(page)
        self.page_act.setChecked(page)
        self.fit_btn.setVisible(page)
        if page:
            self.zoom_lab.setText(f"{self.canvas.zoom_percent()} %")
        else:
            self.zoom_lab.setText("")
        sc = self.canvas.map_scale()
        self.scale_lab.setText(f"Scale 1 : {_round_scale(sc)}" + ("  (on paper)" if page else "  (at 96 dpi)"))

    def _mouse_moved(self, lon, lat):
        if math.isnan(lon):
            self.coord_lab.setText("")
            QToolTip.hideText()
            return
        txt = f"{lat:.5f}° N  {lon:.5f}° E"
        if 10 < lon < 25 and 55 < lat < 70:
            try:
                import numpy as np
                from ..geo import get_from_wgs84
                e, n = get_from_wgs84(3006)(np.array([[lon, lat]]))[0]
                txt += f"   ·   SWEREF 99 TM  N {n:,.0f}  E {e:,.0f}".replace(",", " ")
            except Exception:
                pass
        self.coord_lab.setText(txt)
        if self.canvas.tool == "select" and self.canvas._drag is None:
            self._hover_timer.start(450)

    def _hover_feature(self):
        from PySide6.QtGui import QCursor
        pos = self.canvas.mapFromGlobal(QCursor.pos())
        try:
            hit = self.canvas.feature_at(pos.toPointF())
        except Exception:
            hit = None
        if not hit:
            QToolTip.hideText()
            return
        L, row = hit
        rows = [f"<b>{_esc(L['name'])}</b>"]
        for k, v in list(row.items())[:12]:
            if v not in (None, ""):
                rows.append(f"{_esc(str(k))}: {_esc(str(v))[:80]}")
        QToolTip.showText(QCursor.pos(), "<br>".join(rows), self.canvas)

    # ================================================================ tools and view
    def set_tool(self, tool):
        self.canvas.set_tool(tool)
        self._tool_changed(tool)

    def _tool_changed(self, tool):
        a = self.tool_actions.get(tool)
        if a and not a.isChecked():
            a.setChecked(True)
        self._show_options(tool)
        self.hint_lab.setText(TOOL_HINTS.get(tool, ""))
        self._view_changed()
        self.canvas.setFocus()

    def set_page_mode(self, on: bool):
        if bool(self.doc.page.get("show")) != bool(on):
            self.canvas.set_page_mode(on)
        self._view_changed()
        self.props.refresh(force=True)

    def goto(self):
        from .dialogs import GotoDialog
        d = GotoDialog(self)
        if d.exec() and d.result_lonlat:
            lon, lat = d.result_lonlat
            self.canvas.center_on(lon, lat, max(self.doc.view[2], 13))
            self.status(f"Centred on {lat:.5f}, {lon:.5f}")

    def zoom_to_selection(self):
        s = self.canvas.sel
        bb = None
        if s["item"]:
            _L, it = self.doc.find_item(s["item"])
            if it is not None:
                bb = item_bbox(it)
        elif s["layer"]:
            L = self.doc.layer(s["layer"])
            if L is not None and L["kind"] == "data":
                t = self.doc.table(L)
                bb = t.bbox() if t is not None else None
            elif L is not None and L["kind"] == "annotations" and L.get("items"):
                bbs = [item_bbox(i) for i in L["items"]]
                bb = (min(b[0] for b in bbs), min(b[1] for b in bbs), max(b[2] for b in bbs), max(b[3] for b in bbs))
        if bb is None:
            self.status("Select a data layer, an annotation layer or an item to zoom to it.")
            return
        w, s_, e, n = bb
        if e - w < 1e-6 and n - s_ < 1e-6:
            self.canvas.center_on(w, s_, max(self.doc.view[2], 14))
        else:
            self.canvas.zoom_to_bbox(w, s_, e, n)

    # ================================================================ layers
    def _sel_layer(self):
        return self.doc.layer(self.canvas.sel.get("layer"))

    def _with_layer(self, fn):
        L = self._sel_layer()
        if L is not None:
            fn(L["id"])

    def _add(self, L, label, background=False):
        self.doc.checkpoint(label)
        L["name"] = self.doc.unique_name(L["name"])
        sel = self._sel_layer()
        if background and (L["kind"] == "color" or sel is None
                           or sel["kind"] not in ("color", "basemap", "terrain", "service")):
            self.doc.add_background(L)
        else:
            self.doc.add_layer(L, above=sel["id"] if sel else None)
        self.canvas.select(L["id"], None, None)
        self.after_change(label)
        return L

    def add_background(self, L):
        self._add(L, "Add " + L["name"], background=True)

    def new_annotation_layer(self):
        self._add(annotation_layer(self.doc.unique_name("Labels & symbols")), "New layer")

    def toggle_table(self):
        if self.attr.isVisible():
            self.attr.hide()
        else:
            self.attr.show()
            self.attr.refresh()
            self.attr.reload()

    def insert_image(self, replace: str | None = None):
        folder = self.settings.value("image_dir") or str(Path.home())
        path, _ = QFileDialog.getOpenFileName(self, "Insert a picture or logo", folder,
                                              "Pictures (*.png *.jpg *.jpeg *.svg *.gif *.bmp *.webp)")
        if not path:
            return
        self.settings.setValue("image_dir", str(Path(path).parent))
        data = Path(path).read_bytes()
        from .painter import picture
        bid = self.doc.add_image(data)
        if picture(self.doc, bid) is None:
            QMessageBox.warning(self, "Insert picture", "MapMaker could not read that picture.")
            return
        from ..document import paper_mm
        W, H = paper_mm(self.doc.page)
        m = float(self.doc.page.get("margin_mm", 12))
        if replace:
            def apply():
                im = next((i for i in self.doc.page.get("images") or [] if i["id"] == replace), None)
                if im is not None:
                    im["img"], im["name"] = bid, Path(path).name
            self.edit("Replace picture", apply)
            return
        im = {"id": new_id(), "img": bid, "name": Path(path).name, "w": 30.0, "x": W - m - 32, "y": m + 2,
              "opacity": 1.0}
        self.edit("Insert picture", lambda: self.doc.page.setdefault("images", []).append(im))
        if not self.doc.page.get("show"):
            self.set_page_mode(True)
        self.canvas.select(self.canvas.sel.get("layer"), None, "image:" + im["id"])
        self.status("Drag the picture to place it; drag its bottom-right corner to resize it.", 8000)

    def map_series(self):
        from .series import run_series
        self.canvas.finish_editing(True)
        run_series(self)

    def add_service(self):
        from .dialogs import ServiceDialog
        d = ServiceDialog(self)
        if d.exec():
            self._add(service_layer(d.source()), "Add map service", background=True)

    def toggle_visible(self, lid):
        L = self.doc.layer(lid)
        if L is not None:
            self.edit("Show layer" if not L.get("visible", True) else "Hide layer",
                      lambda: L.__setitem__("visible", not L.get("visible", True)))

    def toggle_locked(self, lid):
        L = self.doc.layer(lid)
        if L is not None:
            self.edit("Unlock layer" if L.get("locked") else "Lock layer",
                      lambda: L.__setitem__("locked", not L.get("locked")))

    def toggle_item_hidden(self, iid):
        _L, it = self.doc.find_item(iid)
        if it is not None:
            self.edit("Show" if it.get("hidden") else "Hide", lambda: it.__setitem__("hidden", not it.get("hidden")))

    def rename_layer(self, lid, name):
        name = name.strip()
        L = self.doc.layer(lid)
        if L is None or not name or name == L["name"]:
            self.layers.refresh()
            return
        self.edit("Rename layer", lambda: L.__setitem__("name", name))

    def rename_selected(self):
        L = self._sel_layer()
        if L is None:
            return
        name, ok = QInputDialog.getText(self, "Rename layer", "Name:", text=L["name"])
        if ok:
            self.rename_layer(L["id"], name)

    def set_layer_value(self, lid, key, value, label, merge=None):
        L = self.doc.layer(lid)
        if L is not None and L.get(key) != value:
            self.edit(label, lambda: L.__setitem__(key, value), merge=merge)

    def move_selected(self, step):
        s = self.canvas.sel
        if s["item"]:
            L, it = self.doc.find_item(s["item"])
            i = L["items"].index(it)
            j = max(0, min(len(L["items"]) - 1, i + (1 if step > 0 else -1) * min(abs(step), len(L["items"]))))
            if i != j:
                self.edit("Move item", lambda: self.doc.move_item_to(it["id"], L["id"], j))
            return
        L = self._sel_layer()
        if L is None:
            return
        i = self.doc.index(L["id"])
        j = max(0, min(len(self.doc.layers) - 1, i + step))
        if i != j:
            self.edit("Move layer", lambda: self.doc.move_layer(L["id"], j))

    def move_layer_to(self, lid, target, above):
        if lid == target:
            return

        def apply():
            L = self.doc.layer(lid)
            self.doc.remove_layer(lid)
            t = self.doc.index(target)
            self.doc.layers.insert(t + 1 if above else t, L)
        self.edit("Move layer", apply)

    def move_item_to(self, iid, lid, target_iid, above):
        def apply():
            L = self.doc.layer(lid)
            if L is None or L["kind"] != "annotations":
                return
            src, it = self.doc.find_item(iid)
            if it is None:
                return
            src["items"] = [x for x in src["items"] if x["id"] != iid]
            if not target_iid:
                L["items"].append(it)
            else:
                t = next((k for k, x in enumerate(L["items"]) if x["id"] == target_iid), len(L["items"]) - 1)
                L["items"].insert(t + 1 if above else t, it)
        self.edit("Move item", apply)
        self.canvas.select(lid, iid)

    def duplicate_selected(self):
        s = self.canvas.sel
        if s["item"]:
            L, it = self.doc.find_item(s["item"])
            c = copy.deepcopy(it)
            c["id"] = new_id()
            f = self.canvas.frame()
            from .canvas import _move_merc
            _move_merc(c, 12 / f.world, 12 / f.world)
            self.edit("Duplicate", lambda: L["items"].insert(L["items"].index(it) + 1, c))
            self.canvas.select(L["id"], c["id"])
            return
        L = self._sel_layer()
        if L is None:
            return
        self.doc.checkpoint("Duplicate layer")
        c = self.doc.duplicate_layer(L["id"])
        self.canvas.select(c["id"], None, None)
        self.after_change("Duplicate layer")

    def delete_selected_layer_or_item(self):
        s = self.canvas.sel
        if s["item"]:
            self.canvas.delete_selection()
            return
        if s["element"]:
            self.canvas.delete_selection()
            return
        L = self._sel_layer()
        if L is None:
            return
        t = self.doc.table(L)
        n = len(L.get("items", [])) or (t.n if t is not None else 0)
        if n > 20:
            what = "items" if L["kind"] == "annotations" else "features"
            if QMessageBox.question(self, "Delete layer",
                                    f"Delete “{L['name']}” with its {n:,} {what}? (Ctrl+Z brings it back.)"
                                    .replace(",", " ")) != QMessageBox.StandardButton.Yes:
                return
        i = self.doc.index(L["id"])
        self.edit("Delete layer", lambda: self.doc.remove_layer(L["id"]))
        nxt = self.doc.layers[min(i, len(self.doc.layers) - 1)]["id"] if self.doc.layers else None
        self.canvas.select(nxt, None, None)

    # ================================================================ data
    def import_file(self, path: str | None = None, layer: str | None = None):
        from PySide6.QtWidgets import QProgressDialog

        from ..sources import FILE_FILTER, file_layers, read_file
        from ..table import Table, read_gpkg
        if not path:
            folder = self.settings.value("import_dir") or str(Path.home())
            path, _ = QFileDialog.getOpenFileName(self, "Import a data file", folder, FILE_FILTER)
            if not path:
                return
            self.settings.setValue("import_dir", str(Path(path).parent))
        try:
            layers = file_layers(path)
        except Exception as e:
            QMessageBox.warning(self, "Import", f"Could not open the file:\n{e}")
            return
        if layer is None and len(layers) > 1:
            names = [f"{n}  ({g}{', ' + format(c, ',').replace(',', ' ') if c else ''})" for n, g, c in layers]
            choice, ok = QInputDialog.getItem(self, "Import", "The file holds several layers. Which one?", names, 0, False)
            if not ok:
                return
            layer = layers[names.index(choice)][0]
        prog = QProgressDialog(f"Reading {Path(path).name} …", "Cancel", 0, 0, self)
        prog.setWindowTitle("Importing")
        prog.setWindowModality(Qt.WindowModality.WindowModal)
        prog.setMinimumDuration(400)
        state = {"cancel": False, "done": 0, "total": 0}
        prog.canceled.connect(lambda: state.__setitem__("cancel", True))
        prog.setValue(0)
        timer = QTimer(self)

        def tick():
            if state["total"]:
                prog.setMaximum(state["total"])
                prog.setValue(min(state["done"], state["total"]))
                prog.setLabelText(f"Reading {Path(path).name} … {state['done']:,} of {state['total']:,}"
                                  .replace(",", " "))
        timer.timeout.connect(tick)
        timer.start(200)

        def work():
            if str(path).lower().endswith(".gpkg"):
                t, name = read_gpkg(path, layer, progress=lambda d, n: state.update(done=d, total=n),
                                    cancelled=lambda: state["cancel"])
            else:
                recs, fields, name = read_file(path, layer)
                t = Table.from_records(recs, fields, cancelled=lambda: state["cancel"])
            t.prepared()                 # index it now, on this thread, so the first drawing is quick
            return t, name

        def done(res):
            timer.stop()
            prog.close()
            t, name = res
            if not t.n:
                QMessageBox.information(self, "Import", "The file holds no features with coordinates MapMaker can read.")
                return
            self._add_data(name, t, {"type": "file", "path": str(path), "name": Path(path).name,
                                     **({"layer": layer} if layer else {})})
            self.status(f"Imported {t.n:,} features from {Path(path).name}".replace(",", " "))

        def failed(tb):
            timer.stop()
            prog.close()
            if "Cancelled" in tb:
                return
            QMessageBox.warning(self, "Import", "Could not read the file:\n" + tb.strip().splitlines()[-1])
        run_async(work, on_done=done, on_error=failed)

    def _add_data(self, name, t, source, color=None, zoom=True, style=None):
        L = data_layer(name, t.main_kind(), list(t.fields), source, color, table=self.doc.add_table(t))
        if style:
            L["style"] = copy.deepcopy(style)
        L["count"] = t.n
        if t.n > 20000 and not style:
            L["style"]["size"] = 4.0           # many features: smaller marks keep the map readable
            L["style"]["outline_width"] = 0.5
        self._add(L, "Add " + name)
        if zoom:
            bb = t.bbox()
            if bb:
                w, s, e, n = bb
                if e - w < 1e-6 and n - s < 1e-6:
                    self.canvas.center_on(w, s, max(self.doc.view[2], 13))
                else:
                    self.canvas.zoom_to_bbox(w, s, e, n)
        return L

    def layer_from_selection(self, L, mask, hint: str = ""):
        t = self.doc.table(L)
        sub = t.take(mask)
        name = f"{L['name']} – {hint}" if hint else f"{L['name']} – selection"
        from ..styles import next_color
        st = copy.deepcopy(L.get("style") or {})
        c = next_color()
        st.update(color=c, line_color=c, fill=c, stroke=c)
        NL = self._add_data(name[:80], sub, dict(L.get("source") or {}, subset=True), zoom=False, style=st)
        NL["label"] = copy.deepcopy(L.get("label") or NL["label"])
        self.status(f"New layer “{NL['name']}” with {sub.n:,} features".replace(",", " "), 6000)

    def remove_selected_features(self, L, mask):
        t = self.doc.table(L)
        n = int(mask.sum())
        if QMessageBox.question(self, "Remove features", f"Remove the {n:,} selected features from “{L['name']}”? "
                                "(Ctrl+Z brings them back.)".replace(",", " ")) != QMessageBox.StandardButton.Yes:
            return
        rest = t.take(~mask)
        self.edit("Remove features", lambda: L.__setitem__("table", self.doc.add_table(rest)))
        self.canvas.clear_feature_selection(L["id"])

    def download_arcgis(self):
        from PySide6.QtWidgets import QProgressDialog

        from ..sources import Cancelled, download
        from .dialogs import ArcGISDialog
        d = ArcGISDialog(self)
        if not d.exec():
            return
        c = d.choice()
        bbox = None
        if c["view_only"]:
            f = self.canvas.frame()
            w, n = f.to_lonlat(f.rect.left(), f.rect.top())
            e, s = f.to_lonlat(f.rect.right(), f.rect.bottom())
            bbox = (w, s, e, n)
        prog = QProgressDialog("Asking the service…", "Cancel", 0, 0, self)
        prog.setWindowTitle("Downloading")
        prog.setWindowModality(Qt.WindowModality.WindowModal)
        prog.setMinimumDuration(0)
        prog.show()
        state = {"cancel": False, "msg": "", "done": 0, "total": 0}
        prog.canceled.connect(lambda: state.__setitem__("cancel", True))

        def progress(done, total, msg):
            state.update(done=done, total=total, msg=msg)

        timer = QTimer(self)

        def tick():
            prog.setLabelText(state["msg"] or "Working…")
            if state["total"]:
                prog.setMaximum(state["total"])
                prog.setValue(state["done"])
        timer.timeout.connect(tick)
        timer.start(200)

        def work():
            from ..table import Table
            recs, fields, name = download(c["url"], where=c["where"], bbox=bbox, progress=progress,
                                          cancelled=lambda: state["cancel"])
            t = Table.from_records(recs, fields)
            t.prepared()
            return t, name

        def done(res):
            timer.stop()
            prog.close()
            t, name = res
            if not t.n:
                QMessageBox.information(self, "Download", "The service returned no features here.")
                return
            self._add_data(c["name"] or name, t,
                           {"type": "arcgis", "url": c["url"], "where": c["where"], "name": c["name"] or name,
                            "attribution": c["attribution"]}, c["color"], zoom=not c["view_only"])
            self.status(f"Downloaded {t.n:,} features".replace(",", " "))

        def failed(tb):
            timer.stop()
            prog.close()
            if "Cancelled" in tb:
                return
            QMessageBox.warning(self, "Download", "The download failed:\n" + tb.strip().splitlines()[-1])
        _ = Cancelled
        run_async(work, on_done=done, on_error=failed)

    # ================================================================ undo
    def undo(self):
        if self.canvas._editor is not None:
            self.canvas.finish_editing(False)
            return
        label = self.doc.undo()
        if label is not None:
            self.status("Undid: " + label, 2500)
            self.after_change(label)

    def redo(self):
        label = self.doc.redo()
        if label is not None:
            self.status("Redid: " + label, 2500)
            self.after_change(label)

    # ================================================================ files
    def default_dir(self) -> Path:
        d = self.settings.value("map_dir")
        return Path(d) if d and Path(d).exists() else maps_dir()

    def maybe_save(self) -> bool:
        self.canvas.finish_editing(True)
        if not self.doc.dirty:
            return True
        name = self.doc.path.name if self.doc.path else "this map"
        r = QMessageBox.question(self, "Unsaved changes", f"Save the changes to {name}?",
                                 QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard
                                 | QMessageBox.StandardButton.Cancel, QMessageBox.StandardButton.Save)
        if r == QMessageBox.StandardButton.Cancel:
            return False
        if r == QMessageBox.StandardButton.Save:
            return self.save()
        return True

    def set_document(self, doc: Document):
        self.doc = doc
        self.canvas.set_document(doc)
        self.layers._expanded.clear()
        self.layers._collapsed.clear()
        self.after_change("", rebuild_props=True)
        self._view_changed()

    def new_map(self):
        if self.maybe_save():
            self.set_document(Document.new())

    def open_map(self, path: str | None = None):
        if not self.maybe_save():
            return
        if not path:
            path, _ = QFileDialog.getOpenFileName(self, "Open a map", str(self.default_dir()),
                                                  f"MapMaker maps (*{SUFFIX})")
            if not path:
                return
        try:
            doc = Document.load(path)
        except Exception as e:
            log.exception("Opening %s failed", path)
            QMessageBox.warning(self, "Open", f"Could not open the map:\n{e}")
            return
        push_recent(self.settings, doc.path)
        self.settings.setValue("map_dir", str(doc.path.parent))
        self.set_document(doc)
        self.status("Opened " + doc.path.name)

    def save(self) -> bool:
        if self.doc.path is None:
            return self.save_as()
        return self._write(self.doc.path)

    def save_as(self) -> bool:
        base = self.doc.path or (self.default_dir() / ((self.doc.page.get("title") or "map").strip() + SUFFIX))
        path, _ = QFileDialog.getSaveFileName(self, "Save the map", str(base), f"MapMaker maps (*{SUFFIX})")
        if not path:
            return False
        return self._write(Path(path))

    def _write(self, path: Path) -> bool:
        self.canvas.finish_editing(True)
        try:
            p = self.doc.save(path)
        except Exception as e:
            log.exception("Saving failed")
            QMessageBox.warning(self, "Save", f"Could not save the map:\n{e}")
            return False
        push_recent(self.settings, p)
        self.settings.setValue("map_dir", str(p.parent))
        self._update_title()
        self.status("Saved " + str(p))
        return True

    def _fill_recent(self):
        m = self.recent_menu
        m.clear()
        items = recent_list(self.settings)
        if not items:
            a = m.addAction("No recent maps")
            a.setEnabled(False)
            return
        for p in items:
            m.addAction(Path(p).name, lambda p=p: self.open_map(p)).setToolTip(p)
        m.setToolTipsVisible(True)

    def export(self):
        from .export import run_export
        self.canvas.finish_editing(True)
        run_export(self)

    # ================================================================ misc
    def show_shortcuts(self):
        from .dialogs import ShortcutsDialog
        ShortcutsDialog(self).exec()

    def about(self):
        QMessageBox.about(self, "About MapMaker",
                          f"<h3>MapMaker {__version__}</h3><p>Design maps from background maps, laser terrain, "
                          "map services and your own data, labels and symbols – then lay them out on a page and "
                          "export them as PDF or images.</p><p>Map design defaults follow Judith Tyner, "
                          "<i>Principles of Map Design</i> (2010).</p>"
                          f"<p style='color:{T.muted}'>Background maps © their providers, shown in the sources line. "
                          "Laser elevation © Lantmäteriet (CC0) via Mapterhorn.</p>")

    def _restore(self):
        g = self.settings.value("geometry")
        if g is not None:
            self.restoreGeometry(g)
        s = self.settings.value("split")
        if s is not None:
            self.split.restoreState(s)
        r = self.settings.value("right")
        if r is not None:
            self.right.restoreState(r)

    def closeEvent(self, ev):
        if not self.maybe_save():
            ev.ignore()
            return
        self.settings.setValue("geometry", self.saveGeometry())
        self.settings.setValue("split", self.split.saveState())
        self.settings.setValue("right", self.right.saveState())
        self.canvas.shutdown()
        self.tiles.shutdown()
        ev.accept()


def _round_scale(d: float) -> str:
    if not math.isfinite(d) or d <= 0:
        return "–"
    e = 10 ** max(0, int(math.floor(math.log10(d))) - 1)
    return f"{int(round(d / e) * e):,}".replace(",", " ")


def _esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


__all__ = ["MainWindow", "os"]
