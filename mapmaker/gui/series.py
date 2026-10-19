"""Map series: the same page layout made once for each of a set of areas – one page per parish,
site or survey area – exported as one PDF with many pages or as separate files.

The areas come from a data layer (its features: areas, lines or points) or from an annotation
layer (its areas, lines and symbols). Each page is zoomed to its area, the title can name it, and
everything outside it can be faded so the area stands out."""
from __future__ import annotations

import logging
import math
import re
from pathlib import Path

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QImage, QPainter, QPainterPath, QPolygonF
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFileDialog,
                               QFormLayout, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QProgressDialog,
                               QPushButton, QSpinBox, QVBoxLayout)

from ..document import item_bbox, item_title, paper_mm
from ..render import lonlat_to_merc
from . import export as E
from . import painter as P
from .theme import T
from .workers import run_async

log = logging.getLogger("mapmaker")
MAX_PAGES = 500


def coverage_pages(doc, L: dict, name_field: str | None = None) -> list[dict]:
    """[{"name", "bbox" (w, s, e, n), "rings": [lon/lat arrays] or None}] for a layer."""
    pages = []
    if L["kind"] == "data":
        t = doc.table(L)
        if t is None:
            return []
        for i in range(min(t.n, MAX_PAGES)):
            bb = t.bbox_of(np.array([i]))
            if bb is None:
                continue
            name = t.text(i, name_field) if name_field else ""
            rings = t.rings(i) if t.kind[i] == 2 else None
            pages.append({"name": name or f"{L['name']} {i + 1}", "bbox": bb, "rings": rings})
    elif L["kind"] == "annotations":
        for j, it in enumerate(L.get("items", [])[:MAX_PAGES]):
            if it.get("hidden"):
                continue
            rings = [np.asarray(it["coords"], dtype=float)] if it["type"] == "area" else None
            name = it.get("legend_text") or (item_title(it) if it["type"] in ("text", "symbol") else "")
            pages.append({"name": name or f"Area {j + 1}", "bbox": item_bbox(it), "rings": rings})
    return pages


def fit_view(doc, bbox, margin: float = 0.1, keep_zoom: float | None = None, point_zoom: float = 15.0):
    """The map view [cx, cy, zoom] that shows bbox in the page's map frame."""
    w, s, e, n = bbox
    x0, y1 = lonlat_to_merc(w, s)
    x1, y0 = lonlat_to_merc(e, n)
    cx, cy = float(x0 + x1) / 2, float(y0 + y1) / 2
    if keep_zoom is not None:
        return [cx, cy, keep_zoom]
    geo = P.page_geometry(doc, 0, 0, P.PX_PER_MM)          # k = 1: frame size in reference pixels
    Wr, Hr = geo.frame.width() * (1 - 2 * margin), geo.frame.height() * (1 - 2 * margin)
    dx, dy = float(x1 - x0), float(y1 - y0)
    if dx < 1e-12 and dy < 1e-12:
        return [cx, cy, point_zoom]
    z = math.log2(max(1e-9, min(Wr / (max(dx, 1e-12) * 256), Hr / (max(dy, 1e-12) * 256))))
    return [cx, cy, min(19.0, z)]


def fade_outside(page: dict, color: str, strength: float = 0.55):
    """after_map hook: veil everything outside the page's area and outline the area."""
    def draw(p: QPainter, fr: P.Frame):
        outside = QPainterPath()
        outside.addRect(fr.rect)
        area = QPainterPath()
        area.setFillRule(Qt.FillRule.OddEvenFill)
        if page.get("rings"):
            for r in page["rings"]:
                a = np.asarray(r, dtype=float)
                x, y = fr.to_px(a[:, 0], a[:, 1])
                area.addPolygon(QPolygonF([QPointF(float(u), float(v)) for u, v in zip(x, y)]))
                area.closeSubpath()
        else:
            w, s, e, n = page["bbox"]
            x0, y0 = fr.to_px(w, n)
            x1, y1 = fr.to_px(e, s)
            r = QRectF(QPointF(float(x0), float(y0)), QPointF(float(x1), float(y1))).normalized()
            if r.width() < 4 and r.height() < 4:
                c = r.center()
                rad = 40 * fr.k
                area.addEllipse(c, rad, rad)
            else:
                area.addRect(r)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        c = QColor(color)
        c.setAlphaF(strength)
        p.fillPath(outside.subtracted(area), QBrush(c))
        p.strokePath(area, P.pen("#1d1d1d", 1.6 * fr.k))
    return draw


def _safe(name: str) -> str:
    return re.sub(r'[\\/:*?"<>|]+', "_", name).strip() or "page"


