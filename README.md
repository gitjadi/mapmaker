# MapMaker 0.3

A desktop program for designing maps, laid out like an image editor: tools on the left,
the map in the middle, Properties and Layers on the right. You build a map from layers –
background maps, laser terrain, map services, your own data, and your own labels, symbols,
lines and areas – then put it on a page with a title, legend, scale bar, north arrow and
sources, and export it as PDF or an image.

MapMaker is a fork of ArchMap without the analysis tools. The two programs are separate and
can be installed side by side.

A **browser version** is being built in [`web/`](web/README.md) – it opens and saves the same
`.mapmaker` files and can be published on your own website.

## Starting

Double-click **start.bat**. The first start installs what MapMaker needs (Python 3.10 or newer
must be installed). **selftest.bat** runs an automatic test and writes screenshots and a report
to `data\selftest\`.

## Working with it

| | |
|---|---|
| **Layers** | *Add layer* (the + under the Layers panel, or the Layer menu): background maps, laser terrain (1 m, Lantmäteriet), height colours, a plain colour, map services (WMS / tiles), data files (CSV, GeoJSON, KML/KMZ, GPX, GeoPackage, shapefile) and downloads from ArcGIS services (Skogsstyrelsen's AI-found charcoal kilns, hunting pits, Skog och historia …). Click the eye to hide a layer, the padlock to lock it; drag to reorder; double-click to rename; Delete removes it. Blend mode and opacity are above the list. |
| **Labels and symbols** | live in annotation layers, and each one is listed under its layer – hide it, drag it to another layer, or select it there. |
| **Tools** | **V** select and move · **H** pan (or hold Space) · **T** text · **S** symbol · **L** line · **A** area. Options for the current tool are in the bar along the top. |
| **Properties** | shows what is selected: a label's type style, a symbol, a line, an area, a layer's look (colours, symbols, labels from a data field), or the page. |
| **Page** | *Page* (top right, or Ctrl+L) shows the map on paper. Click the title, legend, scale bar or north arrow to select them, drag to move them, double-click the title to edit it. |
| **Export** | File → Export (Ctrl+E): the page as PDF (vector text and lines) or PNG/JPEG at 150–600 dpi, or the map view as an image. |
| **Selecting features** | The selection bar under the menus works like ArcGIS's *Select by attributes*: pick a data layer, a field, a condition (is, is one of, contains, >, < …) and a value – the list offers the layer's own values, most common first – then **Apply**. The method decides how it combines with the current selection (new, add, remove, within), so “graves in Uppsala” is two steps. Then **Invert**, **Zoom to**, **New layer from selection** (a layer of its own to style) or **Remove from layer**. The **Select features** tool (M) selects with a rectangle; Shift adds, Alt removes. The Select menu has the same commands with shortcuts. |
| **Colour by attribute** | Select a data layer and choose *Colour by* in Properties: **Categories** gives each value of a field its own colour (e.g. one per lämningstyp, the rest as “Other”), **Graduated** makes 2–9 classes of a number field. Untick a class to leave it out, rename it to change its legend text; the legend lists the classes on its own. |
| **Heat maps and clusters** | *Show as* in a data layer's Properties: **Heat map** shows the density of the features (radius, intensity, colours, optional weight field); **Clusters** gathers nearby features into circles with their count. |
| **Attribute table** | View → Attribute table (Ctrl+T) or *Table* in the selection bar: the features as rows. Click a column header to sort, type to find rows, show only the selected features; selecting rows selects the features on the map, double-click zooms to one. |
| **Coordinate grid** | Add layer → Coordinate grid: SWEREF 99 TM in metres or latitude/longitude, as lines, crosses or frame ticks, with labels. |
| **Pictures and logos** | Page → Insert picture or logo (PNG, JPEG or SVG). Drag to move, drag the corner to resize; it is saved inside the map file. |
| **Map series** | Page → Map series: one page per area of a layer (features of a data layer, or areas drawn in an annotation layer), named from a field, fitted or at a fixed scale, with everything outside the area faded – as one multi-page PDF or one file per page. |
| **Design** | The *Design* tab next to Properties sets how the finished map looks: themes (Light, Dark, Parchment, Monochrome, Blueprint, Classic atlas), legend styles (panel, card, no box, strip) and columns, scale-bar and north-arrow styles, frame line, title inside or above the map, fonts and colours. |
| **Undo** | Ctrl+Z / Ctrl+Shift+Z – everything you change can be undone. |

Large data – hundreds of thousands of features, like the whole Swedish heritage register – is
stored by column and drawn in tiles on background threads, so zooming and panning stay smooth.

Maps are saved as `.mapmaker` files (in `Documents\MapMaker\Maps` by default). Imported data is
stored inside the file, so a map can be moved to another computer on its own.

## Map design

The defaults follow Judith Tyner, *Principles of Map Design* (2010):

- **Type**: one sans-serif face for most labels and one serif face; water names in blue italics,
  regions in spaced capitals, landforms in spaced serif capitals; labels on a thin light halo
  instead of boxes; a symbol's name sits above and to the right of it.
- **Hierarchy**: quiet, light background maps by default so your own data and labels stand out;
  terrain shading blends with *Multiply* so it reads as relief rather than a grey veil.
- **Map elements**: a modest, round-numbered scale bar (at most about a fifth of the map width);
  a simple, optional north arrow; a legend without the redundant heading “Legend”; a small
  sources line under the map filled in from the layers you use.
- **Colour**: palettes from ColorBrewer (qualitative and sequential) plus conventional map
  colours – water blue, vegetation green.

## Folders

- `mapmaker/` – the program (`gui/` is the interface; `painter.py` draws maps for both screen and export)
- `data/` – tile cache, logs and self-test output (made on first start)
- `web/` – the browser version (TypeScript); see `web/README.md`
- `installer/`, `build_installer.bat` – builds `dist\MapMaker-Setup-<version>.exe` (PyInstaller + Inno Setup)

## Ideas for later versions

- **Labels along lines** (rivers, roads), leader lines and label priorities.
- **Insets and locator maps**, several map frames on one page, and page templates.
- **Measuring and drawing aids**: distances, areas, snapping, buffers around features.
- **Proportional symbols** (size by a number) and editing attributes in the table.
- **Free text boxes** on the page.
- **Export** to SVG / GeoPackage / GeoJSON, and printing.
