"""Dialogs: adding a map service, downloading from an ArcGIS service, going to coordinates,
keyboard shortcuts."""
from __future__ import annotations

import time

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QComboBox, QDialog, QDialogButtonBox, QFormLayout, QGroupBox, QLabel, QLineEdit,
                               QRadioButton, QSpinBox, QTextBrowser, QVBoxLayout)

from ..sources import CATALOGUE
from ..tiles import TileSource
from .theme import T

# ====================================================================== map services
PRESETS = [
    ("Custom XYZ / WMTS tiles", "xyz", "", ""),
    ("Custom WMS", "wms", "", ""),
    ("Skogsstyrelsen – Skog och historia (remains recorded in forests, open)", "wms", "© Skogsstyrelsen",
     "https://geodpags.skogsstyrelsen.se/arcgis/services/Geodataportal/GeodataportalVisaSkoghistoria/MapServer/WmsServer"),
    ("Skogsstyrelsen – remains found by AI in laser data (open)", "wms", "© Skogsstyrelsen",
     "https://geodpags.skogsstyrelsen.se/arcgis/services/Geodataportal/GeodataportalVisaDVIS/MapServer/WMSServer"),
    ("Lantmäteriet – laser hillshade, Markhöjdmodell Visning (Geotorget login)", "wms", "© Lantmäteriet",
     "https://maps.lantmateriet.se/hojdmodell/wms/v1.1"),
    ("Lantmäteriet – Topografisk webbkarta (free account)", "xyz", "© Lantmäteriet",
     "Paste the EPSG:3857 tile URL from Geotorget with {z}/{x}/{y}"),
    ("Lantmäteriet – Ortofoto Visning (free account)", "wms", "© Lantmäteriet", "Paste the WMS URL from Geotorget"),
]


