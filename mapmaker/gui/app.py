"""Starting MapMaker: the application, its look, and the main window."""
from __future__ import annotations

import logging
import sys
import traceback


def run(argv: list[str]) -> int:
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QFont, QIcon
    from PySide6.QtWidgets import QApplication, QMessageBox

    from ..paths import setup_logging
    from .theme import palette, stylesheet
    from .widgets import install_wheel_guard

    log_path = setup_logging()
    log = logging.getLogger("mapmaker")
    QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication.instance() or QApplication(argv)
    app.setApplicationName("MapMaker")
    app.setOrganizationName("MapMaker")
    app.setStyle("Fusion")
    app.setPalette(palette())
    app.setStyleSheet(stylesheet())
    f = QFont("Segoe UI")
    f.setPointSizeF(9.0)
    app.setFont(f)
    from pathlib import Path
    ic = Path(__file__).with_name("icon.png")
    if ic.exists():
        app.setWindowIcon(QIcon(str(ic)))
    app._wheel_guard = install_wheel_guard(app)

    def excepthook(t, v, tb):
        text = "".join(traceback.format_exception(t, v, tb))
        log.error("Unhandled error:\n%s", text)
        try:
            QMessageBox.warning(None, "MapMaker – something went wrong",
                                f"{v}\n\nThe details are in the log:\n{log_path}")
        except Exception:
            pass
    sys.excepthook = excepthook

    if "--selftest" in argv:
        from .selftest import run_selftest
        return run_selftest(app)

    from ..document import SUFFIX
    from .main_window import MainWindow
    win = MainWindow()
    win.show()
    files = [a for a in argv[1:] if a.lower().endswith(SUFFIX)]
    if files:
        win.open_map(files[0])
    log.info("MapMaker started")
    return app.exec()
