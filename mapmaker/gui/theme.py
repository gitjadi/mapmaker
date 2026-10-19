"""Dark, neutral theme in the manner of image editors, so the map itself carries the colour.

The whole palette is set explicitly (all roles, all colour groups), so the app
looks the same whatever light/dark setting Windows uses."""
from __future__ import annotations

from pathlib import Path

import numpy as np
from PySide6.QtGui import QColor, QPalette


class T:
    # neutral greys (no blue tint)
    window = "#1f1f1f"
    panel = "#262626"
    base = "#1a1a1a"       # inputs, lists, tables
    alt = "#1d1d1d"
    raised = "#303030"     # buttons
    hover = "#3a3a3a"
    line = "#3a3a3a"
    text = "#e6e6e6"
    muted = "#a3a3a3"
    faint = "#6e6e6e"
    accent = "#3d8ee6"     # clear blue
    accent_hi = "#5aa2ee"
    accent_dim = "#203d5c"
    on_accent = "#ffffff"
    select = "#2f8cff"     # selection outlines on the map
    paste = "#2a2a2a"      # the pasteboard around the page
    map_bg = "#e8e6e1"
    danger = "#ff7b72"


def qc(h: str, a: int = 255) -> QColor:
    c = QColor(h)
    c.setAlpha(a)
    return c


def palette() -> QPalette:
    p = QPalette()
    R = QPalette.ColorRole
    G = QPalette.ColorGroup
    roles = {
        R.Window: T.window, R.WindowText: T.text, R.Base: T.base, R.AlternateBase: T.alt,
        R.ToolTipBase: T.panel, R.ToolTipText: T.text, R.PlaceholderText: T.faint, R.Text: T.text,
        R.Button: T.raised, R.ButtonText: T.text, R.BrightText: "#ffffff", R.Light: T.hover,
        R.Midlight: T.raised, R.Mid: T.line, R.Dark: "#121417", R.Shadow: "#000000",
        R.Highlight: T.accent, R.HighlightedText: T.on_accent, R.Link: T.accent, R.LinkVisited: T.accent,
    }
    for group in (G.Active, G.Inactive):
        for role, col in roles.items():
            p.setColor(group, role, QColor(col))
    for role, col in roles.items():
        p.setColor(G.Disabled, role, QColor(col))
    for role in (R.WindowText, R.Text, R.ButtonText):
        p.setColor(G.Disabled, role, QColor(T.faint))
    p.setColor(G.Disabled, R.Highlight, QColor(T.accent_dim))
    return p


# ------------------------------------------------------------------ texture
TEX_W, TEX_H = 2400, 1600   # large enough that the pattern hardly ever repeats


def _arcs(w, h, cx, cy, pitch, strength):
    """Anti-aliased concentric rings of 1 px around (cx, cy), as 0..strength coverage."""
    ys, xs = np.mgrid[0:h, 0:w].astype(np.float32)
    d = np.hypot(xs - cx, ys - cy)
    m = np.mod(d, pitch)
    dist = np.minimum(m, pitch - m)          # distance to the nearest ring
    return np.clip(1.0 - dist, 0.0, 1.0) * strength


def make_texture(path: Path, ground: str, ink: str = "#e4e4e4", strength: float = 1.0):
    """Engraved ground: two families of fine concentric arcs crossing each other, the way
    old maps were engraved. Centres lie outside the tile, so the arcs read as sweeping curves."""
    from PIL import Image
    w, h = TEX_W, TEX_H
    a = _arcs(w, h, 0.18 * w, -0.20 * h, 6.0, 0.075 * strength)
    b = _arcs(w, h, 0.92 * w, 1.30 * h, 8.0, 0.060 * strength)
    cover = np.clip(a + b, 0, 1)[..., None]
    g = np.array(QColor(ground).getRgb()[:3], dtype=np.float32)
    k = np.array(QColor(ink).getRgb()[:3], dtype=np.float32)
    img = (g * (1 - cover) + k * cover).astype(np.uint8)
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(img, "RGB").save(path, optimize=True)


def textures(cache_dir: Path) -> dict[str, str]:
    """Texture files for the flat surfaces, made once and kept in the cache folder."""
    folder = Path(cache_dir) / "theme"
    out = {}
    for name, ground, strength in (("window", T.window, 0.45), ("panel", T.panel, 0.4)):
        p = folder / f"arcs_{name}_{ground.lstrip('#')}_{int(strength * 100)}.png"
        try:
            if not p.exists():
                make_texture(p, ground, strength=strength)
            out[name] = p.as_posix()
        except Exception:
            pass
    return out


