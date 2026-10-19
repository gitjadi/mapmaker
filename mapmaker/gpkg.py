"""Reading the structure of GeoPackage files (feature layers, columns, coordinate system)."""
from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

_log = logging.getLogger("mapmaker")


def log(msg):
    _log.info(msg)


def _affinity(decl: str) -> str:
    d = (decl or "").upper()
    if "INT" in d or d in ("BOOLEAN",):
        return "INTEGER"
    if any(k in d for k in ("REAL", "FLOA", "DOUB", "NUMERIC", "DECIMAL")):
        return "REAL"
    if "BLOB" in d:
        return "BLOB"
    return "TEXT"


def discover(files: list[Path]):
    """Read the structure of every feature layer in every file."""
    layers = []
    for f in files:
        con = sqlite3.connect(f"file:{f.as_posix()}?mode=ro", uri=True)
        try:
            rows = con.execute(
                "SELECT c.table_name, g.column_name, g.srs_id, g.geometry_type_name "
                "FROM gpkg_contents c JOIN gpkg_geometry_columns g ON g.table_name = c.table_name "
                "WHERE c.data_type = 'features'").fetchall()
        except sqlite3.DatabaseError as e:
            log(f"  ! {f.name}: not a GeoPackage ({e})")
            continue
        for table, gcol, srs, gtype in rows:
            info = con.execute(f'PRAGMA table_info("{table}")').fetchall()
            # Feature id: the primary key if there is one. Views (and some exporters) have
            # none, so fall back to a typical id column, then to SQLite's rowid, then to
            # a simple running number.
            pk = next((c[1] for c in info if c[5] == 1 and _affinity(c[2]) == "INTEGER"), None)
            fid_expr = f'"{pk}"' if pk else None
            if not pk:
                idcol = next((c[1] for c in info if c[1].lower() in ("fid", "ogc_fid", "objectid", "gid", "id")), None)
                if idcol:
                    fid_expr = f'"{idcol}"'
                else:
                    try:
                        con.execute(f'SELECT rowid FROM "{table}" LIMIT 1').fetchall()
                        fid_expr = "rowid"
                    except sqlite3.OperationalError:
                        fid_expr = "NULL"
            kind = con.execute("SELECT type FROM sqlite_master WHERE name = ?", (table,)).fetchone()
            cols = [(c[1], _affinity(c[2])) for c in info if c[1] != gcol and c[1] != pk]
            srsdef = con.execute(
                "SELECT srs_name, srs_id, organization, organization_coordsys_id, definition, description "
                "FROM gpkg_spatial_ref_sys WHERE srs_id = ?", (srs,)).fetchone()
            n = con.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
            layers.append(dict(file=f, table=table, geom=gcol, pk=pk, fid=fid_expr, srs=srs, gtype=gtype,
                               cols=cols, srsdef=srsdef, n=n))
            log(f"  {f.name} / {table}: {n:,} features, {gtype}, SRS {srs}, {len(cols)} attribute columns"
                f"{' (view)' if kind and kind[0] == 'view' else ''}, id: {fid_expr}")
        con.close()
    return layers


def resolve_epsg(srsdef, srs_id) -> int | None:
    if srsdef:
        org, code = srsdef[2], srsdef[3]
        if org and str(org).upper() == "EPSG" and code and code > 0:
            return int(code)
    return srs_id if srs_id and srs_id > 0 else None