class ServiceDialog(QDialog):
    """Add a map from a tile service (XYZ / WMTS) or a WMS as a layer."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Add a map service")
        self.resize(580, 420)
        lay = QVBoxLayout(self)
        form = QFormLayout()
        self.preset = QComboBox()
        for p in PRESETS:
            self.preset.addItem(p[0])
        self.name = QLineEdit()
        self.kind = QComboBox()
        self.kind.addItem("XYZ / WMTS tiles  ({z}/{x}/{y})", "xyz")
        self.kind.addItem("WMS", "wms")
        self.url = QLineEdit()
        self.layers = QLineEdit()
        self.layers.setPlaceholderText("comma separated")
        self.attr = QLineEdit()
        self.maxz = QSpinBox()
        self.maxz.setRange(1, 22)
        self.maxz.setValue(19)
        self.crs = QComboBox()
        self.crs.addItem("Web Mercator (EPSG:3857)", 3857)
        self.crs.addItem("SWEREF 99 TM (EPSG:3006) – reprojected", 3006)
        form.addRow("Start from", self.preset)
        form.addRow("Name", self.name)
        form.addRow("Type", self.kind)
        form.addRow("Address", self.url)
        form.addRow("WMS layer(s)", self.layers)
        form.addRow("WMS coordinates", self.crs)
        form.addRow("Credit", self.attr)
        form.addRow("Most detailed zoom", self.maxz)
        lay.addLayout(form)
        grp = QGroupBox("Login (optional, HTTP Basic – saved in the map file)")
        gl = QFormLayout(grp)
        self.user = QLineEdit()
        self.pw = QLineEdit()
        self.pw.setEchoMode(QLineEdit.EchoMode.Password)
        gl.addRow("Username", self.user)
        gl.addRow("Password", self.pw)
        lay.addWidget(grp)
        self.err = QLabel()
        self.err.setWordWrap(True)
        self.err.setStyleSheet(f"color:{T.muted}")
        lay.addWidget(self.err)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self._ok)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self.preset.currentIndexChanged.connect(self._preset)
        self.kind.currentIndexChanged.connect(self._kind)
        self._preset(0)

    def _kind(self):
        wms = self.kind.currentData() == "wms"
        self.layers.setEnabled(wms)
        self.crs.setEnabled(wms)

    def _preset(self, i):
        label, kind, attr, hint = PRESETS[i]
        self.err.clear()
        self.kind.setCurrentIndex(0 if kind == "xyz" else 1)
        self.name.setText("" if label.startswith("Custom") else label.split(" (")[0].split(" – ", 1)[-1])
        self.attr.setText(attr)
        self.url.clear()
        self.url.setPlaceholderText(hint or ("https://…/{z}/{x}/{y}.png" if kind == "xyz" else "https://…/wms"))
        self.layers.clear()
        self._kind()
        if hint.startswith("https://"):
            self.url.setText(hint)
            self.crs.setCurrentIndex(1)
        if "laser hillshade" in label:
            self.layers.setText("terrangskuggning")
            self.name.setText("Laser hillshade (Lantmäteriet)")
            self.err.setText("Needs your Geotorget username and password (free account at geotorget.lantmateriet.se).")
        elif "Skog och historia" in label:
            self.layers.setText("SkoghistoriaYta_Skogsstyrelsen,SkoghistoriaLinje_Skogsstyrelsen,"
                                "SkoghistoriaPunkt_Skogsstyrelsen")
            self.name.setText("Skog och historia (Skogsstyrelsen)")
            self.err.setText("Open service, no login.")
        elif "found by AI" in label:
            self.layers.setText("KolbottenAI_EjValiderardpunkt,FangstgropAI_Valideradpunkt,"
                                "Forhistoriskalagen_EjValideradLinje")
            self.name.setText("AI-detected remains (Skogsstyrelsen)")
            self.err.setText("Open service, no login. Shown as the service draws them – to style them yourself, "
                             "use Add layer → Download from an ArcGIS service.")
        elif "Lantmäteriet" in label:
            self.crs.setCurrentIndex(1)

    def _ok(self):
        name, url = self.name.text().strip(), self.url.text().strip()
        if not name or not url.lower().startswith(("http://", "https://")):
            self.err.setText("Give the layer a name and an address starting with https://")
            return
        if self.kind.currentData() == "xyz" and "{z}" not in url:
            self.err.setText("A tile address must contain {z}, {x} and {y}.")
            return
        self.accept()

    def source(self) -> dict:
        wms = self.kind.currentData() == "wms"
        return TileSource(
            id="svc-" + str(int(time.time() * 1000)), name=self.name.text().strip(), urls=[self.url.text().strip()],
            kind=self.kind.currentData(), max_zoom=self.maxz.value(), attribution=self.attr.text().strip(),
            wms_layers=self.layers.text().strip(), user=self.user.text() or None, password=self.pw.text() or None,
            role="overlay", user_defined=True, crs=int(self.crs.currentData() or 3857) if wms else 3857).to_dict()


# ====================================================================== ArcGIS download
class ArcGISDialog(QDialog):
    """Download features from an ArcGIS map or feature service layer into a data layer you can style."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Download from an ArcGIS service")
        self.resize(600, 360)
        lay = QVBoxLayout(self)
        form = QFormLayout()
        self.cat = QComboBox()
        self.cat.addItem("Other service – enter the address below", "")
        for c in CATALOGUE:
            self.cat.addItem(c["name"], c["key"])
        self.url = QLineEdit()
        self.url.setPlaceholderText("https://…/MapServer/0  or  …/FeatureServer/0")
        self.where = QLineEdit("1=1")
        self.where.setToolTip("Optional filter in the service's SQL, e.g. Det_Prob > 0.8")
        form.addRow("Layer", self.cat)
        form.addRow("Address", self.url)
        form.addRow("Filter", self.where)
        lay.addLayout(form)
        grp = QGroupBox("Which part")
        gl = QVBoxLayout(grp)
        self.view_only = QRadioButton("Only what is inside the map frame now (recommended)")
        self.everything = QRadioButton("The whole layer – can be hundreds of thousands of features")
        self.view_only.setChecked(True)
        gl.addWidget(self.view_only)
        gl.addWidget(self.everything)
        lay.addWidget(grp)
        self.info = QLabel()
        self.info.setWordWrap(True)
        self.info.setStyleSheet(f"color:{T.muted}")
        lay.addWidget(self.info, 1)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.button(QDialogButtonBox.StandardButton.Ok).setText("Download")
        bb.accepted.connect(self._ok)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self.cat.currentIndexChanged.connect(self._cat)
        self._cat()

    def _cat(self):
        key = self.cat.currentData()
        c = next((c for c in CATALOGUE if c["key"] == key), None)
        if c is None:
            self.url.setReadOnly(False)
            self.url.clear()
            self.info.setText("Any public ArcGIS REST layer works – open the service in a browser and copy the address "
                              "of one layer (it ends in /MapServer/<number> or /FeatureServer/<number>).")
            return
        self.url.setText(c["url"])
        self.url.setReadOnly(True)
        self.info.setText(f"{c['description']}  ({c.get('approx', '')})")

    def _ok(self):
        if not self.url.text().strip().startswith("http"):
            self.info.setText("Enter the address of a layer.")
            return
        self.accept()

    def choice(self) -> dict:
        key = self.cat.currentData()
        c = next((c for c in CATALOGUE if c["key"] == key), {})
        return {"url": self.url.text().strip(), "where": self.where.text().strip() or "1=1",
                "view_only": self.view_only.isChecked(), "name": c.get("name"), "color": c.get("color"),
                "attribution": c.get("attribution", "")}


