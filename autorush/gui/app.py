"""Point d'entree de l'interface graphique."""

from __future__ import annotations

import logging
import sys
from pathlib import Path


def load_stylesheet() -> str:
    path = Path(__file__).with_name("styles.qss")
    try:
        return path.read_text(encoding="utf-8")
    except OSError:  # pragma: no cover
        return ""


def main() -> int:
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication

    from autorush.gui.main_window import MainWindow
    from autorush.logging_setup import setup_logging
    from autorush.version import APP_NAME, __version__

    setup_logging(logging.INFO)

    QApplication.setAttribute(Qt.ApplicationAttribute.AA_DontUseNativeMenuBar, False)
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(__version__)
    app.setOrganizationName(APP_NAME)
    app.setStyleSheet(load_stylesheet())

    window = MainWindow()
    window.show()

    # un fichier passe en argument est charge directement
    for argument in sys.argv[1:]:
        candidate = Path(argument)
        if candidate.is_file():
            window._set_input(candidate)
            break

    return app.exec()


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
