"""The map document: a stack of layers (bottom to top), the page (paper, title, legend,
scale bar, north arrow, source line) and the map view. Plain data, no Qt, so it saves
as JSON and undo is a list of snapshots.

Layer kinds
-----------
color        a plain background colour                        {"color"}
basemap      a background map from a tile service            {"source": basemap id, "labels"}
terrain      shading or height colours from elevation data   {"style", "strength"}
service      a WMS / XYZ map service the user added           {"source": TileSource dict}
data         features imported from a file or map service     {"table", "geometry", "fields", "style", "label", "legend"}
             (the features are a table.Table in Document.tables[layer["table"]]; tables never change,
             so undo only has to remember which table a layer shows)
annotations  your own labels, symbols, lines and areas        {"items": [...]}
grid         a coordinate grid or graticule                  {"system", "interval", "style", "color", "width", "labels"}

Every layer also has id, name, visible, locked, opacity and blend ("normal" / "multiply" / "screen").

Annotation items (all have id, type, hidden, legend, legend_text)
----------------
text    {"lon", "lat", "text", "preset", "style": text style}
symbol  {"lon", "lat", "symbol", "size", "color", "badge", "label", "label_style"}
line    {"coords": [[lon, lat], ...], "style": {"color", "width", "dash", "opacity", "arrow"}}
area    {"coords": [[lon, lat], ...], "style": {"fill", "fill_opacity", "stroke", "width", "dash"}}

Files are .mapmaker: a zip with document.json and data/<table id>/ (numpy arrays) for imported features."""
from __future__ import annotations

import copy
import json
import time
import uuid
import zipfile
from pathlib import Path

from . import __version__
from .table import Table
from .styles import TEXT_PRESETS, default_area_style, default_data_style, default_line_style, text_style

SUFFIX = ".mapmaker"
FORMAT = 2
SWEDEN_VIEW = [0.5444, 0.2770, 4.6]     # Web Mercator centre and zoom showing all of Sweden


def new_id() -> str:
    return uuid.uuid4().hex[:10]


# ---------------------------------------------------------------------- page
PAPERS = {"A4": (210, 297), "A3": (297, 420), "A2": (420, 594), "Letter": (215.9, 279.4), "Square": (250, 250),
          "Screen 16:9": (180, 320)}
PAGE_ELEMENTS = {"title": "Title", "legend": "Legend", "scalebar": "Scale bar", "north": "North arrow",
                 "credits": "Sources and credits"}


def default_page() -> dict:
    """The printed page. Positions of legend, scale bar and north arrow are fractions of the map
    frame (0..1 from the top left) for the element's anchor corner ("tl", "tr", "bl", "br")."""
    return {
        "show": True, "paper": "A4", "landscape": True, "margin_mm": 12, "background": "#ffffff",
        "title": "", "subtitle": "", "title_align": "left",
        "title_style": {"font": "Georgia", "size": 22, "bold": True, "color": "#1d1d1d"},
        "neatline": True,
        "legend": {"show": True, "title": "", "pos": [0.985, 0.975], "anchor": "br"},
        "scalebar": {"show": True, "pos": [0.015, 0.975], "anchor": "bl", "units": "metric", "style": "bar"},
        "north": {"show": True, "pos": [0.985, 0.025], "anchor": "tr"},
        "credits": {"show": True, "text": "", "auto": True},
        "design": {},                     # see styles.DESIGN_DEFAULTS – themes, legend style, colours …
        "images": [],                     # pictures and logos: {"id", "img", "x", "y", "w" (mm), "opacity"}
    }


def merge_page(saved: dict | None) -> dict:
    page = default_page()
    for k, v in (saved or {}).items():
        page[k] = dict(page[k], **v) if isinstance(page.get(k), dict) and isinstance(v, dict) else v
    return page


def paper_mm(page: dict) -> tuple[float, float]:
    """Page width and height in millimetres."""
    w, h = PAPERS.get(page.get("paper", "A4"), PAPERS["A4"])
    return (max(w, h), min(w, h)) if page.get("landscape", True) else (min(w, h), max(w, h))


# ---------------------------------------------------------------------- layers
def _layer(kind: str, name: str, **kw) -> dict:
    d = {"id": new_id(), "kind": kind, "name": name, "visible": True, "locked": False, "opacity": 1.0,
         "blend": "normal"}
    d.update(kw)
    return d


def color_layer(color: str = "#f4f1ea", name: str = "Background colour") -> dict:
    return _layer("color", name, color=color)


