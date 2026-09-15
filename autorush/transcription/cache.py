"""Cache des transcriptions.

Transcrire un rush de 30 minutes prend plusieurs minutes. Comme on relance
souvent AutoRush sur le meme rush pour essayer un autre style ou une autre
intensite de zoom, la transcription est mise en cache.

La clef tient compte du fichier (chemin, taille, date de modification) **et**
des reglages du moteur : changer de modele invalide le cache.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import asdict
from pathlib import Path

from autorush.config import TranscriptionSettings
from autorush.logging_setup import get_logger
from autorush.transcription.base import Transcript
from autorush.transcription.io import transcript_from_dict, transcript_to_dict
from autorush.version import __version__

log = get_logger("transcription.cache")

#: duree de vie d'une entree de cache (jours)
MAX_AGE_DAYS = 120
#: nombre maximum d'entrees conservees
MAX_ENTRIES = 60


def cache_root() -> Path:
    """Dossier de cache, dependant du systeme."""
    override = os.environ.get("AUTORUSH_CACHE_DIR")
    if override:
        return Path(override)
    if os.name == "nt":  # pragma: no cover - specifique Windows
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
        return Path(base) / "AutoRush" / "cache"
    base = os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache")
    return Path(base) / "autorush"


def cache_key(media_path: Path, settings: TranscriptionSettings) -> str:
    """Empreinte du couple (fichier, reglages du moteur)."""
    try:
        stat = media_path.stat()
        signature = f"{media_path.resolve()}|{stat.st_size}|{int(stat.st_mtime)}"
    except OSError:
        signature = str(media_path)
    relevant = {
        key: value
        for key, value in asdict(settings).items()
        if key not in {"use_cache", "cpu_threads", "device", "compute_type"}
    }
    payload = signature + "|" + json.dumps(relevant, sort_keys=True) + "|" + __version__
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:20]


class TranscriptCache:
    """Cache disque simple, une transcription par fichier JSON."""

    def __init__(self, directory: Path | None = None) -> None:
        self.directory = Path(directory) if directory else cache_root()

    def path_for(self, key: str) -> Path:
        return self.directory / f"{key}.json"

    def load(self, key: str) -> Transcript | None:
        path = self.path_for(key)
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            transcript = transcript_from_dict(data)
        except (OSError, ValueError) as exc:
            log.warning("Cache illisible (%s), il sera refait : %s", path.name, exc)
            return None
        log.info("Transcription reprise du cache (%s)", path.name)
        try:
            os.utime(path, None)
        except OSError:  # pragma: no cover
            pass
        return transcript

    def save(self, key: str, transcript: Transcript) -> Path | None:
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            path = self.path_for(key)
            path.write_text(
                json.dumps(transcript_to_dict(transcript), ensure_ascii=False),
                encoding="utf-8",
            )
            self.prune()
            return path
        except OSError as exc:  # pragma: no cover - disque plein, droits...
            log.warning("Impossible d'ecrire le cache : %s", exc)
            return None

    def prune(self) -> None:
        """Retire les entrees trop vieilles ou trop nombreuses."""
        try:
            entries = sorted(
                self.directory.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True
            )
        except OSError:  # pragma: no cover
            return
        deadline = time.time() - MAX_AGE_DAYS * 86400
        for position, entry in enumerate(entries):
            try:
                if position >= MAX_ENTRIES or entry.stat().st_mtime < deadline:
                    entry.unlink(missing_ok=True)
            except OSError:  # pragma: no cover
                continue

    def clear(self) -> int:
        """Vide le cache et retourne le nombre d'entrees supprimees."""
        count = 0
        if not self.directory.exists():
            return 0
        for entry in self.directory.glob("*.json"):
            try:
                entry.unlink()
                count += 1
            except OSError:  # pragma: no cover
                continue
        return count