def stylesheet(tex: dict[str, str] | None = None) -> str:
    tex = tex or {}
    win_img = f'background-image: url("{tex["window"]}");' if "window" in tex else ""
    pan_img = f'background-image: url("{tex["panel"]}");' if "panel" in tex else ""
    return f"""
    QWidget {{ color: {T.text}; }}
    QMainWindow, QDialog {{ background-color: {T.window}; {win_img} }}
    QToolTip {{ background: {T.panel}; color: {T.text}; border: 1px solid {T.line}; padding: 4px; }}
    QMenuBar {{ background: transparent; }}
    QMenuBar::item {{ background: transparent; }}
    QMenuBar::item:selected {{ background: {T.hover}; }}
    QMenu {{ background-color: {T.panel}; {pan_img} border: 1px solid {T.line}; }}
    QMenu::item {{ padding: 5px 22px 5px 22px; }}
    QMenu::item:selected {{ background: {T.accent}; color: {T.on_accent}; }}
    QMenu::separator {{ height: 1px; background: {T.line}; margin: 4px 8px; }}
    QMenu::item:disabled {{ color: {T.faint}; }}
    QToolBar {{ background: transparent; spacing: 4px; padding: 3px; border: 0; border-bottom: 1px solid {T.line}; }}
    QToolButton {{ padding: 4px 10px; border-radius: 5px; }}
    QToolButton:hover {{ background: {T.hover}; }}
    QToolButton:checked {{ background: {T.accent_dim}; }}
    QStatusBar {{ background: transparent; border-top: 1px solid {T.line}; }}
    QDockWidget::title {{ padding: 6px 8px; background-color: {T.panel}; {pan_img} font-weight: 600; border-bottom: 1px solid {T.line}; }}
    QScrollArea, QScrollArea > QWidget > QWidget {{ background-color: {T.panel}; {pan_img} }}
    QTabWidget::pane {{ border: 0; background-color: {T.panel}; {pan_img} }}
    QTabWidget > QStackedWidget > QWidget {{ background-color: {T.panel}; {pan_img} }}
    QTabBar::tab {{ background: {T.window}; color: {T.muted}; padding: 6px 14px; border: 0; }}
    QTabBar::tab:selected {{ background: {T.panel}; color: {T.text}; border-bottom: 2px solid {T.accent}; }}
    QPushButton {{ background: {T.raised}; border: 1px solid {T.line}; border-radius: 5px; padding: 4px 10px; }}
    QPushButton:hover {{ border-color: {T.accent}; }}
    QPushButton:checked {{ background: {T.accent_dim}; border-color: {T.accent}; }}
    QPushButton:disabled {{ color: {T.faint}; border-color: {T.line}; }}
    QPushButton[flat="true"] {{ background: transparent; border: 0; color: {T.muted}; }}
    QPushButton[flat="true"]:hover {{ color: {T.accent}; }}
    QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QPlainTextEdit, QTextBrowser, QTextEdit {{
        background: {T.base}; border: 1px solid {T.line}; border-radius: 5px; padding: 3px 6px;
        selection-background-color: {T.accent}; selection-color: {T.on_accent}; }}
    QLineEdit:focus, QComboBox:focus {{ border-color: {T.accent}; }}
    QComboBox QAbstractItemView {{ background: {T.panel}; border: 1px solid {T.line}; selection-background-color: {T.accent}; }}
    QListWidget, QTableView {{ background: {T.base}; alternate-background-color: {T.alt}; border: 1px solid {T.line};
        border-radius: 5px; gridline-color: {T.line}; selection-background-color: {T.accent_dim}; selection-color: {T.text}; }}
    QListWidget::item:hover {{ background: {T.hover}; }}
    QHeaderView::section {{ background: {T.panel}; color: {T.muted}; border: 0; border-right: 1px solid {T.line};
        border-bottom: 1px solid {T.line}; padding: 4px 6px; font-weight: 600; }}
    QTableCornerButton::section {{ background: {T.panel}; border: 0; }}
    QScrollBar:vertical {{ background: transparent; width: 10px; margin: 0; }}
    QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 0; }}
    QScrollBar::handle {{ background: {T.line}; border-radius: 4px; min-height: 24px; min-width: 24px; }}
    QScrollBar::handle:hover {{ background: {T.faint}; }}
    QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
    QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
    QSlider::groove:horizontal {{ height: 4px; background: {T.line}; border-radius: 2px; }}
    QSlider::sub-page:horizontal {{ background: {T.accent}; border-radius: 2px; }}
    QSlider::handle:horizontal {{ background: {T.text}; width: 14px; height: 14px; margin: -6px 0; border-radius: 7px; }}
    QGroupBox {{ border: 1px solid {T.line}; border-radius: 6px; margin-top: 10px; padding-top: 8px; }}
    QGroupBox::title {{ subcontrol-origin: margin; left: 8px; color: {T.muted}; }}
    QProgressBar {{ background: {T.base}; border: 1px solid {T.line}; border-radius: 5px; text-align: center; }}
    QProgressBar::chunk {{ background: {T.accent}; border-radius: 4px; }}
    QSplitter::handle {{ background: {T.line}; }}
    QSpinBox:focus, QDoubleSpinBox:focus, QPlainTextEdit:focus {{ border-color: {T.accent}; }}
    QLineEdit:disabled, QComboBox:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled {{
        color: {T.faint}; background: {T.alt}; border-color: {T.alt}; }}
    QCheckBox:disabled, QRadioButton:disabled {{ color: {T.faint}; }}
    QPushButton[primary="true"] {{ background: {T.accent}; color: {T.on_accent}; border: 1px solid {T.accent};
        font-weight: 600; padding: 6px 16px; }}
    QPushButton[primary="true"]:hover {{ background: {T.accent_hi}; border-color: {T.accent_hi}; }}
    QPushButton[primary="true"]:disabled {{ background: {T.accent_dim}; color: {T.faint}; border-color: {T.accent_dim}; }}
    QPushButton[link="true"] {{ background: transparent; border: 0; color: {T.accent}; padding: 2px 4px; }}
    QPushButton[link="true"]:hover {{ text-decoration: underline; }}
    QPushButton[link="true"]:disabled {{ color: {T.faint}; }}
    QPushButton[seg="first"], QPushButton[seg="mid"], QPushButton[seg="last"] {{ background: {T.base}; color: {T.muted};
        border: 1px solid {T.line}; border-radius: 0; padding: 5px 14px; }}
    QPushButton[seg="first"] {{ border-top-left-radius: 6px; border-bottom-left-radius: 6px; }}
    QPushButton[seg="last"] {{ border-top-right-radius: 6px; border-bottom-right-radius: 6px; border-left: 0; }}
    QPushButton[seg="mid"] {{ border-left: 0; }}
    QPushButton[seg="first"]:hover, QPushButton[seg="mid"]:hover, QPushButton[seg="last"]:hover {{ color: {T.text}; }}
    QPushButton[seg="first"]:checked, QPushButton[seg="mid"]:checked, QPushButton[seg="last"]:checked {{
        background: {T.accent_dim}; color: {T.text}; border-color: {T.accent}; }}
    QFrame#section {{ border: 0; border-top: 1px solid {T.line}; background: transparent; }}
    QFrame#sectionHead {{ border: 0; border-radius: 4px; background: transparent; }}
    QFrame#sectionHead:hover {{ background: {T.hover}; }}
    QFrame#sectionHead:focus {{ background: {T.hover}; }}
    QWidget#sectionBody {{ background: transparent; }}
    QFrame#card, QFrame#tile {{ background: {T.base}; border: 1px solid {T.line}; border-radius: 6px; }}
    QFrame#card QLabel, QFrame#tile QLabel {{ background: transparent; border: 0; }}
    QFrame#footer {{ background: {T.panel}; {pan_img} border: 0; border-top: 1px solid {T.line}; }}
    QLabel#group {{ color: {T.muted}; font-size: 11px; font-weight: 600; margin-top: 4px; }}
    QLabel#hint {{ color: {T.muted}; }}
    QToolBar#tools {{ background: {T.panel}; border: 0; border-right: 1px solid {T.line}; padding: 6px 4px; spacing: 2px; }}
    QToolBar#tools QToolButton {{ padding: 6px; border-radius: 6px; }}
    QToolBar#tools QToolButton:checked {{ background: {T.accent}; }}
    QToolBar#selbar {{ background: {T.window}; border: 0; border-bottom: 1px solid {T.line}; padding: 4px 8px; spacing: 6px; }}
    QToolBar#selbar QLabel {{ color: {T.muted}; padding: 0 2px; }}
    QToolBar#options {{ background: {T.panel}; border: 0; border-bottom: 1px solid {T.line}; padding: 4px 8px; spacing: 6px; }}
    QToolBar#options QLabel {{ color: {T.muted}; padding: 0 2px 0 8px; }}
    QTreeWidget {{ background: {T.base}; border: 0; outline: 0; }}
    QTreeWidget::item {{ padding: 3px 0; border-bottom: 1px solid {T.alt}; }}
    QTreeWidget::item:selected {{ background: {T.accent_dim}; color: {T.text}; }}
    QTreeWidget::item:hover:!selected {{ background: {T.hover}; }}
    QFrame#panelHead {{ background: {T.panel}; border: 0; border-bottom: 1px solid {T.line}; }}
    QFrame#panelHead QLabel {{ font-weight: 600; }}
    QFrame#panelFoot {{ background: {T.panel}; border: 0; border-top: 1px solid {T.line}; }}
    QFrame#panelFoot QToolButton {{ padding: 3px; border-radius: 4px; }}
    QWidget#props {{ background: {T.panel}; }}
    QToolButton#swatch {{ padding: 2px; border: 1px solid {T.line}; border-radius: 4px; background: {T.base}; }}
    QToolButton#swatch:hover {{ border-color: {T.accent}; }}
    QToolButton::menu-indicator {{ image: none; width: 0; }}
    """


def detail_css() -> str:
    return (f"td {{ padding: 3px 4px; vertical-align: top; border-top: 1px solid {T.line}; color: {T.text}; }}"
            f"td.k {{ color: {T.muted}; width: 38%; }} a {{ color: {T.accent}; }} body {{ color: {T.text}; }}")