def basemap_layer(source: str = "esri-light", name: str | None = None) -> dict:
    from .tiles import BASEMAPS_BY_ID
    src = BASEMAPS_BY_ID.get(source)
    return _layer("basemap", name or (src.name if src else source), source=source, labels=False)


def terrain_layer(style: str = "hillshade") -> dict:
    from .terrain import TERRAIN_STYLES
    label = TERRAIN_STYLES.get(style, style).split(" (")[0]
    name = ("Terrain – " if style == "tint" else "Laser terrain – ") + label[0].lower() + label[1:]
    blend = "normal" if style in ("lrm", "tint") else "multiply"
    return _layer("terrain", name, style=style, strength=0.8, opacity=0.8 if style == "tint" else 1.0, blend=blend)


def grid_layer(system: str = "sweref") -> dict:
    """A coordinate grid: SWEREF 99 TM metres or a latitude/longitude graticule."""
    name = "Grid – SWEREF 99 TM" if system == "sweref" else "Grid – latitude / longitude"
    return _layer("grid", name, system=system, interval=0, style="lines", color="#3c3c3c", width=0.6,
                  dash="solid", labels=True, label_size=7.0, opacity=0.75)


def service_layer(source: dict) -> dict:
    return _layer("service", source.get("name", "Map service"), opacity=0.85, source=source)


def annotation_layer(name: str = "Labels & symbols") -> dict:
    return _layer("annotations", name, items=[])


def data_layer(name: str, geometry: str, fields: list, source: dict, color: str | None = None,
               table: str | None = None) -> dict:
    return _layer("data", name, table=table, geometry=geometry, fields=fields, source=source,
                  style=default_data_style(geometry, color),
                  label={"field": None, "style": text_style("feature")}, legend=True, count=0)


# ---------------------------------------------------------------------- annotation items
def _item(kind: str, **kw) -> dict:
    d = {"id": new_id(), "type": kind, "hidden": False, "legend": kind == "symbol", "legend_text": ""}
    d.update(kw)
    return d


def text_item(lon, lat, text, preset: str = "place") -> dict:
    return _item("text", lon=float(lon), lat=float(lat), text=text, preset=preset, style=text_style(preset),
                 legend=False)


def symbol_item(lon, lat, symbol="pin", color="#b03a2e", size=22, badge=False) -> dict:
    return _item("symbol", lon=float(lon), lat=float(lat), symbol=symbol, color=color, size=float(size),
                 badge=bool(badge), label="", label_style=text_style("place"))


def line_item(coords, color="#b03a2e", width=2.5, dash="solid") -> dict:
    st = default_line_style(color)
    st.update(width=float(width), dash=dash)
    return _item("line", coords=[[float(c[0]), float(c[1])] for c in coords], style=st)


def area_item(coords, fill="#b03a2e", stroke=None) -> dict:
    st = default_area_style(fill)
    if stroke:
        st["stroke"] = stroke
    return _item("area", coords=[[float(c[0]), float(c[1])] for c in coords], style=st)


def item_lonlat(it: dict):
    """A representative position of an item (its point, or the middle of its vertices)."""
    if "lon" in it:
        return it["lon"], it["lat"]
    c = it.get("coords") or [[0, 0]]
    return sum(p[0] for p in c) / len(c), sum(p[1] for p in c) / len(c)


def item_title(it: dict) -> str:
    t = it.get("type")
    if t == "text":
        return (it.get("text") or "Text").replace("\n", " ")[:40]
    if t == "symbol":
        from .symbols import label
        return (it.get("label") or it.get("legend_text") or label(it.get("symbol", "circle")))[:40]
    if it.get("legend_text"):
        return it["legend_text"][:40]
    return {"line": "Line", "area": "Area"}.get(t, t or "Item")


def item_bbox(it: dict):
    """(west, south, east, north) of an item."""
    if "lon" in it:
        return it["lon"], it["lat"], it["lon"], it["lat"]
    c = it.get("coords") or [[0, 0]]
    xs, ys = [p[0] for p in c], [p[1] for p in c]
    return min(xs), min(ys), max(xs), max(ys)


def move_item(it: dict, dlon: float, dlat: float):
    if "lon" in it:
        it["lon"] += dlon
        it["lat"] += dlat
    for p in it.get("coords", []):
        p[0] += dlon
        p[1] += dlat


