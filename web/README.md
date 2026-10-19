# MapMaker in the browser

The browser version of MapMaker: a static web app (TypeScript, no runtime libraries) that runs entirely
in the browser, so it can be hosted on any web server or on GitHub Pages. It uses the same `.mapmaker`
file format as the desktop app, so maps open in both. Layers the browser can't draw yet (data, terrain,
grid) are kept and saved back untouched.

## Working on it

Needs Node.js 18 or newer.

```
cd web
npm install
npm run dev        # http://localhost:5173 – reloads as you edit
npm run build      # the finished site in web/dist
```

`src/shared/` (symbols, text styles, colours, themes, background maps) is generated from the desktop
app's Python modules so the two stay the same. After changing those in `mapmaker/`, run
`python web/tools/gen_shared.py` from the repository root (with the desktop app's `.venv`).

## Status

**Phase 1 (done):** map canvas with pan/zoom (mouse, wheel, touch pinch), background maps, plain colour
and map-service (XYZ/WMS) layers, Layers panel (show/hide, lock, rename, drag to reorder, opacity,
blend mode), annotation layers with Text, Symbol, Line and Area tools, Properties panel, undo/redo,
autosave in the browser, Open/Save `.mapmaker`, PNG/JPEG export of the map view.

**Phase 2 (done):** data layers – desktop maps' data shows and saves back; import GeoPackage (read
with a built-in SQLite reader, several tables at once), GeoJSON, CSV (lat/lon or SWEREF 99 TM / RT 90
N/E), KML/KMZ, GPX and shapefiles via *Layer → Data from a file…* or by dropping files on the map;
point, line and area styles, colour by categories or graduated classes, labels from a field, heat
maps and clusters; click a feature to see its attributes.

**Selecting (the Select menu):** select by attributes (field, condition, value, and new / add /
remove / within), the rectangle tool (M), select all, invert, clear, zoom to selected, new layer from
selection, remove selected features. **Attribute table** (Ctrl+T): sort, find, only selected, click
rows to select features, double-click to zoom.

**Phase 3 (done):** the page – *Page* (top right, Ctrl+L) shows the map on paper with title,
subtitle, legend, scale bar, north arrow, sources line and pictures/logos; drag elements to move them,
click them to change them, design themes, paper size and orientation. **Export** (Ctrl+E): the page as
PDF, PNG or JPEG at 150–600 dpi, or the map view as a picture. (The PDF holds the page as one
high-resolution picture, not vector text.)

**Next:** laser terrain and hillshade, coordinate grid, ArcGIS service downloads, map series,
vector PDF.

## Publishing on your own domain (jadi.zip)

The site is published with GitHub Pages straight from the repository – no build server needed:

1. `cd web`, `npm install` (once), then **`npm run site`**. This writes the finished app to the
   repository root: `index.html`, `assets/`, `icon.png` and `.nojekyll` (which stops GitHub from
   turning `README.md` into the home page).
2. Commit and push (GitHub Desktop: commit to `main`, *Push origin*). A minute later the app is
   live at https://jadi.zip.

GitHub Pages settings for this: **Settings → Pages → Build and deployment → Source: Deploy from a
branch**, branch **main**, folder **/ (root)**, custom domain **jadi.zip** (the `CNAME` file at the
root keeps it). Run `npm run site` again whenever the code in `web/` changes.

*Alternative:* `web/deploy/pages.yml` is a GitHub Actions workflow that builds on every push
instead. To use it, move it to `.github/workflows/pages.yml` and set the Pages source to *GitHub
Actions*; then you don't need to commit the built files.

**Any other web host:** `npm run build` and upload the contents of `web/dist/` – the paths are
relative, so it also works in a sub-folder.

## Notes for a public site

- Background maps come from Esri, OpenStreetMap and OpenTopoMap tile servers. OpenStreetMap's own
  servers don't allow heavy use from public websites; for real traffic, use a tile provider with an
  API key (MapTiler, Stadia, Esri's location platform) – they can be added as map services.
- Maps are kept in each visitor's own browser (IndexedDB) and in files they save. Shared or cloud
  maps would need a server and accounts – a possible later step.