class SeriesDialog(QDialog):
    def __init__(self, win):
        super().__init__(win)
        self.win = win
        self.setWindowTitle("Map series")
        self.resize(520, 460)
        doc = win.doc
        lay = QVBoxLayout(self)
        intro = QLabel("Make one page for each area of a layer, with the same layout, legend and styles.")
        intro.setWordWrap(True)
        lay.addWidget(intro)
        f = QFormLayout()
        self.layer = QComboBox()
        for L in reversed(doc.layers):
            if L["kind"] in ("data", "annotations"):
                n = doc.table(L).n if L["kind"] == "data" and doc.table(L) is not None else len(L.get("items", []))
                self.layer.addItem(f"{L['name']}  ({n})", L["id"])
        self.name = QComboBox()
        self.title = QLineEdit((doc.page.get("title") or "") + (" – {name}" if doc.page.get("title") else "{name}"))
        self.title.setToolTip("{name} is replaced by each page's name")
        self.scale = QComboBox()
        self.scale.addItem("Fit each area on its page", "fit")
        self.scale.addItem("Keep the current scale on every page", "keep")
        self.margin = QSpinBox()
        self.margin.setRange(0, 45)
        self.margin.setValue(10)
        self.margin.setSuffix(" %")
        self.fade = QCheckBox("Fade the map outside the area")
        self.fade.setChecked(True)
        self.fmt = QComboBox()
        self.fmt.addItem("One PDF with a page per area", "pdf")
        self.fmt.addItem("A PNG image per area", "png")
        self.fmt.addItem("A PDF file per area", "pdfs")
        self.dpi = QComboBox()
        for d in (150, 300):
            self.dpi.addItem(f"{d} dpi", d)
        self.dpi.setCurrentIndex(1)
        f.addRow("Areas from", self.layer)
        f.addRow("Page names from", self.name)
        f.addRow("Page title", self.title)
        f.addRow("Scale", self.scale)
        f.addRow("Space around", self.margin)
        f.addRow(self.fade)
        f.addRow("Make", self.fmt)
        f.addRow("Resolution", self.dpi)
        lay.addLayout(f)
        prev = QHBoxLayout()
        self.page_no = QSpinBox()
        self.page_no.setRange(1, 1)
        show = QPushButton("Show on the page")
        show.clicked.connect(self.preview)
        prev.addWidget(QLabel("Preview page"))
        prev.addWidget(self.page_no)
        prev.addWidget(show)
        prev.addStretch(1)
        lay.addLayout(prev)
        self.info = QLabel()
        self.info.setStyleSheet(f"color:{T.muted}")
        self.info.setWordWrap(True)
        lay.addWidget(self.info)
        lay.addStretch(1)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.button(QDialogButtonBox.StandardButton.Ok).setText("Make the series…")
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self.layer.currentIndexChanged.connect(self._layer)
        self.name.currentIndexChanged.connect(self._count)
        self._layer()

    def _layer(self):
        doc = self.win.doc
        L = doc.layer(self.layer.currentData())
        self.name.clear()
        if L is not None and L["kind"] == "data" and doc.table(L) is not None:
            t = doc.table(L)
            for fld in t.fields:
                self.name.addItem(fld, fld)
            guess = next((i for i, fld in enumerate(t.fields) if re.search(r"namn|name|title|socken|lämning", fld,
                                                                             re.I)), 0)
            self.name.setCurrentIndex(guess)
            self.name.setEnabled(True)
        else:
            self.name.addItem("The item's name / legend text", None)
            self.name.setEnabled(False)
        self._count()

    def pages(self):
        L = self.win.doc.layer(self.layer.currentData())
        return coverage_pages(self.win.doc, L, self.name.currentData()) if L else []

    def _count(self):
        n = len(self.pages())
        self.page_no.setRange(1, max(1, n))
        self.info.setText(f"{n} page{'s' if n != 1 else ''}" + (f" (at most {MAX_PAGES})" if n >= MAX_PAGES else "")
                          + ". The layout, legend and styles are those of the current page.")

    def options(self) -> dict:
        return {"title": self.title.text(), "scale": self.scale.currentData(), "margin": self.margin.value() / 100,
                "fade": self.fade.isChecked(), "fmt": self.fmt.currentData(), "dpi": self.dpi.currentData()}

    def preview(self):
        pages = self.pages()
        if not pages:
            return
        pg = pages[self.page_no.value() - 1]
        o = self.options()
        doc = self.win.doc
        keep = doc.view[2] if o["scale"] == "keep" else None
        doc.view = fit_view(doc, pg["bbox"], o["margin"], keep)
        if not doc.page.get("show"):
            self.win.set_page_mode(True)
        self.win.canvas.update()
        self.win.canvas.viewChanged.emit()
        self.win.status(f"Page {self.page_no.value()}: {pg['name']}", 5000)


