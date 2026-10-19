"""Exporting the map: the page (PNG, JPEG or PDF at a chosen resolution) or the map view
(PNG / JPEG at 1–4 times screen size). Drawn afresh with the same code as the screen, so it
matches what you see; map tiles at the needed detail are downloaded first."""
from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import QMarginsF, QRectF, QSizeF, Qt
from PySide6.QtGui import QColor, QImage, QPageLayout, QPageSize, QPainter, QPdfWriter
from PySide6.QtWidgets import (QButtonGroup, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QLabel,
                               QMessageBox, QProgressDialog, QRadioButton, QVBoxLayout)

from ..document import paper_mm
from . import painter as P
from .theme import T
from .workers import run_async

log = logging.getLogger("mapmaker")


# ---------------------------------------------------------------------- rendering
def _tile_layers(doc, cache):
    specs = []
    for L in doc.layers:
        if L.get("visible", True) and L["kind"] in ("basemap", "terrain", "service"):
            specs += P.specs_for(L, cache)
    return specs


def page_frame(doc, s: float) -> tuple[P.PageGeometry, P.Frame]:
    geo = P.page_geometry(doc, 0, 0, s)
    cx, cy, z = doc.view
    return geo, P.Frame(geo.frame, cx, cy, z, geo.k, 1.0)


def view_frame(doc, w: int, h: int, scale: float) -> P.Frame:
    cx, cy, z = doc.view
    return P.Frame(QRectF(0, 0, w * scale, h * scale), cx, cy, z, scale, 1.0)


def prefetch(doc, cache, frame: P.Frame):
    """Download the tiles an export needs (call on a background thread)."""
    P.BlockingTiles(cache).prefetch(frame, _tile_layers(doc, cache))


def render_page(doc, cache, painter: QPainter, s: float, vector: bool, after_map=None):
    """after_map(painter, frame): extra drawing on the map, under the page elements (map series)."""
    geo, frame = page_frame(doc, s)
    bt = P.BlockingTiles(cache)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)

    def draw_map(p, fr):
        from ..styles import design
        p.fillRect(fr.rect, QColor(design(doc.page)["map_bg"]))
        P.render_map(p, doc, fr, cache, bt.draw, vector=vector, raster_groups=vector)
        if after_map is not None:
            p.save()
            p.setClipRect(fr.rect)
            after_map(p, fr)
            p.restore()
    P.draw_page(painter, doc, geo, frame, draw_map, vector=vector)


def pdf_writer(doc, path: str, dpi: int) -> QPdfWriter:
    W, H = paper_mm(doc.page)
    w = QPdfWriter(path)
    w.setResolution(dpi)
    w.setTitle(doc.page.get("title") or doc.title)
    w.setCreator("MapMaker")
    size = QPageSize(QSizeF(min(W, H), max(W, H)), QPageSize.Unit.Millimeter, "", QPageSize.SizeMatchPolicy.ExactMatch)
    w.setPageLayout(QPageLayout(size, QPageLayout.Orientation.Landscape if W > H else QPageLayout.Orientation.Portrait,
                                QMarginsF(0, 0, 0, 0), QPageLayout.Unit.Millimeter))
    return w


def page_image(doc, cache, dpi: int, after_map=None) -> QImage:
    W, H = paper_mm(doc.page)
    s = dpi / 25.4
    img = QImage(int(round(W * s)), int(round(H * s)), QImage.Format.Format_ARGB32_Premultiplied)
    img.setDotsPerMeterX(int(dpi / 0.0254))
    img.setDotsPerMeterY(int(dpi / 0.0254))
    img.fill(QColor(doc.page.get("background", "#ffffff")))
    p = QPainter(img)
    try:
        render_page(doc, cache, p, s, vector=False, after_map=after_map)
    finally:
        p.end()
    return img


def page_pdf(doc, cache, path: str, dpi: int = 300):
    W, H = paper_mm(doc.page)
    w = QPdfWriter(path)
    w.setResolution(dpi)
    w.setTitle(doc.page.get("title") or doc.title)
    w.setCreator("MapMaker")
    size = QPageSize(QSizeF(min(W, H), max(W, H)), QPageSize.Unit.Millimeter, "", QPageSize.SizeMatchPolicy.ExactMatch)
    w.setPageLayout(QPageLayout(size, QPageLayout.Orientation.Landscape if W > H else QPageLayout.Orientation.Portrait,
                                QMarginsF(0, 0, 0, 0), QPageLayout.Unit.Millimeter))
    p = QPainter(w)
    try:
        s = dpi / 25.4
        render_page(doc, cache, p, s, vector=True)
    finally:
        p.end()


