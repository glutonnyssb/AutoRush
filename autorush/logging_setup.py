"""Configuration du journal (console + fichier)."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

_CONFIGURED = False
_FMT = "%(asctime)s | %(levelname)-7s | %(name)-28s | %(message)s"
_DATEFMT = "%H:%M:%S"


def setup_logging(level: int = logging.INFO, log_file: Path | None = None) -> logging.Logger:
    """Initialise le logger racine ``autorush`` (idempotent)."""
    global _CONFIGURED
    root = logging.getLogger("autorush")
    root.setLevel(logging.DEBUG)

    if not _CONFIGURED:
        console = logging.StreamHandler(stream=sys.stderr)
        console.setLevel(level)
        console.setFormatter(logging.Formatter(_FMT, datefmt=_DATEFMT))
        root.addHandler(console)
        root.propagate = False
        _CONFIGURED = True
    else:
        for handler in root.handlers:
            if isinstance(handler, logging.StreamHandler) and not isinstance(
                handler, logging.FileHandler
            ):
                handler.setLevel(level)

    if log_file is not None:
        log_file = Path(log_file)
        log_file.parent.mkdir(parents=True, exist_ok=True)
        existing = {
            Path(h.baseFilename).resolve()
            for h in root.handlers
            if isinstance(h, logging.FileHandler)
        }
        if log_file.resolve() not in existing:
            file_handler = logging.FileHandler(log_file, encoding="utf-8")
            file_handler.setLevel(logging.DEBUG)
            file_handler.setFormatter(logging.Formatter(_FMT, datefmt=_DATEFMT))
            root.addHandler(file_handler)

    return root


def get_logger(name: str) -> logging.Logger:
    """Retourne un logger enfant du logger applicatif."""
    if name.startswith("autorush"):
        return logging.getLogger(name)
    return logging.getLogger(f"autorush.{name}")
