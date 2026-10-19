"""Map symbols: small solid glyphs on a 24 x 24 grid, drawn as SVG so they stay sharp at any
size and print resolution. Each symbol has an anchor – the point that sits on the map
location (the centre for most, the tip for a pin, the foot of a flag pole).

Following map-design practice, symbols are simple and solid: pictorial enough to be
recognised without the legend, plain enough not to dominate the map."""
from __future__ import annotations

# key: (label, category, anchor (x, y) on the 24 grid, SVG elements – no fill set, so the group colour applies)
SYMBOLS: dict[str, tuple[str, str, tuple[float, float], str]] = {
    # --- shapes
    "circle": ("Circle", "Shapes", (12, 12), '<circle cx="12" cy="12" r="8"/>'),
    "ring": ("Ring", "Shapes", (12, 12),
             '<path fill-rule="evenodd" d="M12 3a9 9 0 1 1 0 18 9 9 0 0 1 0-18zm0 3.4a5.6 5.6 0 1 0 0 11.2 5.6 5.6 0 0 0 0-11.2z"/>'),
    "square": ("Square", "Shapes", (12, 12), '<rect x="4.5" y="4.5" width="15" height="15" rx="1.2"/>'),
    "triangle": ("Triangle", "Shapes", (12, 13), '<path d="M12 3.2 21.2 19.8H2.8z"/>'),
    "diamond": ("Diamond", "Shapes", (12, 12), '<path d="M12 2.5 21.5 12 12 21.5 2.5 12z"/>'),
    "star": ("Star", "Shapes", (12, 12),
             '<path d="M12 2.3l2.95 6.2 6.75.8-5 4.65 1.35 6.75L12 17.35 5.95 20.7 7.3 13.95l-5-4.65 6.75-.8z"/>'),
    "cross": ("Cross", "Shapes", (12, 12),
              '<path d="M5.6 3.4 12 9.8l6.4-6.4 2.2 2.2-6.4 6.4 6.4 6.4-2.2 2.2-6.4-6.4-6.4 6.4-2.2-2.2 6.4-6.4-6.4-6.4z"/>'),
    "arrow": ("Arrow", "Shapes", (12, 12), '<path d="M12 2 20.5 12.5H15V22H9v-9.5H3.5z"/>'),
    # --- places
    "pin": ("Pin", "Places", (12, 22.2),
            '<path fill-rule="evenodd" d="M12 1.8a7.2 7.2 0 0 0-7.2 7.2c0 5.4 7.2 13.2 7.2 13.2s7.2-7.8 7.2-13.2A7.2 7.2 0 0 0 '
            '12 1.8zm0 4.3a2.9 2.9 0 1 1 0 5.8 2.9 2.9 0 0 1 0-5.8z"/>'),
    "flag": ("Flag", "Places", (5.9, 21.8), '<path d="M5 2.5h1.8v19.3H5z"/><path d="M7.6 3.4h11.6l-2.7 4.1 2.7 4.1H7.6z"/>'),
    "house": ("House", "Places", (12, 13), '<path d="M12 2.8 1.8 11.3h2.7v9.9h5.6v-6.3h3.8v6.3h5.6v-9.9h2.7z"/>'),
    "church": ("Church", "Places", (12, 13),
               '<path d="M11 1.3h2v2.6h2.6v2H13v2.3l5.2 3.4v9.6h-4.6v-4.4a1.6 1.6 0 0 0-3.2 0v4.4H5.8v-9.6L11 8.2V5.9H8.4v-2H11z"/>'),
    "castle": ("Castle / fort", "Places", (12, 13),
               '<path d="M2.8 21.2V7.6h2.7v2.6h2.1V7.6h2.7v2.6h3.4V7.6h2.7v2.6h2.1V7.6h2.7v13.6h-6.5v-4.1a2.7 2.7 0 0 0-5.4 0v4.1z"/>'),
    "tent": ("Camp site", "Places", (12, 13),
             '<path fill-rule="evenodd" d="M10.6 2.4h2.8l-.6 1.4 9.4 17.4H1.8l9.4-17.4zM12 11.2l-4.2 8h8.4z"/>'),
    "parking": ("Parking", "Places", (12, 12),
                '<path fill-rule="evenodd" d="M4 2.5h16A1.5 1.5 0 0 1 21.5 4v16a1.5 1.5 0 0 1-1.5 1.5H4A1.5 1.5 0 0 1 2.5 20V4A1.5 '
                '1.5 0 0 1 4 2.5zM8.6 6.4V18h2.8v-3.9h2.2a3.85 3.85 0 0 0 0-7.7zm2.8 2.4h2.1a1.5 1.5 0 0 1 0 3h-2.1z"/>'),
    "boat": ("Harbour / boat", "Places", (12, 14),
             '<path d="M2.5 15.2h19l-2.8 5.6H5.3z"/><path d="M12 2.6h1.3l6 10.6H12z"/><path d="M10.7 5.6v7.6H5.2z"/>'),
    "camera": ("Photo spot", "Places", (12, 12),
               '<path fill-rule="evenodd" d="M8.4 4.4h7.2l1.6 2.6H20a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V9a2 2 0 0 '
               '1 2-2h2.8zM12 9.3a4.3 4.3 0 1 0 0 8.6 4.3 4.3 0 0 0 0-8.6z"/>'),
    "eye": ("Viewpoint", "Places", (12, 12),
            '<path fill-rule="evenodd" d="M12 4.8C6.4 4.8 2.4 9.4.8 12c1.6 2.6 5.6 7.2 11.2 7.2S21.6 14.6 23.2 12C21.6 9.4 '
            '17.6 4.8 12 4.8zm0 3a4.2 4.2 0 1 1 0 8.4 4.2 4.2 0 0 1 0-8.4z"/><circle cx="12" cy="12" r="1.9"/>'),
    "walk": ("Trail / hiking", "Places", (12, 12),
             '<circle cx="13.6" cy="3.6" r="2.1"/><path d="M12.4 6.9 8.7 8.7 7.2 13l2 .7 1.2-3 1-.5-1.4 5.7-2.9 4.8 2.1 '
             '1.3 3.1-5.1 1.8 2.1V22h2.4v-5.1l-2.5-2.8.8-3.4 1.3 1.8 3.1.9.6-2.2-2.3-.7-2.1-3.3c-.6-1-1.7-1.4-2.8-1.2z"/>'),
    "info": ("Information", "Places", (12, 12),
             '<path fill-rule="evenodd" d="M12 2a10 10 0 1 1 0 20 10 10 0 0 1 0-20zm-1.35 8h2.7v8.2h-2.7zM12 5.4a1.65 1.65 '
             '0 1 1 0 3.3 1.65 1.65 0 0 1 0-3.3z"/>'),
    "warning": ("Warning", "Places", (12, 13),
                '<path fill-rule="evenodd" d="M12 2.4 22.8 20.8H1.2zm-1.15 6.4h2.3l-.35 6.4h-1.6zM12 16.4a1.35 1.35 0 1 1 0 '
                '2.7 1.35 1.35 0 0 1 0-2.7z"/>'),
    # --- nature
    "peak": ("Summit", "Nature", (12, 14), '<path d="M1.4 20.4 9 5.8l3.6 6.2 2.6-3.6 7.4 12z"/>'),
    "conifer": ("Tree (conifer)", "Nature", (12, 21.8),
                '<path d="M12 1.8 5.8 10h2.6L4.8 15.6H10v6.2h4v-6.2h5.2L15.6 10h2.6z"/>'),
    "tree": ("Tree (broadleaf)", "Nature", (12, 21.8),
             '<circle cx="12" cy="9" r="6.8"/><rect x="10.8" y="13" width="2.4" height="8.8" rx="1"/>'),
    "water": ("Spring / water", "Nature", (12, 13),
              '<path d="M12 2.4S4.8 10.5 4.8 15.2a7.2 7.2 0 0 0 14.4 0C19.2 10.5 12 2.4 12 2.4z"/>'),
    "cave": ("Cave", "Nature", (12, 14),
             '<path fill-rule="evenodd" d="M1.6 21.2 7.4 7.4 11 4.8l3.6 2.1 7.8 14.3zm7-0V17a3.4 3.4 0 0 1 6.8 0v4.2z"/>'),
    # --- heritage
    "mound": ("Grave mound", "Heritage", (12, 16),
              '<path d="M1.8 18.8C4.2 12.9 7.8 9.6 12 9.6s7.8 3.3 10.2 9.2z"/><rect x="1.2" y="19.2" width="21.6" height="1.9" rx=".95"/>'),
    "stone": ("Standing stone", "Heritage", (12, 20),
              '<path d="M10 3.2c2.5-.7 4.4.6 4.8 3.1l1.5 12.6H8.2l.6-12.8c.1-1.4.5-2.6 1.2-2.9z"/>'
              '<rect x="4.5" y="19.3" width="15" height="1.9" rx=".95"/>'),
    "runestone": ("Rune stone", "Heritage", (12, 20),
                  '<path fill-rule="evenodd" d="M7.8 19.2 6.9 8.6C6.6 4.8 9 2.4 12 2.4s5.4 2.4 5.1 6.2l-.9 10.6zM12 5.2a3.9 3.9 '
                  '0 0 0-3.9 4.1l.6 7.6h1.4l-.6-7.6a2.5 2.5 0 0 1 5 0l-.6 7.6h1.4l.6-7.6A3.9 3.9 0 0 0 12 5.2z"/>'
                  '<rect x="4.5" y="19.6" width="15" height="1.9" rx=".95"/>'),
    "cairn": ("Cairn / stone setting", "Heritage", (12, 17),
              '<circle cx="7.2" cy="17.4" r="3.6"/><circle cx="15.2" cy="17.6" r="3.4"/><circle cx="11" cy="11.3" r="3.1"/>'
              '<circle cx="12.6" cy="5.6" r="2.4"/>'),
    "ruin": ("Ruin", "Heritage", (12, 15),
             '<path d="M3.2 21V9.2l2.4-1.5 1.2 1.6 2.6-1.8V21H7.2v-4.6a1.8 1.8 0 0 0-3.6 0V21z"/>'
             '<path d="M12.4 21v-8.7l2.1-1.1 1.6 1.4 1.7-1.3 2.9 1.9V21h-2.2v-3.6a1.9 1.9 0 0 0-3.8 0V21z"/>'),
    "kiln": ("Charcoal kiln", "Heritage", (12, 12),
             '<path fill-rule="evenodd" d="M12 2.5a9.5 9.5 0 1 1 0 19 9.5 9.5 0 0 1 0-19zm0 2.6a6.9 6.9 0 1 0 0 13.8 6.9 '
             '6.9 0 0 0 0-13.8z"/><circle cx="12" cy="12" r="4.3"/>'),
    "pit": ("Pit / hunting pit", "Heritage", (12, 12),
            '<path fill-rule="evenodd" d="M12 3.5c5.2 0 9.4 3.8 9.4 8.5s-4.2 8.5-9.4 8.5S2.6 16.7 2.6 12 6.8 3.5 12 3.5zm0 '
            '3.4c-3.1 0-5.6 2.3-5.6 5.1s2.5 5.1 5.6 5.1 5.6-2.3 5.6-5.1-2.5-5.1-5.6-5.1z"/>'
            '<path d="M12 8.6c1.9 0 3.4 1.5 3.4 3.4h-6.8c0-1.9 1.5-3.4 3.4-3.4z"/>'),
}
CATEGORIES = ["Shapes", "Places", "Nature", "Heritage"]