def view_image(doc, cache, w: int, h: int, scale: float) -> QImage:
    frame = view_frame(doc, w, h, scale)
    img = QImage(int(w * scale), int(h * scale), QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(QColor(T.map_bg))
    p = QPainter(img)
    try:
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        P.render_map(p, doc, frame, cache, P.BlockingTiles(cache).draw)
    finally:
        p.end()
    return img


# ---------------------------------------------------------------------- dialog
class ExportDialog(QDialog):
    def __init__(self, win):
        super().__init__(win)
        self.win = win
        self.setWindowTitle("Export the map")
        self.resize(460, 300)
        doc = win.doc
        lay = QVBoxLayout(self)
        self.what = QButtonGroup(self)
        self.page_rb = QRadioButton("The page – title, map, legend, scale bar and sources")
        self.view_rb = QRadioButton("Only the map, as shown in map view")
        self.what.addButton(self.page_rb)
        self.what.addButton(self.view_rb)
        lay.addWidget(self.page_rb)
        lay.addWidget(self.view_rb)
        (self.page_rb if doc.page.get("show") else self.view_rb).setChecked(True)
        form = QFormLayout()
        self.fmt = QComboBox()
        self.fmt.addItem("PDF – sharp text and lines, for printing", "pdf")
        self.fmt.addItem("PNG image", "png")
        self.fmt.addItem("JPEG image (smaller file)", "jpg")
        self.dpi = QComboBox()
        for d, lab in ((150, "150 dpi – screen, e-mail"), (300, "300 dpi – print"), (600, "600 dpi – fine print")):
            self.dpi.addItem(lab, d)
        self.dpi.setCurrentIndex(1)
        self.scale = QComboBox()
        for k in (1, 2, 3, 4):
            self.scale.addItem(f"{k}× the window ({win.canvas.width() * k} × {win.canvas.height() * k} px)", k)
        self.scale.setCurrentIndex(1)
        form.addRow("Format", self.fmt)
        form.addRow("Resolution", self.dpi)
        form.addRow("Size", self.scale)
        lay.addLayout(form)
        self.size_lab = QLabel()
        self.size_lab.setStyleSheet(f"color:{T.muted}")
        lay.addWidget(self.size_lab)
        lay.addStretch(1)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.button(QDialogButtonBox.StandardButton.Ok).setText("Export…")
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        for w in (self.page_rb, self.view_rb):
            w.toggled.connect(self._update)
        self.fmt.currentIndexChanged.connect(self._update)
        self.dpi.currentIndexChanged.connect(self._update)
        self._update()

    def _update(self):
        page = self.page_rb.isChecked()
        if not page and self.fmt.currentData() == "pdf":
            self.fmt.setCurrentIndex(1)
        self.fmt.model().item(0).setEnabled(page)
        self.dpi.setEnabled(page)
        self.scale.setEnabled(not page)
        if page:
            W, H = paper_mm(self.win.doc.page)
            d = self.dpi.currentData()
            self.size_lab.setText(f"{self.win.doc.page.get('paper', 'A4')}, {W:g} × {H:g} mm"
                                  + (f" – {int(W / 25.4 * d)} × {int(H / 25.4 * d)} pixels"
                                     if self.fmt.currentData() != "pdf" else ""))
        else:
            self.size_lab.setText("")

    def choice(self) -> dict:
        return {"page": self.page_rb.isChecked(), "fmt": self.fmt.currentData(), "dpi": self.dpi.currentData(),
                "scale": self.scale.currentData()}


def run_export(win):
    dlg = ExportDialog(win)
    if dlg.exec() != QDialog.DialogCode.Accepted:
        return
    c = dlg.choice()
    ext = {"pdf": ".pdf", "png": ".png", "jpg": ".jpg"}[c["fmt"]]
    base = (win.doc.path.stem if win.doc.path else (win.doc.page.get("title") or "map")).strip() or "map"
    folder = win.settings.value("export_dir") or str(win.default_dir())
    filt = {"pdf": "PDF (*.pdf)", "png": "PNG image (*.png)", "jpg": "JPEG image (*.jpg *.jpeg)"}[c["fmt"]]
    path, _ = QFileDialog.getSaveFileName(win, "Export the map", str(Path(folder) / (base + ext)), filt)
    if not path:
        return
    if not path.lower().endswith(ext) and not (ext == ".jpg" and path.lower().endswith(".jpeg")):
        path += ext
    win.settings.setValue("export_dir", str(Path(path).parent))
    doc, cache = win.doc, win.tiles
    if c["page"]:
        _geo, frame = page_frame(doc, c["dpi"] / 25.4)
    else:
        frame = view_frame(doc, win.canvas.width(), win.canvas.height(), c["scale"])
    prog = QProgressDialog("Downloading map tiles for the export…", None, 0, 0, win)
    prog.setWindowTitle("Exporting")
    prog.setWindowModality(Qt.WindowModality.WindowModal)
    prog.setMinimumDuration(300)
    prog.show()

    def done(_):
        prog.setLabelText("Drawing…")
        try:
            if c["page"] and c["fmt"] == "pdf":
                page_pdf(doc, cache, path, max(150, c["dpi"]))
            else:
                img = page_image(doc, cache, c["dpi"]) if c["page"] else view_image(
                    doc, cache, win.canvas.width(), win.canvas.height(), c["scale"])
                if c["fmt"] == "jpg":
                    img = img.convertToFormat(QImage.Format.Format_RGB32)
                if not img.save(path, None, 92 if c["fmt"] == "jpg" else -1):
                    raise OSError("Could not write " + path)
        except Exception as e:
            log.exception("Export failed")
            prog.close()
            QMessageBox.warning(win, "Export failed", f"The map could not be exported:\n{e}")
            return
        prog.close()
        win.status(f"Exported to {path}", 8000)
        win.last_export = path

    def failed(tb):
        prog.close()
        QMessageBox.warning(win, "Export failed", "Downloading the map tiles failed:\n" + tb.splitlines()[-1])
    run_async(prefetch, doc, cache, frame, on_done=done, on_error=failed)
