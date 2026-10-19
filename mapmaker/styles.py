"""Cartographic defaults: text styles, colours and default symbols for new layers.

The defaults follow common map-design practice (Tyner, *Principles of Map Design*):

* At most two typefaces – one sans serif for most labels and one serif for water and titles.
* Type distinguishes kinds of features: water names in blue italics, regions in spaced
  upper case, places in upright sans serif; size and weight show importance.
* Labels sit on a light halo so they stay readable over busy backgrounds without boxes.
* Colours follow conventions (water blue, vegetation green) and come from ColorBrewer
  schemes that print well and avoid red-green pairs for qualitative differences.
"""
from __future__ import annotations

import copy

SANS = "Segoe UI"
SERIF = "Georgia"

# a short, curated list – the book's advice is to use few typefaces
FONTS = {
    "Sans serif": ["Segoe UI", "Arial", "Calibri", "Verdana", "Tahoma", "Franklin Gothic Book"],
    "Serif": ["Georgia", "Cambria", "Times New Roman", "Palatino Linotype", "Garamond", "Book Antiqua"],
}

# ---------------------------------------------------------------------- text
# size in points (on the page / at 96 dpi on screen); halo_width in points; spacing = extra letter spacing in %
_BASE = {"font": SANS, "size": 10.0, "bold": False, "italic": False, "color": "#262626", "halo": "#ffffff",
         "halo_width": 1.4, "caps": False, "spacing": 0, "box": "", "align": "center", "angle": 0.0}

TEXT_PRESETS: dict[str, tuple[str, dict]] = {
    "place": ("Place name", {}),
    "place_major": ("Important place", {"size": 13.0, "bold": True, "color": "#1a1a1a"}),
    "region": ("Region / area", {"size": 11.0, "caps": True, "spacing": 28, "color": "#5a5a5a", "halo_width": 1.2}),
    "water": ("Water", {"font": SERIF, "italic": True, "color": "#2f6ea5", "size": 10.5}),
    "landform": ("Mountain / landform", {"font": SERIF, "size": 10.0, "caps": True, "spacing": 18, "color": "#6b4f2a"}),
    "feature": ("Small feature", {"size": 8.0, "color": "#3a3a3a", "halo_width": 1.1}),
    "note": ("Note (boxed)", {"size": 9.0, "color": "#1d1d1d", "halo": "", "box": "#ffffff", "align": "left"}),
    "heading": ("Heading on the map", {"font": SERIF, "size": 16.0, "bold": True, "color": "#1d1d1d"}),
}


def text_style(preset: str = "place") -> dict:
    s = copy.deepcopy(_BASE)
    s.update(copy.deepcopy(TEXT_PRESETS.get(preset, TEXT_PRESETS["place"])[1]))
    return s


def preset_names() -> list[tuple[str, str]]:
    return [(k, v[0]) for k, v in TEXT_PRESETS.items()]


# ---------------------------------------------------------------------- colours
# ColorBrewer (Cynthia Brewer, colorbrewer2.org) – qualitative schemes for kinds of things
DARK2 = ["#1b9e77", "#d95f02", "#7570b3", "#e7298a", "#66a61e", "#e6ab02", "#a6761d", "#666666"]
SET1 = ["#e41a1c", "#377eb8", "#4daf4a", "#984ea3", "#ff7f00", "#ffff33", "#a65628", "#f781bf", "#999999"]
PAIRED = ["#a6cee3", "#1f78b4", "#b2df8a", "#33a02c", "#fb9a99", "#e31a1c", "#fdbf6f", "#ff7f00", "#cab2d6", "#6a3d9a"]
# sequential – for more / less of something
BLUES = ["#eff3ff", "#bdd7e7", "#6baed6", "#3182bd", "#08519c"]
YLORBR = ["#ffffd4", "#fed98e", "#fe9929", "#d95f0e", "#993404"]
GREENS = ["#edf8e9", "#bae4b3", "#74c476", "#31a354", "#006d2c"]

