"""Point d'entree de l'executable en ligne de commande (PyInstaller)."""

import multiprocessing
import sys

if __name__ == "__main__":
    multiprocessing.freeze_support()
    from autorush.cli import main

    sys.exit(main())