def run_series(win):
    if not any(L["kind"] in ("data", "annotations") for L in win.doc.layers):
        QMessageBox.information(win, "Map series", "Add a data layer or draw some areas first – each of them "
                                "becomes a page.")
        return
    d = SeriesDialog(win)
    if d.exec() != QDialog.DialogCode.Accepted:
        return
    pages = d.pages()
    if not pages:
        QMessageBox.information(win, "Map series", "That layer has nothing to make pages from.")
        return
    o = d.options()
    doc, cache = win.doc, win.tiles
    folder = win.settings.value("export_dir") or str(win.default_dir())
    if o["fmt"] == "pdf":
        path, _ = QFileDialog.getSaveFileName(win, "Save the map series", str(Path(folder) / "map series.pdf"),
                                              "PDF (*.pdf)")
        if not path:
            return
        if not path.lower().endswith(".pdf"):
            path += ".pdf"
        out_dir = Path(path).parent
    else:
        out = QFileDialog.getExistingDirectory(win, "Folder for the pages", folder)
        if not out:
            return
        out_dir, path = Path(out), None
    win.settings.setValue("export_dir", str(out_dir))
    saved_view, saved_title = list(doc.view), doc.page.get("title", "")
    keep = doc.view[2] if o["scale"] == "keep" else None
    prog = QProgressDialog("Preparing…", "Cancel", 0, len(pages), win)
    prog.setWindowTitle("Map series")
    prog.setWindowModality(Qt.WindowModality.WindowModal)
    prog.setMinimumDuration(0)
    prog.setValue(0)
    state = {"i": 0, "writer": None, "painter": None, "cancel": False}
    prog.canceled.connect(lambda: state.__setitem__("cancel", True))
    from ..styles import design
    veil = design(doc.page)["map_bg"]

    def finish(msg=None):
        if state["painter"] is not None:
            state["painter"].end()
        doc.view, doc.page["title"] = saved_view, saved_title
        prog.close()
        win.canvas.update()
        if msg:
            win.status(msg, 10000)

    def set_page(i):
        pg = pages[i]
        doc.view = fit_view(doc, pg["bbox"], o["margin"], keep)
        doc.page["title"] = o["title"].replace("{name}", pg["name"])

    def step():
        i = state["i"]
        if state["cancel"]:
            finish("Map series cancelled.")
            return
        if i >= len(pages):
            finish(f"Map series: {len(pages)} pages saved in {out_dir}")
            return
        set_page(i)
        prog.setLabelText(f"Page {i + 1} of {len(pages)}: {pages[i]['name']}")
        prog.setValue(i)
        _geo, frame = E.page_frame(doc, o["dpi"] / 25.4)
        run_async(E.prefetch, doc, cache, frame, on_done=lambda _r: draw(i), on_error=lambda _tb: draw(i))

    def draw(i):
        if state["cancel"]:
            finish("Map series cancelled.")
            return
        set_page(i)
        pg = pages[i]
        hook = fade_outside(pg, veil) if o["fade"] else None
        try:
            if o["fmt"] == "pdf":
                if state["writer"] is None:
                    state["writer"] = E.pdf_writer(doc, path, o["dpi"])
                    state["painter"] = QPainter(state["writer"])
                else:
                    state["writer"].newPage()
                E.render_page(doc, cache, state["painter"], o["dpi"] / 25.4, vector=True, after_map=hook)
            elif o["fmt"] == "pdfs":
                w = E.pdf_writer(doc, str(out_dir / f"{i + 1:03d} {_safe(pg['name'])}.pdf"), o["dpi"])
                pp = QPainter(w)
                try:
                    E.render_page(doc, cache, pp, o["dpi"] / 25.4, vector=True, after_map=hook)
                finally:
                    pp.end()
            else:
                img = E.page_image(doc, cache, o["dpi"], after_map=hook)
                img.convertToFormat(QImage.Format.Format_RGB32).save(
                    str(out_dir / f"{i + 1:03d} {_safe(pg['name'])}.png"))
        except Exception as e:
            log.exception("Map series page failed")
            finish()
            QMessageBox.warning(win, "Map series", f"Page {i + 1} could not be made:\n{e}")
            return
        state["i"] = i + 1
        step()
    step()


__all__ = ["run_series", "coverage_pages", "fit_view", "SeriesDialog", "paper_mm", "QDoubleSpinBox"]
