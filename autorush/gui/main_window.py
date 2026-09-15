"""Fenetre principale d'AutoRush.

L'interface reste volontairement courte : choisir une video, choisir un style,
regler l'intensite des zooms, cocher la preview, lancer. Les reglages plus fins
sont regroupes dans un panneau replie par defaut.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from PySide6.QtCore import Qt, QThread, QUrl
from PySide6.QtGui import QDesktopServices, QDragEnterEvent, QDropEvent, QFont
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from autorush.config import STYLE_LABELS, Settings
from autorush.media.ffmpeg import VIDEO_EXTENSIONS, ffmpeg_available
from autorush.pipeline import STAGES, PipelineOptions, PipelineResult, Progress
from autorush.utils import format_timecode, human_duration
from autorush.version import APP_NAME, __version__

STYLE_ORDER = ["naturel", "dynamique", "tres_dynamique"]
STYLE_HELP = {
    "naturel": "Respiration preservee, peu de zooms. Pour un propos pose.",
    "dynamique": "Rythme serre, zooms reguliers. Le reglage recommande.",
    "tres_dynamique": "Montage tres coupe, beaucoup de mouvement.",
}
MODELS = ["large-v3", "large-v2", "medium", "small", "base", "tiny"]
LANGUAGES = [
    ("auto", "Detection automatique"),
    ("fr", "Francais"),
    ("en", "Anglais"),
    ("multi", "Francais + anglais melanges"),
]


def _card() -> QFrame:
    frame = QFrame()
    frame.setObjectName("Card")
    return frame


def _section(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("SectionTitle")
    return label


class DropZone(QFrame):
    """Zone de depot d'un fichier video."""

    def __init__(self, on_file) -> None:
        super().__init__()
        self.setObjectName("Card")
        self.setAcceptDrops(True)
        self.setMinimumHeight(96)
        self._on_file = on_file

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(5)

        self.name_label = QLabel("Aucune video choisie")
        self.name_label.setObjectName("FileName")
        self.hint_label = QLabel(
            "Glissez un rush ici, ou cliquez sur « Choisir une video »"
        )
        self.hint_label.setObjectName("DropHint")
        self.hint_label.setWordWrap(True)

        row = QHBoxLayout()
        row.setSpacing(9)
        self.browse_button = QPushButton("Choisir une video...")
        self.browse_button.clicked.connect(self._browse)
        row.addWidget(self.browse_button)
        row.addStretch(1)

        layout.addWidget(self.name_label)
        layout.addWidget(self.hint_label)
        layout.addLayout(row)

    def _browse(self) -> None:
        patterns = " ".join(f"*{ext}" for ext in sorted(VIDEO_EXTENSIONS))
        path, _ = QFileDialog.getOpenFileName(
            self, "Choisir un rush", "", f"Videos ({patterns});;Tous les fichiers (*)"
        )
        if path:
            self._on_file(Path(path))

    def set_file(self, path: Path, detail: str = "") -> None:
        self.name_label.setText(path.name)
        self.hint_label.setText(detail or str(path.parent))

    # -- glisser-deposer ------------------------------------------------ #
    def dragEnterEvent(self, event: QDragEnterEvent) -> None:  # noqa: N802
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent) -> None:  # noqa: N802
        for url in event.mimeData().urls():
            path = Path(url.toLocalFile())
            if path.is_file():
                self._on_file(path)
                event.acceptProposedAction()
                return


