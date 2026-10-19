"""Small reusable pieces of interface: the mouse-wheel guard, collapsible
sections, labels that shorten themselves, a two-way switch and number tiles."""
from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, QSettings, Qt, Signal
from PySide6.QtWidgets import (QAbstractSpinBox, QButtonGroup, QComboBox, QFrame, QHBoxLayout, QLabel, QPushButton,
                               QSizePolicy, QSlider, QTabBar, QVBoxLayout, QWidget)

from .theme import T


# ---------------------------------------------------------------------- wheel guard
class WheelGuard(QObject):
    """Installed on the application: the mouse wheel scrolls the panel under the pointer
    instead of changing a dropdown, slider or tab by accident. Spin boxes still react to
    the wheel after you have clicked into them."""

    ALWAYS = (QComboBox, QSlider, QTabBar)
    GUARDED = (QComboBox, QSlider, QAbstractSpinBox)

    def eventFilter(self, obj, ev):
        t = ev.type()
        if t == QEvent.Type.Wheel:
            if isinstance(obj, self.ALWAYS) or (isinstance(obj, QAbstractSpinBox) and not obj.hasFocus()):
                ev.ignore()          # Qt then offers the event to the parent widgets, e.g. the scroll area
                return True
        elif t == QEvent.Type.Polish and isinstance(obj, self.GUARDED):
            # without this the wheel itself would give the widget focus before the check above
            obj.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        return False


def install_wheel_guard(app) -> WheelGuard:
    g = WheelGuard(app)
    app.installEventFilter(g)
    return g


# ---------------------------------------------------------------------- labels
class ElidedLabel(QLabel):
    """One line of text that is shortened with … when there is not room for all of it
    (the whole text is in the tooltip). It never makes its panel wider."""

    def __init__(self, text: str = "", parent=None):
        super().__init__(parent)
        self._full = ""
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.setMinimumWidth(8)
        self.set_text(text)

    def set_text(self, text: str):
        self._full = text or ""
        self.setToolTip(self._full if len(self._full) > 24 else "")
        self._elide()

    def full_text(self) -> str:
        return self._full

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        self._elide()

    def _elide(self):
        w = max(0, self.width() - 2)
        QLabel.setText(self, self.fontMetrics().elidedText(self._full, Qt.TextElideMode.ElideRight, w))


def shrinkable(w):
    """Let a check box or label be narrower than its text (the text is cut off and shown in full
    in the tooltip) instead of forcing the whole panel wider."""
    w.setSizePolicy(QSizePolicy.Policy.Ignored, w.sizePolicy().verticalPolicy())
    w.setMinimumWidth(0)
    if not w.toolTip() and hasattr(w, "text"):
        w.setToolTip(w.text())
    return w


