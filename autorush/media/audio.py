"""Analyse de l'enveloppe audio.

Deux usages :

* **classer les blancs** : un blanc dont l'energie reste nettement au-dessus du
  plancher de bruit est une respiration audible, qu'il faut garder pour ne pas
  etouffer le montage ; un blanc au plancher est un vrai silence, coupable ;
* **placer les zooms** : une montee d'energie sur un debut de phrase est un bon
  declencheur de mouvement.

L'analyse est volontairement legere : RMS par fenetre glissante, converti en
decibels, puis normalise entre le plancher de bruit et le niveau de parole du
rush. Aucune dependance lourde.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from autorush.errors import MediaError
from autorush.logging_setup import get_logger
from autorush.utils import clamp

log = get_logger("media.audio")

#: pas d'analyse (secondes) : 10 ms suffisent pour distinguer une respiration
DEFAULT_HOP = 0.010
DEFAULT_WINDOW = 0.025
#: plancher en dB, evite log(0)
DB_FLOOR = -90.0


def load_audio(path: str | Path) -> tuple[np.ndarray, int]:
    """Charge un WAV mono en ``float32`` dans [-1, 1]."""
    try:
        import soundfile as sf
    except ImportError as exc:  # pragma: no cover - dependance declaree
        raise MediaError(
            "Le module 'soundfile' est absent.",
            "Installez-le avec : pip install soundfile",
        ) from exc

    path = Path(path)
    try:
        samples, sample_rate = sf.read(str(path), dtype="float32", always_2d=True)
    except Exception as exc:  # pragma: no cover - fichier casse
        raise MediaError(f"Audio illisible : {path.name}", str(exc)) from exc

    if samples.size == 0:
        raise MediaError(f"Audio vide : {path.name}")
    mono = samples.mean(axis=1) if samples.shape[1] > 1 else samples[:, 0]
    return np.ascontiguousarray(mono), int(sample_rate)


@dataclass
class AudioProfile:
    """Enveloppe d'energie d'un rush, en decibels."""

    #: niveau RMS en dB, une valeur tous les ``hop`` secondes
    levels: np.ndarray
    hop: float
    sample_rate: int
    duration: float
    #: plancher de bruit estime (dB)
    noise_floor: float = -60.0
    #: niveau de parole typique (dB)
    speech_level: float = -20.0
    meta: dict = field(default_factory=dict)

    # ------------------------------------------------------------------ #
    @classmethod
    def analyze(
        cls,
        samples: np.ndarray,
        sample_rate: int,
        hop: float = DEFAULT_HOP,
        window: float = DEFAULT_WINDOW,
    ) -> AudioProfile:
        """Calcule l'enveloppe RMS en dB."""
        hop_samples = max(1, int(round(hop * sample_rate)))
        window_samples = max(hop_samples, int(round(window * sample_rate)))

        count = max(1, 1 + (len(samples) - 1) // hop_samples)
        # padding pour que la derniere fenetre soit complete
        padded = np.pad(samples, (0, window_samples), mode="constant")
        # somme des carres par fenetre via somme cumulee : O(n), sans boucle
        squares = np.concatenate(([0.0], np.cumsum(padded.astype(np.float64) ** 2)))
        starts = np.arange(count) * hop_samples
        ends = np.minimum(starts + window_samples, len(padded))
        energies = (squares[ends] - squares[starts]) / np.maximum(1, ends - starts)
        rms = np.sqrt(np.maximum(energies, 0.0))
        levels = 20.0 * np.log10(np.maximum(rms, 1e-10))
        levels = np.maximum(levels, DB_FLOOR).astype(np.float32)

        profile = cls(
            levels=levels,
            hop=hop,
            sample_rate=sample_rate,
            duration=len(samples) / float(sample_rate),
        )
        profile._estimate_reference_levels()
        log.info(
            "Audio : %.1f s | plancher %.1f dB | parole %.1f dB",
            profile.duration, profile.noise_floor, profile.speech_level,
        )
        return profile

    @classmethod
    def from_file(cls, path: str | Path, hop: float = DEFAULT_HOP) -> AudioProfile:
        samples, sample_rate = load_audio(path)
        return cls.analyze(samples, sample_rate, hop=hop)

    # ------------------------------------------------------------------ #
    def _estimate_reference_levels(self) -> None:
        """Estime le plancher de bruit et le niveau de parole par percentiles."""
        finite = self.levels[self.levels > DB_FLOOR + 1.0]
        if finite.size < 10:
            self.noise_floor = DB_FLOOR
            self.speech_level = DB_FLOOR + 30.0
            return
        self.noise_floor = float(np.percentile(finite, 8.0))
        self.speech_level = float(np.percentile(finite, 88.0))
        # garde-fou : un rush tres compresse peut avoir un ecart minuscule
        if self.speech_level - self.noise_floor < 8.0:
            self.speech_level = self.noise_floor + 8.0

    # ------------------------------------------------------------------ #
    def _index(self, time: float) -> int:
        return int(clamp(round(time / self.hop), 0, max(0, len(self.levels) - 1)))

    def _slice(self, start: float, end: float) -> np.ndarray:
        first = self._index(start)
        last = self._index(end)
        if last <= first:
            last = min(len(self.levels), first + 1)
        return self.levels[first:last]

    # ------------------------------------------------------------------ #
    def mean_db(self, start: float, end: float) -> float:
        """Niveau moyen (dB) sur l'intervalle."""
        window = self._slice(start, end)
        if window.size == 0:
            return self.noise_floor
        return float(np.mean(window))

    def peak_db(self, start: float, end: float) -> float:
        window = self._slice(start, end)
        if window.size == 0:
            return self.noise_floor
        return float(np.max(window))

    def energy(self, start: float, end: float) -> float:
        """Energie normalisee dans [0, 1] (0 = silence, 1 = parole pleine)."""
        span = max(1e-6, self.speech_level - self.noise_floor)
        return clamp((self.mean_db(start, end) - self.noise_floor) / span, 0.0, 1.0)

    def peak_energy(self, start: float, end: float) -> float:
        span = max(1e-6, self.speech_level - self.noise_floor)
        return clamp((self.peak_db(start, end) - self.noise_floor) / span, 0.0, 1.0)

    def is_breath(
        self, start: float, end: float, min_energy: float = 0.055, max_energy: float = 0.42
    ) -> bool:
        """Le blanc contient-il une respiration audible ?

        Une respiration se situe entre le plancher de bruit et la parole : elle
        n'est ni un silence total, ni un mot.
        """
        if end - start <= 0.0:
            return False
        level = self.energy(start, end)
        return min_energy <= level <= max_energy

    def is_silent(self, start: float, end: float, threshold: float = 0.055) -> bool:
        return self.energy(start, end) < threshold

    def loudness_profile(self, start: float, end: float, steps: int = 8) -> list[float]:
        """Echantillonne l'energie sur l'intervalle (pour le placement des zooms)."""
        if steps <= 0 or end <= start:
            return []
        edges = np.linspace(start, end, steps + 1)
        return [
            self.energy(float(edges[i]), float(edges[i + 1])) for i in range(steps)
        ]

    def onset_strength(self, time: float, look_back: float = 0.35, look_ahead: float = 0.35) -> float:
        """Force de l'attaque a ``time`` : montee d'energie apres un creux."""
        before = self.energy(max(0.0, time - look_back), time)
        after = self.energy(time, time + look_ahead)
        return clamp(after - before, 0.0, 1.0)

    def as_dict(self) -> dict:
        return {
            "hop": self.hop,
            "duration": round(self.duration, 3),
            "noise_floor_db": round(self.noise_floor, 2),
            "speech_level_db": round(self.speech_level, 2),
            "frames": int(len(self.levels)),
        }
