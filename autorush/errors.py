"""Exceptions d'AutoRush.

Toutes les erreurs previsibles heritent de ``AutoRushError`` afin que la CLI et
l'interface graphique puissent afficher un message clair a l'utilisateur au lieu
d'une trace Python.
"""

from __future__ import annotations


class AutoRushError(Exception):
    """Erreur prevue, affichable telle quelle a l'utilisateur."""

    #: message court propose a l'utilisateur pour resoudre le probleme
    hint: str = ""

    def __init__(self, message: str, hint: str = "") -> None:
        super().__init__(message)
        self.message = message
        if hint:
            self.hint = hint

    def user_text(self) -> str:
        if self.hint:
            return f"{self.message}\n\n-> {self.hint}"
        return self.message


class DependencyMissingError(AutoRushError):
    """Une dependance externe (ffmpeg, faster-whisper...) est absente."""


class MediaError(AutoRushError):
    """Le fichier video est illisible ou non supporte."""


class TranscriptionError(AutoRushError):
    """La transcription a echoue."""


class ExportError(AutoRushError):
    """L'ecriture d'un fichier de sortie a echoue."""


class CancelledError(AutoRushError):
    """L'utilisateur a annule le traitement."""

    def __init__(self, message: str = "Traitement annule par l'utilisateur.") -> None:
        super().__init__(message)