# ---------------------------------------------------------------------- collapsible section
class _Header(QFrame):
    clicked = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("sectionHead")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)

    def mouseReleaseEvent(self, ev):
        if ev.button() == Qt.MouseButton.LeftButton and self.rect().contains(ev.position().toPoint()):
            self.clicked.emit()
        super().mouseReleaseEvent(ev)

    def keyPressEvent(self, ev):
        if ev.key() in (Qt.Key.Key_Space, Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.clicked.emit()
            return
        super().keyPressEvent(ev)


class Section(QFrame):
    """A block that folds open and shut. The header shows the title and a one-line summary
    of the settings inside (amber when the block switches something on), so a closed
    section still tells you what it does. Open/closed is remembered under `key`."""

    toggled = Signal(bool)

    def __init__(self, title: str, key: str | None = None, open_: bool = False, parent=None):
        super().__init__(parent)
        self.setObjectName("section")
        self.key = key
        if key is not None:
            v = QSettings().value(f"sections/{key}")
            if v is not None:
                open_ = str(v).lower() == "true"
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self.head = _Header()
        hl = QHBoxLayout(self.head)
        hl.setContentsMargins(6, 7, 8, 7)
        hl.setSpacing(6)
        self.arrow = QLabel()
        self.arrow.setFixedWidth(12)
        self.arrow.setStyleSheet(f"color:{T.muted};")
        self.title = QLabel(title)
        self.title.setStyleSheet("font-weight:600;")
        self.summary = ElidedLabel()
        self.summary.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        for w in (self.arrow, self.title, self.summary):
            w.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        hl.addWidget(self.arrow)
        hl.addWidget(self.title)
        hl.addWidget(self.summary, 1)
        lay.addWidget(self.head)
        self.body = QWidget()
        self.body.setObjectName("sectionBody")
        self.lay = QVBoxLayout(self.body)
        self.lay.setContentsMargins(24, 2, 8, 12)
        self.lay.setSpacing(6)
        lay.addWidget(self.body)
        self.head.clicked.connect(lambda: self.set_open(not self.is_open()))
        self._open = None
        self.set_open(open_, remember=False)
        self.set_summary("")

    # -- content
    def addWidget(self, w, stretch: int = 0):
        self.lay.addWidget(w, stretch)
        return w

    def addLayout(self, layout):
        self.lay.addLayout(layout)
        return layout

    # -- state
    def is_open(self) -> bool:
        return bool(self._open)

    def set_open(self, on: bool, remember: bool = True):
        on = bool(on)
        if on == self._open:
            return
        self._open = on
        self.body.setVisible(on)
        self.arrow.setText("▾" if on else "▸")
        if remember and self.key is not None:
            QSettings().setValue(f"sections/{self.key}", on)
        self.toggled.emit(on)

    def set_summary(self, text: str, active: bool = False):
        self.summary.set_text(text)
        self.summary.setStyleSheet(f"color:{T.accent if active else T.muted};")


# ---------------------------------------------------------------------- switch
class Switch(QWidget):
    """Two or more buttons joined into one control, one of them chosen (a segmented control)."""

    changed = Signal(int)

    def __init__(self, labels: list[str], parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        self.buttons = []
        for i, text in enumerate(labels):
            b = QPushButton(text)
            b.setCheckable(True)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setProperty("seg", "first" if i == 0 else "last" if i == len(labels) - 1 else "mid")
            self.group.addButton(b, i)
            lay.addWidget(b)
            self.buttons.append(b)
        self.buttons[0].setChecked(True)
        self.group.idClicked.connect(self.changed.emit)

    def index(self) -> int:
        return self.group.checkedId()

    def set_index(self, i: int):
        b = self.group.button(i)
        if b is not None:
            b.setChecked(True)

    def set_label(self, i: int, text: str):
        self.buttons[i].setText(text)


# ---------------------------------------------------------------------- number tiles
class StatTile(QFrame):
    """A headline number with a short caption under it."""

    def __init__(self, caption: str = "", parent=None):
        super().__init__(parent)
        self.setObjectName("tile")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 7, 10, 8)
        lay.setSpacing(1)
        self.value = QLabel("–")
        self.value.setStyleSheet("font-size:18px; font-weight:600;")
        self.caption = QLabel(caption)
        self.caption.setWordWrap(True)
        self.caption.setStyleSheet(f"color:{T.muted}; font-size:11px;")
        lay.addWidget(self.value)
        lay.addWidget(self.caption)
        lay.addStretch(1)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)

    def set(self, value: str, caption: str | None = None, color: str | None = None, tip: str = ""):
        self.value.setText(value)
        self.value.setStyleSheet(f"font-size:18px; font-weight:600; color:{color or T.text};")
        if caption is not None:
            self.caption.setText(caption)
        self.setToolTip(tip)


def link_button(text: str, tip: str = "") -> QPushButton:
    """A small flat button that looks like a link."""
    b = QPushButton(text)
    b.setFlat(True)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    b.setProperty("link", True)
    if tip:
        b.setToolTip(tip)
    return b


def primary(b: QPushButton) -> QPushButton:
    """Style a button as the main action of its panel."""
    b.setProperty("primary", True)
    return b


__all__ = ["WheelGuard", "install_wheel_guard", "ElidedLabel", "shrinkable", "Section", "Switch", "StatTile",
           "link_button", "primary"]
