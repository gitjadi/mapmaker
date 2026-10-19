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

**Next:** data import (GeoJSON, CSV, KML, GPX, GeoPackage, shapefile) and data styling → page layout
(title, legend, scale bar, north arrow, sources), themes and PDF export → laser terrain, grid, ArcGIS
downloads.

## Publishing on your own domain

**GitHub Pages (free):** the workflow `.github/workflows/pages.yml` builds and publishes `web/` on
every push to `main`.

1. Move `web/deploy/pages.yml` to `.github/workflows/pages.yml` (create the folders).
   On GitHub: repository **Settings → Pages → Source: GitHub Actions**.
2. Push. The site appears at `https://<user>.github.io/<repo>/`.
3. Own domain: create `web/public/CNAME` containing just the domain (e.g. `maps.example.se`), push,
   and enter the same domain under **Settings → Pages → Custom domain** (tick *Enforce HTTPS*).
4. At your DNS provider:
   - a subdomain (`maps.example.se`): a **CNAME** record `maps` → `<user>.github.io`
   - the bare domain (`example.se`): **A** records to `185.199.108.153`, `185.199.109.153`,
     `185.199.110.153`, `185.199.111.153`

**Any other web host:** run `npm run build` and upload the contents of `web/dist/` – the paths are
relative, so it also works in a sub-folder such as `example.se/mapmaker/`.

## Notes for a public site

- Background maps come from Esri, OpenStreetMap and OpenTopoMap tile servers. OpenStreetMap's own
  servers don't allow heavy use from public websites; for real traffic, use a tile provider with an
  API key (MapTiler, Stadia, Esri's location platform) – they can be added as map services.
- Maps are kept in each visitor's own browser (IndexedDB) and in files they save. Shared or cloud
  maps would need a server and accounts – a possible later step.
