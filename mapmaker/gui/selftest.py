"""Automatic self-test: builds a sample map, works the tools the way a person would, saves,
reopens and exports it, and writes screenshots and a report to data/selftest/."""
from __future__ import annotations

import json
import logging
import time
import traceback
from pathlib import Path

from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QMouseEvent, QPainter
from PySide6.QtWidgets import QApplication

from ..paths import data_dir

log = logging.getLogger("mapmaker")


class Report:
    def __init__(self, folder: Path):
        self.folder = folder
        self.lines: list[str] = []
        self.failed = 0

    def ok(self, name, detail=""):
        self.lines.append(f"PASS  {name}{('  – ' + detail) if detail else ''}")

    def fail(self, name, detail=""):
        self.failed += 1
        self.lines.append(f"FAIL  {name}{('  – ' + detail) if detail else ''}")

    def check(self, name, cond, detail=""):
        (self.ok if cond else self.fail)(name, detail)
        return cond

    def write(self):
        head = f"MapMaker self-test {time.strftime('%Y-%m-%d %H:%M')}: " + (
            "ALL PASSED" if not self.failed else f"{self.failed} FAILED")
        (self.folder / "result.txt").write_text(head + "\n\n" + "\n".join(self.lines) + "\n", encoding="utf-8")


def _wait(app, cond=None, timeout=20.0, at_least=0.0):
    t0 = time.time()
    while True:
        app.processEvents()
        el = time.time() - t0
        if el >= at_least and (cond is None or cond()):
            return True
        if el > timeout:
            return False
        time.sleep(0.03)


def _mouse(w, kind, pos, button=Qt.MouseButton.LeftButton, buttons=None):
    types = {"press": QEvent.Type.MouseButtonPress, "release": QEvent.Type.MouseButtonRelease,
             "move": QEvent.Type.MouseMove, "double": QEvent.Type.MouseButtonDblClick}
    if buttons is None:
        buttons = Qt.MouseButton.NoButton if kind in ("release",) else button
    if kind == "move" and buttons is None:
        buttons = Qt.MouseButton.NoButton
    p = QPointF(pos)
    ev = QMouseEvent(types[kind], p, w.mapToGlobal(p), button if kind != "move" else Qt.MouseButton.NoButton,
                     buttons, Qt.KeyboardModifier.NoModifier)
    QApplication.sendEvent(w, ev)


def _click(app, w, pos):
    _mouse(w, "press", pos)
    _mouse(w, "release", pos)
    app.processEvents()


def _drag(app, w, a, b, steps=8):
    _mouse(w, "press", a)
    for i in range(1, steps + 1):
        q = QPointF(a.x() + (b.x() - a.x()) * i / steps, a.y() + (b.y() - a.y()) * i / steps)
        _mouse(w, "move", q, buttons=Qt.MouseButton.LeftButton)
        app.processEvents()
    _mouse(w, "release", b)
    app.processEvents()


def _settled(win):
    return win.tiles.busy() == 0 and win.canvas.timgs.pending() == 0


def run_selftest(app) -> int:
    folder = data_dir() / "selftest"
    folder.mkdir(parents=True, exist_ok=True)
    for f in folder.glob("*"):
        try:
            f.unlink()
        except OSError:
            pass
    rep = Report(folder)
    try:
        _run(app, rep, folder)
    except Exception:
        rep.fail("self-test crashed", traceback.format_exc())
    rep.write()
    print((folder / "result.txt").read_text(encoding="utf-8"))
    return 0 if not rep.failed else 1


def _shot(app, win, folder, name, wait=True):
    if wait:
        _wait(app, lambda: _settled(win), timeout=25, at_least=0.4)
    _wait(app, timeout=0.3, at_least=0.3)
    win.grab().save(str(folder / f"{name}.png"))


