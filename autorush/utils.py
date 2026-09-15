"""Petites fonctions utilitaires partagees (temps, texte, structures)."""

from __future__ import annotations

import math
import re
import unicodedata
from collections.abc import Iterable, Sequence
from typing import TypeVar

T = TypeVar("T")


# --------------------------------------------------------------------------- #
# Temps / timecode
# --------------------------------------------------------------------------- #
def format_timecode(seconds: float, with_ms: bool = False) -> str:
    """``93.4`` -> ``01:33`` (ou ``01:33.400``). Utilise dans les rapports."""
    if seconds is None or (isinstance(seconds, float) and math.isnan(seconds)):
        return "--:--"
    seconds = max(0.0, float(seconds))
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = seconds % 60
    if with_ms:
        base = f"{minutes:02d}:{secs:06.3f}"
    else:
        base = f"{minutes:02d}:{int(secs):02d}"
    if hours:
        return f"{hours:d}:{base}"
    return base


def format_smpte(seconds: float, fps: float) -> str:
    """Timecode SMPTE ``HH:MM:SS:FF`` a partir d'une duree en secondes."""
    fps = float(fps) if fps and fps > 0 else 25.0
    total_frames = int(round(max(0.0, seconds) * fps))
    frames_per_hour = int(round(fps * 3600))
    frames_per_minute = int(round(fps * 60))
    hours = total_frames // frames_per_hour
    rem = total_frames % frames_per_hour
    minutes = rem // frames_per_minute
    rem %= frames_per_minute
    secs = int(rem // round(fps))
    frames = int(rem % round(fps))
    return f"{hours:02d}:{minutes:02d}:{secs:02d}:{frames:02d}"


def parse_timecode(text: str) -> float:
    """``01:33``/``1:02:03``/``12.5`` -> secondes."""
    text = text.strip()
    if not text:
        return 0.0
    parts = text.split(":")
    try:
        values = [float(p.replace(",", ".")) for p in parts]
    except ValueError as exc:  # pragma: no cover - entree utilisateur
        raise ValueError(f"Timecode invalide : {text!r}") from exc
    total = 0.0
    for value in values:
        total = total * 60.0 + value
    return total


def clamp(value: float, low: float, high: float) -> float:
    if low > high:
        low, high = high, low
    return max(low, min(high, value))


def lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def remap(value: float, in_lo: float, in_hi: float, out_lo: float, out_hi: float) -> float:
    """Transpose linearement ``value`` d'un intervalle a un autre (borne)."""
    if in_hi == in_lo:
        return out_lo
    t = clamp((value - in_lo) / (in_hi - in_lo), 0.0, 1.0)
    return lerp(out_lo, out_hi, t)


# --------------------------------------------------------------------------- #
# Texte
# --------------------------------------------------------------------------- #
_APOSTROPHES = {"’": "'", "ʼ": "'", "‘": "'", "`": "'"}
_PUNCT_RE = re.compile(r"[^\w'\-]+", re.UNICODE)
_MULTISPACE_RE = re.compile(r"\s+")


def normalize_apostrophes(text: str) -> str:
    for src, dst in _APOSTROPHES.items():
        text = text.replace(src, dst)
    return text


def strip_accents(text: str) -> str:
    """``dEja`` -> ``deja`` : indispensable pour comparer des transcriptions."""
    decomposed = unicodedata.normalize("NFD", text)
    return "".join(ch for ch in decomposed if unicodedata.category(ch) != "Mn")


def normalize_word(word: str) -> str:
    """Forme canonique d'un mot pour les comparaisons lexicales.

    Minuscules, sans accents, sans ponctuation de bord, apostrophes unifiees.
    Conserve le tiret interne (``peut-etre``) et l'apostrophe (``j'ai``).
    """
    word = normalize_apostrophes(word or "").strip()
    word = strip_accents(word).lower()
    word = word.strip(".,;:!?()[]{}\"«»…")
    word = word.replace("…", "")
    return word.strip()


def normalize_text(text: str) -> str:
    """Normalise une phrase entiere en une suite de mots separes par un espace."""
    text = normalize_apostrophes(text or "")
    text = strip_accents(text).lower()
    text = _PUNCT_RE.sub(" ", text)
    return _MULTISPACE_RE.sub(" ", text).strip()


def tokenize(text: str) -> list[str]:
    """Decoupe un texte normalise en tokens comparables."""
    normalized = normalize_text(text)
    return [t for t in normalized.split(" ") if t]


def ends_with_suspension(text: str) -> bool:
    """La phrase se termine-t-elle en suspens (``...``) ?"""
    stripped = (text or "").strip().rstrip("\"'\u00bb)]")
    return stripped.endswith("...") or stripped.endswith("\u2026")


def ends_with_terminal_punct(text: str) -> bool:
    """La phrase se termine-t-elle proprement (``.``, ``!``, ``?``) ?

    Les points de suspension ne comptent **pas** : ``Raru...`` est une phrase
    laissee en suspens, pas une phrase finie.
    """
    stripped = (text or "").strip().rstrip("\"'\u00bb)]")
    if not stripped:
        return False
    if ends_with_suspension(stripped):
        return False
    return stripped[-1] in ".!?"


# --------------------------------------------------------------------------- #
# Structures
# --------------------------------------------------------------------------- #
def merge_intervals(
    intervals: Iterable[tuple[float, float]], tolerance: float = 0.0
) -> list[tuple[float, float]]:
    """Fusionne des intervalles [debut, fin] qui se touchent ou se chevauchent."""
    items = sorted((float(a), float(b)) for a, b in intervals if b > a)
    if not items:
        return []
    merged: list[tuple[float, float]] = [items[0]]
    for start, end in items[1:]:
        last_start, last_end = merged[-1]
        if start <= last_end + tolerance:
            merged[-1] = (last_start, max(last_end, end))
        else:
            merged.append((start, end))
    return merged


def subtract_intervals(
    base: Sequence[tuple[float, float]], holes: Sequence[tuple[float, float]]
) -> list[tuple[float, float]]:
    """Retire ``holes`` de ``base`` (tous deux des listes d'intervalles)."""
    result: list[tuple[float, float]] = []
    holes_sorted = merge_intervals(holes)
    for start, end in merge_intervals(base):
        cursor = start
        for h_start, h_end in holes_sorted:
            if h_end <= cursor or h_start >= end:
                continue
            if h_start > cursor:
                result.append((cursor, min(h_start, end)))
            cursor = max(cursor, h_end)
            if cursor >= end:
                break
        if cursor < end:
            result.append((cursor, end))
    return [(a, b) for a, b in result if b > a]


def intervals_duration(intervals: Iterable[tuple[float, float]]) -> float:
    return sum(max(0.0, b - a) for a, b in intervals)


def overlap(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Duree de chevauchement entre deux intervalles (0 si disjoints)."""
    return max(0.0, min(a[1], b[1]) - max(a[0], b[0]))


def chunked(items: Sequence[T], size: int) -> list[list[T]]:
    if size <= 0:
        raise ValueError("size must be > 0")
    return [list(items[i : i + size]) for i in range(0, len(items), size)]


def human_duration(seconds: float) -> str:
    """``3725`` -> ``1 h 02 min``. Pour les rapports."""
    seconds = max(0.0, float(seconds))
    if seconds < 60:
        return f"{seconds:.1f} s"
    minutes, secs = divmod(int(round(seconds)), 60)
    if minutes < 60:
        return f"{minutes} min {secs:02d} s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours} h {minutes:02d} min"


def safe_filename(name: str, max_length: int = 120) -> str:
    """Nettoie un nom de fichier pour Windows."""
    name = normalize_apostrophes(name or "sortie")
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name)
    name = name.strip(" .") or "sortie"
    return name[:max_length]
