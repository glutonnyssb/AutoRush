"""Execution du pipeline dans un fil d'execution separe.

L'interface doit rester reactive pendant un traitement qui dure plusieurs
minutes. Le pipeline tourne donc dans un ``QThread`` et communique par signaux.
"""

from __future__ import annotations

import logging
import traceback

from PySide6.QtCore import QObject, QThread, Signal

from autorush.errors import AutoRushError, CancelledError
from autorush.pipeline import PipelineOptions, PipelineResult, Progress, run_pipeline


class LogBridge(logging.Handler):
    """Renvoie les lignes du journal vers l'interface."""

    def __init__(self, emit_line) -> None:
        super().__init__(level=logging.INFO)
        self._emit_line = emit_line
        self.setFormatter(logging.Formatter("%(message)s"))

    def emit(self, record: logging.LogRecord) -> None:  # pragma: no cover - interface
        try:
            self._emit_line(record.levelname, self.format(record))
        except Exception:
            pass


class PipelineWorker(QObject):
    """Encapsule un traitement."""

    progressed = Signal(object)          # Progress
    finished_ok = Signal(object)         # PipelineResult
    failed = Signal(str, str)            # message, detail
    cancelled = Signal()
    logged = Signal(str, str)            # niveau, ligne

    def __init__(self, options: PipelineOptions) -> None:
        super().__init__()
        self._options = options
        self._cancel = False
        self._options.cancel_check = self.is_cancelled

    def cancel(self) -> None:
        self._cancel = True

    def is_cancelled(self) -> bool:
        return self._cancel

    def run(self) -> None:  # pragma: no cover - interface
        bridge = LogBridge(lambda level, line: self.logged.emit(level, line))
        root = logging.getLogger("autorush")
        root.addHandler(bridge)
        try:
            result: PipelineResult = run_pipeline(
                self._options, on_progress=self._on_progress
            )
            self.finished_ok.emit(result)
        except CancelledError:
            self.cancelled.emit()
        except AutoRushError as exc:
            self.failed.emit(exc.message, exc.hint)
        except Exception as exc:  # noqa: BLE001 - on veut tout rattraper
            self.failed.emit(
                f"Erreur inattendue : {exc}",
                traceback.format_exc(limit=8),
            )
        finally:
            root.removeHandler(bridge)

    def _on_progress(self, progress: Progress) -> None:
        self.progressed.emit(progress)


def start_worker(options: PipelineOptions) -> tuple[QThread, PipelineWorker]:
    """Cree le fil et le worker, et lance le traitement."""
    thread = QThread()
    worker = PipelineWorker(options)
    worker.moveToThread(thread)
    thread.started.connect(worker.run)
    return thread, worker