def _run(app, rep: Report, folder: Path):
    from ..document import (Document, area_item, data_layer, line_item, symbol_item, terrain_layer, text_item)
    from ..render import lonlat_to_merc
    from .main_window import MainWindow

    t0 = time.time()
    win = MainWindow()
    win.resize(1500, 940)
    win.show()
    _wait(app, timeout=1, at_least=0.5)
    rep.ok("window opened", f"{time.time() - t0:.1f} s")
    c = win.canvas
    doc = win.doc

    # ------------------------------------------------------------ a sample map of Uppsala
    mx, my = lonlat_to_merc(17.64, 59.857)
    doc.view = [float(mx), float(my), 12.6]
    doc.page["title"] = "Uppsala – a test map"
    doc.page["subtitle"] = "MapMaker self-test"
    win.edit("Terrain", lambda: doc.add_background(terrain_layer("hillshade")))
    ann = doc.annotation_layers()[0]
    ann["items"] += [
        text_item(17.6389, 59.8586, "Uppsala", "place_major"),
        text_item(17.648, 59.852, "Fyrisån", "water"),
        text_item(17.60, 59.875, "Luthagen", "region"),
        symbol_item(17.6334, 59.8581, "church", "#8c2d19", 22, True),
        symbol_item(17.6290, 59.8506, "castle", "#6a3d9a", 24, False),
        line_item([[17.60, 59.84], [17.62, 59.845], [17.64, 59.842], [17.665, 59.85]], "#d95f02", 3.0, "dash"),
        area_item([[17.66, 59.865], [17.69, 59.866], [17.695, 59.88], [17.665, 59.882]], "#33a02c"),
    ]
    ann["items"][3]["label"] = "Domkyrkan"
    ann["items"][4]["label"] = "Slottet"
    ann["items"][5]["style"]["arrow"] = True
    ann["items"][5]["legend"] = True
    ann["items"][5]["legend_text"] = "Walking route"
    ann["items"][6]["legend"] = True
    ann["items"][6]["legend_text"] = "Study area"
    feats = []
    import random
    rnd = random.Random(4)
    for i in range(40):
        lo, la = 17.58 + rnd.random() * 0.14, 59.83 + rnd.random() * 0.06
        feats.append({"g": ["Point", [lo, la]], "p": {"name": f"Site {i + 1}", "type": rnd.choice(["mound", "stone"])}})
    feats.append({"g": ["Polygon", [[[17.59, 59.83], [17.61, 59.83], [17.61, 59.84], [17.59, 59.84], [17.59, 59.83]]]],
                  "p": {"name": "Field"}})
    feats.append({"g": ["LineString", [[17.58, 59.86], [17.6, 59.87], [17.62, 59.872]]], "p": {"name": "Old road"}})
    from ..table import Table, TableBuilder
    import numpy as np
    L = data_layer("Recorded sites", "point", ["name", "type"], {"type": "selftest"}, "#7570b3",
                   table=doc.add_table(Table.from_features(feats)))
    L["label"]["field"] = "name"
    doc.add_layer(L, above=doc.layers[1]["id"])
    # a big layer to exercise the cached drawing
    nb = 300000
    t0b = time.time()
    b = TableBuilder(["n", "kind"])
    gen = np.random.default_rng(3)
    xs, ys = 11.0 + gen.random(nb) * 12.0, 55.5 + gen.random(nb) * 13.0
    kinds = ["Grav", "Gravfält", "Fornborg", "Boplats", "Fångstgrop", "Kolningsanläggning"]
    kk = gen.integers(0, len(kinds), nb)
    for i in range(nb):
        b.add_point(xs[i], ys[i], (i, kinds[kk[i]]))
    big = b.finish()
    big.prepared()
    rep.ok("build a 300 000-point table", f"{time.time() - t0b:.1f} s")
    LB = data_layer("Many points", "point", ["n", "kind"], {"type": "selftest"}, "#e7298a", table=doc.add_table(big))
    LB["style"]["size"] = 4.0
    LB["visible"] = False
    doc.add_layer(LB, above=L["id"])
    doc.touch()
    win.after_change("sample")
    c.set_page_mode(True)
    win._view_changed()
    _shot(app, win, folder, "01_page")
    rep.check("page drawn", bool(c._page_rects.get("legend")) and bool(c._page_rects.get("scalebar")),
              ", ".join(sorted(c._page_rects)))
    rep.check("tiles arrived", _settled(win), f"tile errors: {dict(win.tiles.errors) or 'none'}")
    hits = len(c._hits)
    rep.check("annotations hit-testable", hits >= 7, f"{hits} shapes")

    # ------------------------------------------------------------ tools
    f = c.frame()
    centre = f.rect.center()
    n0 = len(ann["items"])
    win.set_tool("symbol")
    _click(app, c, centre + QPointF(-120, 60))
    rep.check("symbol tool places a symbol", len(ann["items"]) == n0 + 1)
    win.set_tool("text")
    _click(app, c, centre + QPointF(140, -80))
    _wait(app, timeout=0.3, at_least=0.2)
    ed = c._editor
    if rep.check("text tool opens an editor", ed is not None):
        ed.setPlainText("Test label")
        ed.finish(True)
        app.processEvents()
    rep.check("text added", any(i.get("text") == "Test label" for i in ann["items"]))
    win.set_tool("line")
    for dx, dy in ((-200, 120), (-120, 160), (-40, 120)):
        _click(app, c, centre + QPointF(dx, dy))
    c._finish_draft(True)
    rep.check("line tool draws a line", ann["items"][-1]["type"] == "line" and len(ann["items"][-1]["coords"]) == 3)
    win.set_tool("area")
    for dx, dy in ((60, 100), (160, 100), (160, 180), (60, 190)):
        _click(app, c, centre + QPointF(dx, dy))
    c._finish_draft(True)
    rep.check("area tool draws an area", ann["items"][-1]["type"] == "area" and len(ann["items"][-1]["coords"]) == 4)
    _wait(app, timeout=0.3, at_least=0.2)
    # select and move
    win.set_tool("select")
    c.update()
    _wait(app, timeout=0.3, at_least=0.2)
    sym = ann["items"][3]
    p0 = c.frame().pt(sym["lon"], sym["lat"])
    before = (sym["lon"], sym["lat"])
    _drag(app, c, p0, p0 + QPointF(40, 25))
    moved = ann["items"][3]["lon"] != before[0]
    rep.check("drag moves a symbol", moved, f"selected {c.sel.get('item') == sym['id']}")
    win.undo()
    rep.check("undo puts it back", abs(doc.find_item(sym["id"])[1]["lon"] - before[0]) < 1e-9)
    win.redo()
    win.undo()
    # move the legend
    lr = c._page_rects.get("legend")
    if lr is not None:
        old = list(doc.page["legend"]["pos"])
        _drag(app, c, lr.center(), lr.center() - QPointF(300, 0))
        rep.check("legend can be dragged", doc.page["legend"]["pos"] != old, f"{old} -> {doc.page['legend']['pos']}")
        win.undo()
    # select a layer from the panel, change opacity
    c.select(L["id"], None, None)
    app.processEvents()
    win.layers.opacity.slider.setValue(60)
    rep.check("opacity from the Layers panel", abs(doc.layer(L["id"])["opacity"] - 0.6) < 1e-6)
    _shot(app, win, folder, "02_layer_selected", wait=False)
    # hide / delete
    win.toggle_visible(L["id"])
    rep.check("hide layer", doc.layer(L["id"])["visible"] is False)
    win.toggle_visible(L["id"])
    nl = len(doc.layers)
    c.select(LB["id"], None, None)
    win.delete_selected_layer_or_item()
    rep.check("delete layer", len(doc.layers) == nl - 1)
    win.undo()
    rep.check("undo delete", len(doc.layers) == nl)
    # big layer: tiles on worker threads, smooth zoom
    win.toggle_visible(LB["id"])
    saved_view = list(doc.view)
    c.set_view(*Document().view)
    t1 = time.time()
    ok = _wait(app, lambda: _settled(win), timeout=40, at_least=0.3)
    rep.check("300 000 points drawn (whole Sweden)", ok, f"{time.time() - t1:.1f} s until every tile was ready")
    _shot(app, win, folder, "02b_300k_points", wait=False)
    times = []
    for steps in (0.6, 0.6, 0.6, -0.6, -0.6, -0.6):
        c.zoom_map(steps)
        t2 = time.time()
        while c._anim.isActive():
            t3 = time.time()
            c.repaint()
            times.append((time.time() - t3) * 1000)
            app.processEvents()
        _wait(app, timeout=0.2, at_least=0.1)
    if times:
        rep.check("zooming stays smooth with 300 000 points", sorted(times)[len(times) // 2] < 60,
                  f"median frame {sorted(times)[len(times) // 2]:.0f} ms, slowest {max(times):.0f} ms, {len(times)} frames")
    # selection by attributes
    win.selbar.refresh()
    i = win.selbar.layer.findData(LB["id"])
    win.selbar.layer.setCurrentIndex(i)
    win.selbar.field.setCurrentIndex(win.selbar.field.findData("kind"))
    win.selbar.op.setCurrentIndex(win.selbar.op.findData("in"))
    win.selbar.value.setEditText("grav, gravfält")
    t2 = time.time()
    win.selbar.apply()
    m = c.fsel.get(LB["id"])
    expect = int(np.isin(kk, [0, 1]).sum())
    rep.check("select by attributes", m is not None and int(m.sum()) == expect,
              f"{int(m.sum()) if m is not None else 0} of {expect} in {(time.time() - t2) * 1000:.0f} ms")
    _wait(app, lambda: _settled(win), timeout=30, at_least=0.3)
    _shot(app, win, folder, "02c_selection", wait=False)
    win.selbar.method.setCurrentIndex(win.selbar.method.findData("within"))
    win.selbar.field.setCurrentIndex(win.selbar.field.findData("n"))
    win.selbar.op.setCurrentIndex(win.selbar.op.findData("lt"))
    win.selbar.value.setEditText("1000")
    win.selbar.apply()
    m = c.fsel.get(LB["id"])
    rep.check("select within the selection", m is not None and int(m.sum()) == int(np.isin(kk[:1000], [0, 1]).sum()))
    win.selbar.method.setCurrentIndex(0)
    nlay = len(doc.layers)
    win.layer_from_selection(LB, m, "test")
    rep.check("new layer from selection", len(doc.layers) == nlay + 1 and doc.table(doc.layers[-1]) is not None
              and doc.table(doc.layers[-1]).n == int(m.sum()))
    win.undo()
    win.selbar.invert()
    rep.check("invert selection", int(c.fsel[LB["id"]].sum()) == nb - int(m.sum()))
    win.canvas.clear_feature_selection()
    # Select-features tool: a rectangle in the middle of the map
    win.set_tool("marquee")
    cf = c.frame()
    ctr = cf.rect.center()
    c.fsel_layer = LB["id"]
    _drag(app, c, ctr - QPointF(60, 40), ctr + QPointF(60, 40))
    m = c.fsel.get(LB["id"])
    rep.check("rectangle selection", m is not None and m.sum() > 0, f"{int(m.sum()) if m is not None else 0} selected")
    c.clear_feature_selection()
    win.set_tool("select")
    win.toggle_visible(LB["id"])
    c.set_view(*saved_view)
    # select an item to show its properties
    c.select(ann["id"], ann["items"][1]["id"])
    app.processEvents()
    _shot(app, win, folder, "03_text_properties", wait=False)
    c.select(ann["id"], ann["items"][3]["id"])
    app.processEvents()
    _shot(app, win, folder, "04_symbol_properties", wait=False)
    c.select(None, None, "legend")
    app.processEvents()
    _shot(app, win, folder, "05_legend_selected", wait=False)

    # ------------------------------------------------------------ map view
    win.set_page_mode(False)
    c.select(L["id"], None, None)
    _shot(app, win, folder, "06_map_view")
    win.set_page_mode(True)

    # ------------------------------------------------------------ save / open
    path = folder / "test.mapmaker"
    rep.check("save", win._write(path))
    d2 = Document.load(path)
    same = json.dumps(d2.layers, sort_keys=True) == json.dumps(doc.layers, sort_keys=True)
    rep.check("reopen gives the same layers", same)
    t2_ = d2.table(d2.layer(L["id"]))
    rep.check("reopen keeps imported features", t2_ is not None and t2_.n == len(feats) and
              t2_.row(0) == doc.table(L).row(0))
    rep.check("reopen keeps the big layer", d2.table(d2.layer(LB["id"])).n == nb)

    # ------------------------------------------------------------ exports
    from . import export as E
    try:
        _geo, fr = E.page_frame(doc, 150 / 25.4)
        E.prefetch(doc, win.tiles, fr)
        img = E.page_image(doc, win.tiles, 150)
        img.save(str(folder / "07_export_page_150dpi.png"))
        rep.check("export page as PNG", img.width() > 1500, f"{img.width()} x {img.height()}")
        E.page_pdf(doc, win.tiles, str(folder / "08_export.pdf"), 300)
        size = (folder / "08_export.pdf").stat().st_size
        rep.check("export page as PDF", size > 20000, f"{size // 1024} kB")
        v = E.view_image(doc, win.tiles, 900, 600, 2)
        v.save(str(folder / "09_export_view.png"))
        rep.check("export map view", v.width() == 1800)
    except Exception:
        rep.fail("exports", traceback.format_exc())

    # ------------------------------------------------------------ design: themes and legend styles
    from .design_panel import apply_theme
    win.tabs.setCurrentIndex(1)
    win.edit("Theme", lambda: apply_theme(doc, "dark"))
    _shot(app, win, folder, "12_theme_dark")
    win.edit("Theme", lambda: apply_theme(doc, "parchment"))
    doc.page["design"]["legend_columns"] = 2
    doc.touch()
    _shot(app, win, folder, "13_theme_parchment")
    win.edit("Theme", lambda: apply_theme(doc, "light"))
    doc.page["design"]["legend_style"] = "strip"
    doc.page["design"]["title_position"] = "inside"
    doc.page["design"]["scale_ratio"] = True
    doc.page["design"]["north_style"] = "star"
    doc.touch()
    _shot(app, win, folder, "14_strip_legend_inside_title")
    doc.page["design"] = {}
    doc.touch()
    win.tabs.setCurrentIndex(0)

    # ------------------------------------------------------------ thematic styles, heat map, clusters
    from ..document import grid_layer
    from ..table import class_index, make_categories
    win.toggle_visible(LB["id"])
    c.set_view(*Document().view)
    cls = make_categories(big, "kind", "Set2")
    win.edit("Colour by", lambda: LB["style"].__setitem__("classify", cls))
    ci = class_index(big, cls)
    rep.check("colour by category", ci is not None and int((ci >= 0).sum()) == nb, f"{len(cls['classes'])} classes")
    c.select(LB["id"], None, None)
    _wait(app, lambda: _settled(win), timeout=40, at_least=0.3)
    _shot(app, win, folder, "15_colour_by_category", wait=False)
    rep.check("legend lists the classes", sum(1 for e in __import__("mapmaker.gui.painter", fromlist=["x"])
                                              .legend_entries(doc) if e["kind"] == "point") >= len(cls["classes"]))
    win.edit("Heat map", lambda: LB["style"].__setitem__("render", "heatmap"))
    _wait(app, lambda: _settled(win) and not c._label_jobs, timeout=30, at_least=0.6)
    _shot(app, win, folder, "16_heatmap", wait=False)
    rep.check("heat map drawn", (LB["id"], "overlay") in c._labels and c._labels[(LB["id"], "overlay")]["img"] is not None)
    win.edit("Clusters", lambda: LB["style"].__setitem__("render", "clusters"))
    _wait(app, lambda: _settled(win) and not c._label_jobs, timeout=30, at_least=0.6)
    _shot(app, win, folder, "17_clusters", wait=False)
    win.edit("Points", lambda: LB["style"].__setitem__("render", "points"))
    win.toggle_visible(LB["id"])
    c.set_view(*saved_view)
    # grid, picture, attribute table
    win._add(grid_layer("sweref"), "Add grid")
    svg = (b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 40"><rect width="100" height="40" rx="6" '
           b'fill="#2f6ea5"/><text x="50" y="27" font-size="18" text-anchor="middle" fill="white">LOGO</text></svg>')
    bid = doc.add_image(svg)
    win.edit("Insert picture", lambda: doc.page.setdefault("images", []).append(
        {"id": "logo1", "img": bid, "name": "logo.svg", "w": 28.0, "x": 240.0, "y": 14.0, "opacity": 1.0}))
    _shot(app, win, folder, "18_grid_and_logo")
    rep.check("picture on the page", "image:logo1" in c._page_rects)
    win.toggle_table()
    win.attr.layer.setCurrentIndex(win.attr.layer.findData(L["id"]))
    app.processEvents()
    rep.check("attribute table lists the features", win.attr.model.rowCount() == doc.table(L).n,
              f"{win.attr.model.rowCount()} rows")
    win.attr._sort(0)
    win.attr.view.selectRow(0)
    app.processEvents()
    m = c.fsel.get(L["id"])
    rep.check("selecting a table row selects the feature", m is not None and int(m.sum()) == 1)
    _shot(app, win, folder, "19_attribute_table", wait=False)
    win.toggle_table()
    c.clear_feature_selection()
    # map series: three pages from the sample layer's first features
    from . import series as S
    pages = S.coverage_pages(doc, L, "name")[:3]
    try:
        w_ = E.pdf_writer(doc, str(folder / "20_series.pdf"), 150)
        pp = QPainter(w_)
        for i, pg in enumerate(pages):
            if i:
                w_.newPage()
            doc.view = S.fit_view(doc, pg["bbox"], 0.1)
            _geo, fr = E.page_frame(doc, 150 / 25.4)
            E.prefetch(doc, win.tiles, fr)
            E.render_page(doc, win.tiles, pp, 150 / 25.4, vector=True, after_map=S.fade_outside(pg, "#ffffff"))
        pp.end()
        rep.check("map series PDF", (folder / "20_series.pdf").stat().st_size > 10000, f"{len(pages)} pages")
    except Exception:
        rep.fail("map series", traceback.format_exc())
    doc.view = saved_view

    # ------------------------------------------------------------ other backgrounds and terrain styles
    for style in ("multi", "slope", "lrm", "tint"):
        Lt = next(x for x in doc.layers if x["kind"] == "terrain")
        Lt["style"] = style
        doc.touch()
        c.update()
        _wait(app, lambda: _settled(win), timeout=20, at_least=0.3)
    _shot(app, win, folder, "10_terrain_tint")
    doc.page["landscape"] = False
    doc.page["paper"] = "A3"
    doc.touch()
    c.fit_page()
    _shot(app, win, folder, "11_portrait_A3")
    rep.ok("total time", f"{time.time() - t0:.0f} s")
    doc.dirty = False
    win.close()
