"""Point d'entree de l'executable graphique (PyInstaller)."""

import multiprocessing
import sys

if __name__ == "__main__":
    multiprocessing.freeze_support()
    from autorush.gui.app import main

    sys.exit(main())