class StageList(QWidget):
    """Liste des etapes, avec celle en cours mise en avant."""

    def __init__(self) -> None:
        super().__init__()
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)
        self._labels: dict[str, QLabel] = {}
        for key, label, _ in STAGES:
            widget = QLabel(label)
            widget.setObjectName("StageTodo")
            self._labels[key] = widget
            layout.addWidget(widget)
        layout.addStretch(1)

    def set_current(self, stage: str) -> None:
        keys = [key for key, _, _ in STAGES]
        index = keys.index(stage) if stage in keys else len(keys)
        for position, key in enumerate(keys):
            widget = self._labels[key]
            if position < index:
                widget.setObjectName("StageDone")
            elif position == index:
                widget.setObjectName("StageCurrent")
            else:
                widget.setObjectName("StageTodo")
            widget.style().unpolish(widget)
            widget.style().polish(widget)

    def mark_all_done(self) -> None:
        for widget in self._labels.values():
            widget.setObjectName("StageDone")
            widget.style().unpolish(widget)
            widget.style().polish(widget)


class MainWindow(QWidget):
    """Fenetre unique de l'application."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(f"{APP_NAME} {__version__}")
        self.setMinimumSize(760, 660)
        self.resize(880, 820)

        self._input_path: Path | None = None
        self._output_dir: Path | None = None
        self._thread: QThread | None = None
        self._worker = None
        self._result: PipelineResult | None = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        container = QWidget()
        scroll.setWidget(container)
        outer.addWidget(scroll)

        root = QVBoxLayout(container)
        root.setContentsMargins(26, 22, 26, 26)
        root.setSpacing(16)

        root.addLayout(self._build_header())
        self.env_banner = QLabel("")
        self.env_banner.setObjectName("Error")
        self.env_banner.setWordWrap(True)
        self.env_banner.setVisible(False)
        root.addWidget(self.env_banner)
        self.drop_zone = DropZone(self._set_input)
        root.addWidget(self.drop_zone)
        root.addWidget(self._build_style_card())
        root.addWidget(self._build_zoom_card())
        root.addWidget(self._build_output_card())
        root.addWidget(self._build_advanced_card())
        root.addLayout(self._build_actions())
        root.addWidget(self._build_progress_card())
        root.addWidget(self._build_result_card())
        root.addStretch(1)

        self._check_environment()
        self._refresh_enabled()

    # ------------------------------------------------------------------ #
    def _build_header(self) -> QVBoxLayout:
        layout = QVBoxLayout()
        layout.setSpacing(2)
        title = QLabel(APP_NAME)
        title.setObjectName("Title")
        subtitle = QLabel(
            "Montage automatique d'un rush facecam : silences, hesitations, "
            "mauvaises prises et zooms, prets pour Premiere Pro."
        )
        subtitle.setObjectName("Subtitle")
        subtitle.setWordWrap(True)
        layout.addWidget(title)
        layout.addWidget(subtitle)
        return layout

    def _build_style_card(self) -> QFrame:
        card = _card()
        layout = QVBoxLayout(card)
        layout.setContentsMargins(18, 15, 18, 16)
        layout.setSpacing(7)
        layout.addWidget(_section("Style de montage"))

        self.style_group = QButtonGroup(self)
        for index, key in enumerate(STYLE_ORDER):
            button = QRadioButton(STYLE_LABELS[key])
            button.setChecked(key == "dynamique")
            self.style_group.addButton(button, index)
            layout.addWidget(button)
            help_label = QLabel("     " + STYLE_HELP[key])
            help_label.setObjectName("DropHint")
            help_label.setWordWrap(True)
            layout.addWidget(help_label)
        return card

    def _build_zoom_card(self) -> QFrame:
        card = _card()
        layout = QVBoxLayout(card)
        layout.setContentsMargins(18, 15, 18, 16)
        layout.setSpacing(9)
        layout.addWidget(_section("Zooms"))

        self.zoom_enabled = QCheckBox("Ajouter des zooms automatiques")
        self.zoom_enabled.setChecked(True)
        self.zoom_enabled.toggled.connect(self._refresh_enabled)
        layout.addWidget(self.zoom_enabled)

        row = QHBoxLayout()
        row.setSpacing(12)
        self.zoom_slider = QSlider(Qt.Orientation.Horizontal)
        self.zoom_slider.setRange(0, 100)
        self.zoom_slider.setValue(55)
        self.zoom_slider.valueChanged.connect(self._update_zoom_label)
        self.zoom_value = QLabel("55")
        self.zoom_value.setMinimumWidth(30)
        font = QFont()
        font.setBold(True)
        self.zoom_value.setFont(font)
        row.addWidget(QLabel("Intensite"))
        row.addWidget(self.zoom_slider, 1)
        row.addWidget(self.zoom_value)
        layout.addLayout(row)

        self.zoom_hint = QLabel("")
        self.zoom_hint.setObjectName("DropHint")
        self.zoom_hint.setWordWrap(True)
        layout.addWidget(self.zoom_hint)
        self._update_zoom_label(55)
        return card

    def _build_output_card(self) -> QFrame:
        card = _card()
        layout = QVBoxLayout(card)
        layout.setContentsMargins(18, 15, 18, 16)
        layout.setSpacing(8)
        layout.addWidget(_section("Sorties"))

        self.preview_check = QCheckBox("Generer une preview MP4 (plus long)")
        layout.addWidget(self.preview_check)

        row = QHBoxLayout()
        row.setSpacing(9)
        self.output_label = QLabel("Dossier : a cote du rush (AutoRush_out)")
        self.output_label.setObjectName("DropHint")
        self.output_label.setWordWrap(True)
        choose = QPushButton("Changer...")
        choose.clicked.connect(self._choose_output)
        row.addWidget(self.output_label, 1)
        row.addWidget(choose)
        layout.addLayout(row)
        return card

    def _build_advanced_card(self) -> QFrame:
        card = _card()
        layout = QVBoxLayout(card)
        layout.setContentsMargins(18, 15, 18, 16)
        layout.setSpacing(9)

        header = QHBoxLayout()
        header.addWidget(_section("Reglages avances"))
        header.addStretch(1)
        self.advanced_toggle = QPushButton("Afficher")
        self.advanced_toggle.setCheckable(True)
        self.advanced_toggle.toggled.connect(self._toggle_advanced)
        header.addWidget(self.advanced_toggle)
        layout.addLayout(header)

        self.advanced_panel = QWidget()
        grid = QGridLayout(self.advanced_panel)
        grid.setContentsMargins(0, 6, 0, 0)
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(9)

        grid.addWidget(QLabel("Langue"), 0, 0)
        self.language_combo = QComboBox()
        for code, label in LANGUAGES:
            self.language_combo.addItem(label, code)
        grid.addWidget(self.language_combo, 0, 1)

        grid.addWidget(QLabel("Modele"), 1, 0)
        self.model_combo = QComboBox()
        self.model_combo.addItems(MODELS)
        grid.addWidget(self.model_combo, 1, 1)

        grid.addWidget(QLabel("Calcul"), 2, 0)
        self.device_combo = QComboBox()
        self.device_combo.addItem("Automatique", "auto")
        self.device_combo.addItem("Processeur", "cpu")
        self.device_combo.addItem("Carte graphique NVIDIA", "cuda")
        grid.addWidget(self.device_combo, 2, 1)

        self.silences_only = QCheckBox(
            "Ne toucher qu'aux silences (ne supprimer aucune parole)"
        )
        grid.addWidget(self.silences_only, 3, 0, 1, 2)

        self.keep_all = QCheckBox("Ne rien supprimer : analyser et signaler seulement")
        grid.addWidget(self.keep_all, 4, 0, 1, 2)

        self.soft_fillers = QCheckBox(
            "Retirer aussi les tics de langage (« du coup », « genre »...)"
        )
        grid.addWidget(self.soft_fillers, 5, 0, 1, 2)

        self.advanced_panel.setVisible(False)
        layout.addWidget(self.advanced_panel)
        return card

    def _build_actions(self) -> QHBoxLayout:
        layout = QHBoxLayout()
        layout.setSpacing(10)
        self.start_button = QPushButton("Lancer le traitement")
        self.start_button.setObjectName("Primary")
        self.start_button.clicked.connect(self._start)
        self.cancel_button = QPushButton("Annuler")
        self.cancel_button.setObjectName("Danger")
        self.cancel_button.clicked.connect(self._cancel)
        self.cancel_button.setVisible(False)
        layout.addWidget(self.start_button)
        layout.addWidget(self.cancel_button)
        layout.addStretch(1)
        return layout

    def _build_progress_card(self) -> QFrame:
        card = _card()
        self.progress_card = card
        layout = QVBoxLayout(card)
        layout.setContentsMargins(18, 15, 18, 16)
        layout.setSpacing(10)

        self.stage_list = StageList()
        layout.addWidget(self.stage_list)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 1000)
        layout.addWidget(self.progress_bar)

        self.progress_detail = QLabel("")
        self.progress_detail.setObjectName("DropHint")
        self.progress_detail.setWordWrap(True)
        layout.addWidget(self.progress_detail)

        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumHeight(130)
        self.log_view.setVisible(False)
        layout.addWidget(self.log_view)

        self.log_toggle = QPushButton("Afficher le journal")
        self.log_toggle.setCheckable(True)
        self.log_toggle.toggled.connect(self._toggle_log)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(self.log_toggle)
        layout.addLayout(row)

        card.setVisible(False)
        return card

    def _build_result_card(self) -> QFrame:
        card = _card()
        self.result_card = card
        layout = QVBoxLayout(card)
        layout.setContentsMargins(18, 15, 18, 16)
        layout.setSpacing(9)
        layout.addWidget(_section("Resultat"))

        self.result_summary = QLabel("")
        self.result_summary.setWordWrap(True)
        layout.addWidget(self.result_summary)

        self.result_zooms = QLabel("")
        self.result_zooms.setObjectName("DropHint")
        self.result_zooms.setWordWrap(True)
        layout.addWidget(self.result_zooms)

        row = QHBoxLayout()
        row.setSpacing(9)
        self.open_folder_button = QPushButton("Ouvrir le dossier")
        self.open_folder_button.clicked.connect(self._open_folder)
        self.open_report_button = QPushButton("Ouvrir le rapport")
        self.open_report_button.clicked.connect(self._open_report)
        self.open_preview_button = QPushButton("Ouvrir la preview")
        self.open_preview_button.clicked.connect(self._open_preview)
        row.addWidget(self.open_folder_button)
        row.addWidget(self.open_report_button)
        row.addWidget(self.open_preview_button)
        row.addStretch(1)
        layout.addLayout(row)

        card.setVisible(False)
        return card

    # ------------------------------------------------------------------ #
    #: message affiche quand ffmpeg manque
    FFMPEG_MESSAGE = (
        "ffmpeg est introuvable sur cet ordinateur. AutoRush en a besoin pour "
        "lire la video et produire la preview.\n\n"
        "Installation la plus simple, dans PowerShell :\n"
        "    winget install Gyan.FFmpeg\n\n"
        "Vous pouvez aussi placer un dossier \u00ab ffmpeg \u00bb (contenant "
        "bin\\ffmpeg.exe) a cote d'AutoRush.exe."
    )

    def _check_environment(self) -> None:
        """Signale une dependance manquante sans bloquer l'ouverture.

        Un bandeau vaut mieux qu'une fenetre modale : l'utilisateur voit
        l'interface, installe ffmpeg, puis relance le traitement.
        """
        if ffmpeg_available():
            self.env_banner.setVisible(False)
            return
        self.env_banner.setText(self.FFMPEG_MESSAGE)
        self.env_banner.setVisible(True)

    def _update_zoom_label(self, value: int) -> None:
        self.zoom_value.setText(str(value))
        if value <= 20:
            hint = "Mouvement tres discret."
        elif value <= 45:
            hint = "Zooms rares et doux."
        elif value <= 70:
            hint = "Zooms reguliers, amplitude moyenne."
        elif value <= 88:
            hint = "Beaucoup de mouvement, amplitude marquee."
        else:
            hint = "Maximum de mouvement : a reserver aux formats tres rythmes."
        self.zoom_hint.setText(hint)

    def _toggle_advanced(self, shown: bool) -> None:
        self.advanced_panel.setVisible(shown)
        self.advanced_toggle.setText("Masquer" if shown else "Afficher")

    def _toggle_log(self, shown: bool) -> None:
        self.log_view.setVisible(shown)
        self.log_toggle.setText("Masquer le journal" if shown else "Afficher le journal")

    def _set_input(self, path: Path) -> None:
        self._input_path = path
        detail = str(path.parent)
        try:
            size = path.stat().st_size / 1_048_576
            detail = f"{detail}  ·  {size:.0f} Mo"
        except OSError:
            pass
        self.drop_zone.set_file(path, detail)
        self.result_card.setVisible(False)
        self._refresh_enabled()

    def _choose_output(self) -> None:
        directory = QFileDialog.getExistingDirectory(self, "Dossier de sortie")
        if directory:
            self._output_dir = Path(directory)
            self.output_label.setText(f"Dossier : {directory}")

    def _refresh_enabled(self) -> None:
        running = self._thread is not None
        self.start_button.setEnabled(self._input_path is not None and not running)
        self.zoom_slider.setEnabled(self.zoom_enabled.isChecked())
        self.zoom_hint.setEnabled(self.zoom_enabled.isChecked())

    # ------------------------------------------------------------------ #
    def _collect_settings(self) -> Settings:
        style = STYLE_ORDER[self.style_group.checkedId()]
        settings = Settings.for_style(style)
        settings.zoom.enabled = self.zoom_enabled.isChecked()
        settings.zoom.intensity = float(self.zoom_slider.value())
        settings.transcription.language = self.language_combo.currentData()
        settings.transcription.model = self.model_combo.currentText()
        settings.transcription.device = self.device_combo.currentData()
        settings.silences_only = self.silences_only.isChecked()
        settings.dry_run_decisions = self.keep_all.isChecked()
        settings.disfluency.remove_soft_fillers = self.soft_fillers.isChecked()
        settings.export.write_preview = self.preview_check.isChecked()
        return settings

    def _start(self) -> None:
        if self._input_path is None or self._thread is not None:
            return
        from autorush.media.ffmpeg import reset_cache

        reset_cache()
        if not ffmpeg_available():
            self._check_environment()
            QMessageBox.critical(self, "ffmpeg manquant", self.FFMPEG_MESSAGE)
            return
        from autorush.gui.worker import start_worker

        options = PipelineOptions(
            input_path=self._input_path,
            output_dir=self._output_dir,
            settings=self._collect_settings(),
            make_preview=self.preview_check.isChecked(),
        )

        self.log_view.clear()
        self.progress_bar.setValue(0)
        self.progress_detail.setText("Preparation...")
        self.progress_card.setVisible(True)
        self.result_card.setVisible(False)
        self.cancel_button.setVisible(True)
        self.stage_list.set_current("import")

        thread, worker = start_worker(options)
        self._thread, self._worker = thread, worker
        worker.progressed.connect(self._on_progress)
        worker.finished_ok.connect(self._on_finished)
        worker.failed.connect(self._on_failed)
        worker.cancelled.connect(self._on_cancelled)
        worker.logged.connect(self._on_log)
        self._refresh_enabled()
        thread.start()

    def _cancel(self) -> None:
        if self._worker is not None:
            self._worker.cancel()
            self.progress_detail.setText("Annulation en cours...")
            self.cancel_button.setEnabled(False)

    def _cleanup_thread(self) -> None:
        if self._thread is not None:
            self._thread.quit()
            self._thread.wait(4000)
        self._thread = None
        self._worker = None
        self.cancel_button.setVisible(False)
        self.cancel_button.setEnabled(True)
        self._refresh_enabled()

    # ------------------------------------------------------------------ #
    def _on_progress(self, progress: Progress) -> None:
        self.progress_bar.setValue(int(progress.fraction * 1000))
        self.stage_list.set_current(progress.stage)
        text = f"{progress.label} — {progress.percent} %"
        if progress.detail:
            text += f"  ·  {progress.detail}"
        self.progress_detail.setText(text)

    def _on_log(self, level: str, line: str) -> None:
        prefix = {"WARNING": "!  ", "ERROR": "X  "}.get(level, "   ")
        self.log_view.appendPlainText(prefix + line)

    def _on_finished(self, result: PipelineResult) -> None:
        self._result = result
        self._cleanup_thread()
        self.stage_list.mark_all_done()
        self.progress_bar.setValue(1000)
        self.progress_detail.setText(
            f"Termine en {human_duration(result.elapsed)}."
        )

        stats = result.analysis.stats
        to_check = len(result.analysis.flags) + len(result.analysis.seams)
        self.result_summary.setText(
            f"<b>{human_duration(stats['source_duration'])}</b> → "
            f"<b>{human_duration(stats['final_duration'])}</b> "
            f"({stats['compression_ratio'] * 100:.0f} % du rush)<br>"
            f"{stats['shots']} plans · {stats['cuts']} coupes · "
            f"{stats['counts']['hesitations']} hesitations · "
            f"{stats['counts']['reprises']} mauvaises prises · "
            f"{stats['counts']['fragments']} fragments<br>"
            f"{to_check} point(s) a verifier — detail dans le rapport."
        )
        lines = [
            f"{format_timecode(event.timeline_start)} — {event.label} "
            f"({event.start_scale:.0f} % → {event.end_scale:.0f} %)"
            for event in result.zooms.events[:14]
        ]
        if len(result.zooms.events) > 14:
            lines.append(f"... et {len(result.zooms.events) - 14} autres")
        self.result_zooms.setText(
            f"<b>{len(result.zooms.events)} zooms</b><br>" + "<br>".join(lines)
            if lines
            else "Aucun zoom place."
        )
        self.open_report_button.setEnabled("report_html" in result.outputs)
        self.open_preview_button.setEnabled("preview" in result.outputs)
        self.result_card.setVisible(True)

        for warning in result.warnings:
            self._on_log("WARNING", warning)

    def _on_failed(self, message: str, detail: str) -> None:
        self._cleanup_thread()
        self.progress_detail.setText("Le traitement a echoue.")
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Critical)
        box.setWindowTitle("AutoRush")
        box.setText(message)
        if detail:
            box.setInformativeText(detail)
        box.exec()

    def _on_cancelled(self) -> None:
        self._cleanup_thread()
        self.progress_detail.setText("Traitement annule.")

    # ------------------------------------------------------------------ #
    def _open_path(self, path: Path) -> None:
        if not path.exists():
            return
        if path.is_dir():
            if os.name == "nt":  # pragma: no cover
                os.startfile(str(path))  # noqa: S606
                return
            if sys.platform == "darwin":  # pragma: no cover
                subprocess.run(["open", str(path)], check=False)
                return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def _open_folder(self) -> None:
        if self._result:
            self._open_path(self._result.output_dir)

    def _open_report(self) -> None:
        if self._result and "report_html" in self._result.outputs:
            self._open_path(self._result.outputs["report_html"])

    def _open_preview(self) -> None:
        if self._result and "preview" in self._result.outputs:
            self._open_path(self._result.outputs["preview"])

    # ------------------------------------------------------------------ #
    def closeEvent(self, event) -> None:  # noqa: N802
        if self._thread is not None:
            answer = QMessageBox.question(
                self,
                "AutoRush",
                "Un traitement est en cours. Voulez-vous l'interrompre ?",
            )
            if answer != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            self._cancel()
            if self._thread is not None:
                self._thread.quit()
                self._thread.wait(3000)
        event.accept()


def _unused() -> None:  # pragma: no cover - garde les imports explicites
    _ = QSizePolicy