# ---------------------------------------------------------------------- the document
class Document:
    def __init__(self):
        self.title = "Untitled map"
        self.layers: list[dict] = []          # bottom to top
        self.page = default_page()
        self.view = list(SWEDEN_VIEW)         # [cx, cy, zoom] – Web Mercator; zoom at 96 dpi
        self.tables: dict = {}                # table id -> table.Table (imported features)
        self.images: dict = {}                # image id -> file bytes (PNG, JPEG or SVG) placed on the page
        self.path: Path | None = None
        self.dirty = False
        self._undo: list[tuple[str, str, object, float]] = []
        self._redo: list[tuple[str, str]] = []
        self.revision = 0                     # bumped on every change (views cache by it)

    @classmethod
    def new(cls) -> "Document":
        d = cls()
        d.layers = [basemap_layer("esri-light"), annotation_layer()]
        return d

    # -- state and undo
    def state(self) -> str:
        return json.dumps({"title": self.title, "layers": self.layers, "page": self.page}, ensure_ascii=False)

    def _apply(self, st: str):
        d = json.loads(st)
        self.title, self.layers, self.page = d["title"], d["layers"], d["page"]
        self.revision += 1

    def checkpoint(self, label: str = "", merge=None):
        """Call before a change: remembers the state for undo and marks the document changed.
        Changes with the same `merge` key less than 1.5 s apart (dragging a slider, typing) share one step."""
        now = time.time()
        last = self._undo[-1] if self._undo else None
        if not (merge is not None and last is not None and last[2] == merge and now - last[3] < 1.5 and not self._redo):
            self._undo.append((label, self.state(), merge, now))
            del self._undo[:-200]
        else:
            self._undo[-1] = (last[0], last[1], merge, now)
        self._redo.clear()
        self.dirty = True
        self.revision += 1

    def push_undo(self, label: str, state: str):
        """Record an undo step from a state taken before a change that is now finished (a drag, an edit)."""
        if state == self.state():
            return
        self._undo.append((label, state, None, 0.0))
        del self._undo[:-200]
        self._redo.clear()
        self.dirty = True
        self.revision += 1

    def touch(self):
        """A change that does not need its own undo step (e.g. the rest of a drag)."""
        self.dirty = True
        self.revision += 1

    def can_undo(self) -> bool:
        return bool(self._undo)

    def can_redo(self) -> bool:
        return bool(self._redo)

    def undo_label(self) -> str:
        return self._undo[-1][0] if self._undo else ""

    def redo_label(self) -> str:
        return self._redo[-1][0] if self._redo else ""

    def undo(self) -> str | None:
        if not self._undo:
            return None
        label, st, _m, _t = self._undo.pop()
        self._redo.append((label, self.state()))
        self._apply(st)
        self.dirty = True
        return label

    def redo(self) -> str | None:
        if not self._redo:
            return None
        label, st = self._redo.pop()
        self._undo.append((label, self.state(), None, 0.0))
        self._apply(st)
        self.dirty = True
        return label

    # -- layers
    def layer(self, lid: str | None) -> dict | None:
        return next((L for L in self.layers if L["id"] == lid), None)

    def index(self, lid: str) -> int:
        return next((i for i, L in enumerate(self.layers) if L["id"] == lid), -1)

    def add_layer(self, layer: dict, above: str | None = None) -> dict:
        """Add a layer on top, or just above another layer."""
        i = self.index(above) if above else -1
        if i < 0:
            self.layers.append(layer)
        else:
            self.layers.insert(i + 1, layer)
        return layer

    def add_background(self, layer: dict) -> dict:
        """Add a background-type layer just above the existing backgrounds (below data and annotations)."""
        if layer["kind"] == "color":          # a plain colour belongs under everything
            self.layers.insert(0, layer)
            return layer
        i = 0
        for j, L in enumerate(self.layers):
            if L["kind"] in ("color", "basemap", "terrain", "service"):
                i = j + 1
        self.layers.insert(i, layer)
        return layer

    def remove_layer(self, lid: str):
        self.layers = [L for L in self.layers if L["id"] != lid]

    def move_layer(self, lid: str, new_index: int):
        i = self.index(lid)
        if i < 0:
            return
        L = self.layers.pop(i)
        self.layers.insert(max(0, min(len(self.layers), new_index)), L)

    def duplicate_layer(self, lid: str) -> dict | None:
        L = self.layer(lid)
        if L is None:
            return None
        c = copy.deepcopy(L)
        c["id"] = new_id()
        c["name"] = L["name"] + " copy"
        for it in c.get("items", []):
            it["id"] = new_id()
        self.add_layer(c, above=lid)
        return c

    def find_item(self, item_id: str | None):
        for L in self.layers:
            for it in L.get("items", []):
                if it["id"] == item_id:
                    return L, it
        return None, None

    def remove_item(self, item_id: str):
        for L in self.layers:
            if "items" in L:
                L["items"] = [it for it in L["items"] if it["id"] != item_id]

    def move_item_to(self, item_id: str, layer_id: str, index: int):
        """Move an item to another annotation layer (or another place in its own), index in that layer's list."""
        src, it = self.find_item(item_id)
        dst = self.layer(layer_id)
        if it is None or dst is None or dst["kind"] != "annotations":
            return
        src["items"] = [x for x in src["items"] if x["id"] != item_id]
        dst["items"].insert(max(0, min(len(dst["items"]), index)), it)

    def table(self, L: dict | str | None):
        """The features of a data layer (a table.Table), or None."""
        if isinstance(L, str):
            L = self.layer(L)
        return self.tables.get((L or {}).get("table")) if L else None

    def add_image(self, data: bytes) -> str:
        iid = new_id()
        self.images[iid] = bytes(data)
        return iid

    def add_table(self, t) -> str:
        tid = new_id()
        self.tables[tid] = t
        return tid

    def annotation_layers(self) -> list[dict]:
        return [L for L in self.layers if L["kind"] == "annotations"]

    def unique_name(self, name: str) -> str:
        names = {L["name"] for L in self.layers}
        if name not in names:
            return name
        i = 2
        while f"{name} {i}" in names:
            i += 1
        return f"{name} {i}"

    # -- files
    def save(self, path) -> Path:
        path = Path(path)
        if path.suffix.lower() != SUFFIX:
            path = path.with_suffix(SUFFIX)
        body = {"format": FORMAT, "app": f"MapMaker {__version__}", "saved": time.strftime("%Y-%m-%d %H:%M"),
                "title": self.title, "layers": self.layers, "page": self.page, "view": self.view}
        tmp = path.with_name(path.name + ".tmp")
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED, compresslevel=1) as z:
            z.writestr("document.json", json.dumps(body, ensure_ascii=False, indent=1))
            for im in self.page.get("images") or []:
                if im.get("img") in self.images:
                    z.writestr(f"images/{im['img']}", self.images[im["img"]])
            done = set()
            for L in self.layers:
                tid = L.get("table")
                if L["kind"] == "data" and tid in self.tables and tid not in done:
                    self.tables[tid].save(z, f"data/{tid}")
                    done.add(tid)
        tmp.replace(path)
        self.path = path
        self.dirty = False
        return path

    @classmethod
    def load(cls, path) -> "Document":
        path = Path(path)
        d = cls()
        with zipfile.ZipFile(path) as z:
            body = json.loads(z.read("document.json").decode("utf-8"))
            if int(body.get("format", 1)) > FORMAT:
                raise ValueError("This map was saved by a newer version of MapMaker.")
            d.title = body.get("title") or path.stem
            d.layers = body.get("layers") or []
            d.page = merge_page(body.get("page"))
            if body.get("view"):
                d.view = list(body["view"])
            names = set(z.namelist())
            for n in names:
                if n.startswith("images/") and len(n) > 7:
                    d.images[n[7:]] = z.read(n)
            for L in d.layers:
                L.setdefault("blend", "normal")
                L.setdefault("locked", False)
                if L["kind"] != "data":
                    continue
                tid = L.get("table")
                if tid and f"data/{tid}/meta.json" in names:
                    if tid not in d.tables:
                        d.tables[tid] = Table.load(z, f"data/{tid}")
                elif f"data/{L['id']}.json" in names:          # MapMaker 0.1
                    tid = new_id()
                    d.tables[tid] = Table.from_features(json.loads(z.read(f"data/{L['id']}.json").decode("utf-8")))
                    L["table"] = tid
        d.path = path
        return d


# ---------------------------------------------------------------------- recent files
def push_recent(settings, path, limit: int = 10):
    items = settings.value("recent") or []
    if isinstance(items, str):
        items = [items]
    items = [p for p in items if p and p != str(path)]
    settings.setValue("recent", [str(path)] + items[:limit - 1])


def recent_list(settings) -> list[str]:
    v = settings.value("recent") or []
    if isinstance(v, str):
        v = [v]
    return [p for p in v if p and Path(p).exists()]


__all__ = ["Document", "SUFFIX", "PAPERS", "PAGE_ELEMENTS", "default_page", "merge_page", "paper_mm", "color_layer",
           "basemap_layer", "terrain_layer", "service_layer", "grid_layer", "annotation_layer", "data_layer", "text_item",
           "symbol_item", "line_item", "area_item", "new_id", "item_lonlat", "item_title", "item_bbox", "move_item",
           "push_recent", "recent_list", "TEXT_PRESETS"]