# conventional map colours
SWATCHES = [
    ("Black", "#1d1d1d"), ("Dark grey", "#555555"), ("Grey", "#9a9a9a"), ("White", "#ffffff"),
    ("Red", "#b03a2e"), ("Orange", "#d95f02"), ("Ochre", "#e6ab02"), ("Brown", "#8c5a2b"),
    ("Dark green", "#1b6b3a"), ("Green", "#4d9a3a"), ("Light green", "#b2df8a"), ("Water blue", "#2f6ea5"),
    ("Light blue", "#a6cee3"), ("Purple", "#6a3d9a"), ("Magenta", "#c51b7d"), ("Sand", "#e9dcb5"),
]
PALETTES = [("Map colours", [c for _, c in SWATCHES]), ("Distinct (ColorBrewer Dark2)", DARK2),
            ("Bright (ColorBrewer Set1)", SET1), ("Paired", PAIRED), ("Blues", BLUES), ("Yellow–brown", YLORBR),
            ("Greens", GREENS)]

_next = [0]


def next_color() -> str:
    """Colours for new data layers, taking turns through a qualitative scheme."""
    c = DARK2[_next[0] % len(DARK2)]
    _next[0] += 1
    return c


# ---------------------------------------------------------------------- data layers
DASHES = {"solid": "Solid", "dash": "Dashed", "dot": "Dotted", "dashdot": "Dash-dot"}


def default_data_style(geometry: str, color: str | None = None) -> dict:
    """Style for a new data layer. geometry: "point", "line" or "polygon" (all keys are always present,
    so a layer with mixed geometry draws every kind)."""
    c = color or next_color()
    return {
        # points
        "marker": "circle", "size": 7.0, "color": c, "outline": "#ffffff", "outline_width": 1.0,
        # lines
        "line_color": c, "line_width": 1.6, "dash": "solid",
        # polygons
        "fill": c, "fill_opacity": 0.25, "stroke": c, "stroke_width": 1.2, "stroke_dash": "solid",
    }


def default_line_style(color="#b03a2e") -> dict:
    return {"color": color, "width": 2.5, "dash": "solid", "opacity": 1.0, "arrow": False}


def default_area_style(color="#b03a2e") -> dict:
    return {"fill": color, "fill_opacity": 0.22, "stroke": color, "width": 1.8, "dash": "solid"}




# ---------------------------------------------------------------------- map output design
DESIGN_DEFAULTS = {
    "theme": "light",
    "map_bg": "#ffffff",              # under all layers in the map frame
    "text": "#262626",                # legend, scale bar and other element text
    "muted": "#5a5a5a",               # subtitle
    "font": SANS,                     # legend, scale bar, credits
    "legend_style": "panel",          # panel | card | plain | strip
    "legend_columns": 1,
    "legend_bg": "#ffffff",
    "legend_opacity": 0.92,
    "legend_border": "#c4c4c4",
    "legend_text_size": 8.5,
    "legend_swatch": 22,
    "scale_dark": "#262626",
    "scale_light": "#ffffff",
    "scale_text_size": 8.0,
    "scale_ratio": False,             # also write the scale as 1 : 50 000
    "north_style": "simple",          # simple | star | classic | letter
    "north_color": "#262626",
    "frame_color": "#2b2b2b",
    "frame_width": 0.3,               # mm
    "frame_style": "single",          # single | double
    "credits_color": "#6a6a6a",
    "credits_size": 6.5,
    "title_position": "above",        # above | inside (on a panel in the map's top-left corner)
    "halo": "#ffffff",                # halo around element text drawn straight on the map
}

LEGEND_STYLES = {"panel": "Panel", "card": "Card with shadow", "plain": "No box (text with halo)",
                 "strip": "Strip along the frame edge"}
NORTH_STYLES = {"simple": "Simple arrow", "star": "Compass star", "classic": "Classic (arrow and N)",
                "letter": "Just “N” with a line"}