# ====================================================================== go to
class GotoDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Go to coordinates")
        lay = QVBoxLayout(self)
        lab = QLabel("Latitude, longitude (e.g. 59.8586, 17.6389) or SWEREF 99 TM north, east "
                     "(e.g. 6640000, 648000):")
        lab.setWordWrap(True)
        lay.addWidget(lab)
        self.text = QLineEdit()
        lay.addWidget(self.text)
        self.err = QLabel()
        self.err.setStyleSheet(f"color:{T.danger}")
        lay.addWidget(self.err)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self._ok)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self.result_lonlat = None

    def _ok(self):
        import re
        nums = [float(x.replace(",", ".")) for x in re.findall(r"-?\d+(?:[.,]\d+)?", self.text.text().replace(", ", " "))]
        if len(nums) < 2:
            self.err.setText("Write two numbers.")
            return
        a, b = nums[:2]
        if abs(a) <= 90 and abs(b) <= 180:
            self.result_lonlat = (b, a)
        elif a > 6_000_000 and 200_000 < b < 1_000_000:
            import numpy as np
            from ..geo import get_to_wgs84
            lo, la = get_to_wgs84(3006)(np.array([[b, a]]))[0]
            self.result_lonlat = (float(lo), float(la))
        else:
            self.err.setText("Those numbers are neither latitude/longitude nor SWEREF 99 TM.")
            return
        self.accept()


SHORTCUTS = """
<h3>Tools</h3>
<table cellpadding=3>
<tr><td><b>V</b></td><td>Select and move labels, symbols, lines, areas and page elements</td></tr>
<tr><td><b>M</b></td><td>Select features of a data layer with a rectangle (Shift adds, Alt removes)</td></tr>
<tr><td><b>H</b> or hold <b>Space</b></td><td>Pan</td></tr>
<tr><td><b>T</b></td><td>Text – click to write a label</td></tr>
<tr><td><b>S</b></td><td>Symbol – click to place</td></tr>
<tr><td><b>L</b></td><td>Line – click points, double-click or Enter to finish</td></tr>
<tr><td><b>A</b></td><td>Area – click corners, double-click or Enter to close</td></tr>
</table>
<h3>Editing</h3>
<table cellpadding=3>
<tr><td><b>Ctrl+Z</b> / <b>Ctrl+Shift+Z</b></td><td>Undo / redo</td></tr>
<tr><td><b>Delete</b></td><td>Delete the selected item (in the Layers panel: the selected layer)</td></tr>
<tr><td><b>Arrow keys</b></td><td>Nudge the selected item (Shift: 10 px)</td></tr>
<tr><td><b>Enter</b> / double-click</td><td>Edit the selected label</td></tr>
<tr><td><b>Ctrl+J</b></td><td>Duplicate layer or item</td></tr>
<tr><td><b>Ctrl+D</b> / <b>Esc</b></td><td>Deselect</td></tr>
<tr><td><b>F2</b></td><td>Rename layer</td></tr>
<tr><td><b>Ctrl+]</b> / <b>Ctrl+[</b></td><td>Move layer up / down</td></tr>
</table>
<h3>Selecting features</h3>
<table cellpadding=3>
<tr><td><b>Ctrl+F</b></td><td>Select by attributes (the value box in the selection bar)</td></tr>
<tr><td><b>Ctrl+Shift+A</b></td><td>Select all features of the layer</td></tr>
<tr><td><b>Ctrl+Shift+I</b></td><td>Invert the selection</td></tr>
<tr><td><b>Ctrl+Shift+D</b></td><td>Clear the selection</td></tr>
<tr><td><b>Ctrl+Shift+J</b></td><td>New layer from the selection</td></tr>
</table>
<h3>View</h3>
<table cellpadding=3>
<tr><td><b>Wheel</b>, <b>+</b> / <b>−</b></td><td>Zoom the map</td></tr>
<tr><td><b>Ctrl+wheel</b></td><td>Zoom the page view</td></tr>
<tr><td><b>Ctrl+0</b></td><td>Fit the page in the window</td></tr>
<tr><td><b>Ctrl+L</b></td><td>Switch between map view and page layout</td></tr>
<tr><td><b>Ctrl+G</b></td><td>Go to coordinates</td></tr>
<tr><td><b>Ctrl+T</b></td><td>Attribute table</td></tr>
<tr><td><b>Ctrl+Shift+P</b></td><td>Insert a picture or logo on the page</td></tr>
</table>
"""


class ShortcutsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Keyboard shortcuts")
        self.resize(480, 560)
        lay = QVBoxLayout(self)
        tb = QTextBrowser()
        tb.setHtml(SHORTCUTS)
        tb.setOpenExternalLinks(True)
        lay.addWidget(tb)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        bb.rejected.connect(self.reject)
        bb.accepted.connect(self.accept)
        bb.button(QDialogButtonBox.StandardButton.Close).clicked.connect(self.accept)
        lay.addWidget(bb)


__all__ = ["ServiceDialog", "ArcGISDialog", "GotoDialog", "ShortcutsDialog", "Qt"]