def svg(key: str, color: str = "#000000", halo: str | None = None, halo_width: float = 2.6) -> bytes:
    """The symbol as an SVG document in a colour, optionally with a halo (outline) in another colour."""
    _label, _cat, _anchor, body = SYMBOLS.get(key, SYMBOLS["circle"])
    parts = ['<svg xmlns="http://www.w3.org/2000/svg" viewBox="-2 -2 28 28" width="28" height="28">']
    if halo:
        parts.append(f'<g fill="{halo}" stroke="{halo}" stroke-width="{halo_width:.2f}" stroke-linejoin="round">{body}</g>')
    parts.append(f'<g fill="{color}">{body}</g></svg>')
    return "".join(parts).encode("utf-8")


def anchor(key: str) -> tuple[float, float]:
    """Anchor as a fraction of the 28 x 28 drawing box (which has a 2-unit margin around the 24 grid)."""
    ax, ay = SYMBOLS.get(key, SYMBOLS["circle"])[2]
    return (ax + 2) / 28.0, (ay + 2) / 28.0


def label(key: str) -> str:
    return SYMBOLS.get(key, SYMBOLS["circle"])[0]


def by_category() -> list[tuple[str, list[str]]]:
    return [(c, [k for k, v in SYMBOLS.items() if v[1] == c]) for c in CATEGORIES]


__all__ = ["SYMBOLS", "CATEGORIES", "svg", "anchor", "label", "by_category"]