# Themes: settings for the page elements (design), the paper, the title, and how labels and
# background maps are restyled when the theme is applied.
THEMES = {
    "light": {"label": "Light", "page": "#ffffff", "title": "#1d1d1d", "basemap": "esri-light",
              "label_color": None, "label_halo": "#ffffff", "design": {}},
    "dark": {"label": "Dark", "page": "#15171b", "title": "#f2f2f2", "basemap": "esri-dark",
             "label_color": "#ececec", "label_halo": "#15171b", "water": "#8fbbe6",
             "design": {"map_bg": "#1d2026", "text": "#e8e8e8", "muted": "#a3a8ae", "legend_bg": "#202329",
                        "legend_opacity": 0.92, "legend_border": "#3b4048", "scale_dark": "#e8e8e8",
                        "scale_light": "#15171b", "north_color": "#e8e8e8", "frame_color": "#4a5058",
                        "credits_color": "#9aa0a6", "halo": "#15171b"}},
    "parchment": {"label": "Parchment", "page": "#f2ead6", "title": "#3b2f22", "basemap": "esri-light",
                  "basemap_opacity": 0.7, "label_color": "#3b2f22", "label_halo": "#f2ead6", "water": "#3f6f8f",
                  "design": {"map_bg": "#f2ead6", "text": "#3b2f22", "muted": "#6b5a45", "font": SERIF,
                             "legend_bg": "#f6efdc", "legend_border": "#b9a684", "legend_style": "card",
                             "scale_dark": "#3b2f22", "scale_light": "#f6efdc", "north_color": "#3b2f22",
                             "north_style": "classic", "frame_color": "#5a4630", "frame_style": "double",
                             "credits_color": "#6b5a45", "halo": "#f2ead6"}},
    "mono": {"label": "Monochrome", "page": "#ffffff", "title": "#111111", "basemap": "esri-light",
             "label_color": "#202020", "label_halo": "#ffffff", "water": "#404040",
             "design": {"text": "#202020", "muted": "#606060", "legend_style": "plain", "legend_border": "#909090",
                        "north_style": "letter", "frame_color": "#111111"}},
    "blueprint": {"label": "Blueprint", "page": "#0f2742", "title": "#e6f1fb", "basemap": "esri-dark",
                  "basemap_opacity": 0.55, "label_color": "#dcebfa", "label_halo": "#0f2742", "water": "#9fd0ff",
                  "design": {"map_bg": "#123052", "text": "#dcebfa", "muted": "#9fc0e0", "legend_bg": "#12345a",
                             "legend_border": "#5d8fc4", "scale_dark": "#dcebfa", "scale_light": "#0f2742",
                             "north_color": "#dcebfa", "north_style": "star", "frame_color": "#9fc0e0",
                             "credits_color": "#9fc0e0", "halo": "#0f2742"}},
    "atlas": {"label": "Classic atlas", "page": "#fbfaf6", "title": "#1d1d1d", "basemap": "esri-light",
              "label_color": None, "label_halo": "#ffffff",
              "design": {"font": SERIF, "legend_style": "card", "north_style": "classic", "frame_style": "double",
                         "title_position": "above", "map_bg": "#fbfaf6"}},
}


def design(page: dict) -> dict:
    """The page's design settings with defaults filled in."""
    d = dict(DESIGN_DEFAULTS)
    d.update(page.get("design") or {})
    return d


# ---------------------------------------------------------------------- colour schemes for thematic maps
# ColorBrewer (colorbrewer2.org). Qualitative schemes tell kinds of things apart; sequential ones
# show more and less of something (light = little, dark = much).
QUALITATIVE = {
    "Set2": ["#66c2a5", "#fc8d62", "#8da0cb", "#e78ac3", "#a6d854", "#ffd92f", "#e5c494", "#b3b3b3"],
    "Dark2": DARK2,
    "Set1": SET1,
    "Paired": PAIRED + ["#ffff99", "#b15928"],
    "Set3": ["#8dd3c7", "#ffffb3", "#bebada", "#fb8072", "#80b1d3", "#fdb462", "#b3de69", "#fccde5", "#d9d9d9",
             "#bc80bd", "#ccebc5", "#ffed6f"],
    "Earth": ["#8c510a", "#bf812d", "#dfc27d", "#80cdc1", "#35978f", "#01665e", "#543005", "#003c30"],
}
SEQUENTIAL = {
    "Blues": ["#f7fbff", "#deebf7", "#c6dbef", "#9ecae1", "#6baed6", "#4292c6", "#2171b5", "#08519c", "#08306b"],
    "Greens": ["#f7fcf5", "#e5f5e0", "#c7e9c0", "#a1d99b", "#74c476", "#41ab5d", "#238b45", "#006d2c", "#00441b"],
    "Reds": ["#fff5f0", "#fee0d2", "#fcbba1", "#fc9272", "#fb6a4a", "#ef3b2c", "#cb181d", "#a50f15", "#67000d"],
    "Purples": ["#fcfbfd", "#efedf5", "#dadaeb", "#bcbddc", "#9e9ac8", "#807dba", "#6a51a3", "#54278f", "#3f007d"],
    "Oranges": ["#fff5eb", "#fee6ce", "#fdd0a2", "#fdae6b", "#fd8d3c", "#f16913", "#d94801", "#a63603", "#7f2704"],
    "YlOrRd": ["#ffffcc", "#ffeda0", "#fed976", "#feb24c", "#fd8d3c", "#fc4e2a", "#e31a1c", "#bd0026", "#800026"],
    "YlOrBr": ["#ffffe5", "#fff7bc", "#fee391", "#fec44f", "#fe9929", "#ec7014", "#cc4c02", "#993404", "#662506"],
    "YlGnBu": ["#ffffd9", "#edf8b1", "#c7e9b4", "#7fcdbb", "#41b6c4", "#1d91c0", "#225ea8", "#253494", "#081d58"],
    "Greys": ["#ffffff", "#f0f0f0", "#d9d9d9", "#bdbdbd", "#969696", "#737373", "#525252", "#252525", "#000000"],
}


def scheme_colors(name: str, n: int) -> list[str]:
    """n colours from a scheme: qualitative ones repeat; sequential ones are sampled from light to dark
    (skipping the palest, which vanish on a light map)."""
    if name in QUALITATIVE:
        c = QUALITATIVE[name]
        return [c[i % len(c)] for i in range(n)]
    c = SEQUENTIAL.get(name, SEQUENTIAL["Blues"])
    if n <= 1:
        return [c[6]]
    lo, hi = 2, len(c) - 1
    return [c[int(round(lo + (hi - lo) * i / (n - 1)))] for i in range(n)]


HEAT_RAMPS = {
    "heat": [(0.0, (255, 255, 178, 0)), (0.15, (254, 204, 92, 150)), (0.4, (253, 141, 60, 200)),
             (0.7, (240, 59, 32, 230)), (1.0, (189, 0, 38, 245))],
    "blue": [(0.0, (240, 249, 255, 0)), (0.2, (158, 202, 225, 150)), (0.5, (66, 146, 198, 200)),
             (1.0, (8, 48, 107, 245))],
    "viridis": [(0.0, (68, 1, 84, 0)), (0.15, (59, 82, 139, 160)), (0.45, (33, 145, 140, 210)),
                (0.75, (94, 201, 98, 230)), (1.0, (253, 231, 37, 245))],
    "magma": [(0.0, (0, 0, 4, 0)), (0.2, (81, 18, 124, 160)), (0.5, (183, 55, 121, 210)),
              (0.8, (252, 137, 97, 235)), (1.0, (252, 253, 191, 250))],
}


__all__ = ["SANS", "SERIF", "FONTS", "TEXT_PRESETS", "text_style", "preset_names", "PALETTES", "SWATCHES", "DARK2",
           "next_color", "DASHES", "default_data_style", "default_line_style", "default_area_style",
           "DESIGN_DEFAULTS", "THEMES", "LEGEND_STYLES", "NORTH_STYLES", "design", "QUALITATIVE", "SEQUENTIAL",
           "scheme_colors", "HEAT_RAMPS"]
