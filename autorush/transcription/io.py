"""Lecture / ecriture de transcriptions.

Formats supportes :

* ``.json`` natif AutoRush (aller-retour sans perte) ;
* ``.json`` Whisper / faster-whisper / WhisperX (segments + words) ;
* ``.srt`` (import approximatif : un mot = une part egale du sous-titre) ;
* ecriture ``.srt`` pour relecture humaine.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from autorush.errors import AutoRushError
from autorush.transcription.base import Segment, Transcript, Word


# --------------------------------------------------------------------------- #
# Dictionnaires
# --------------------------------------------------------------------------- #
def transcript_to_dict(transcript: Transcript) -> dict[str, Any]:
    return {
        "format": "autorush-transcript",
        "version": 1,
        "language": transcript.language,
        "languages": list(transcript.languages),
        "duration": round(transcript.duration, 4),
        "model": transcript.model,
        "meta": transcript.meta,
        "segments": [
            {
                "start": round(seg.start, 4),
                "end": round(seg.end, 4),
                "text": seg.text,
                "language": seg.language,
                "avg_logprob": round(seg.avg_logprob, 4),
                "no_speech_prob": round(seg.no_speech_prob, 4),
                "words": [
                    {
                        "text": w.text,
                        "start": round(w.start, 4),
                        "end": round(w.end, 4),
                        "probability": round(w.probability, 4),
                        "language": w.language,
                    }
                    for w in seg.words
                ],
            }
            for seg in transcript.segments
        ],
    }


def _word_from_dict(raw: dict[str, Any], default_language: str = "") -> Word | None:
    text = raw.get("text", raw.get("word", ""))
    if text is None:
        return None
    text = str(text)
    if not text.strip():
        return None
    start = raw.get("start")
    end = raw.get("end")
    if start is None or end is None:
        return None
    return Word(
        text=text,
        start=float(start),
        end=float(end),
        probability=float(raw.get("probability", raw.get("score", 1.0)) or 1.0),
        language=str(raw.get("language", default_language) or default_language),
    )


def transcript_from_dict(data: dict[str, Any]) -> Transcript:
    """Accepte le format natif comme celui de Whisper / WhisperX."""
    if not isinstance(data, dict):
        raise AutoRushError("Transcription JSON invalide (objet attendu).")

    transcript = Transcript()
    transcript.language = str(data.get("language", "") or "")
    transcript.languages = [str(x) for x in (data.get("languages") or []) if x]
    transcript.duration = float(data.get("duration", 0.0) or 0.0)
    transcript.model = str(data.get("model", "") or "")
    meta = data.get("meta")
    transcript.meta = dict(meta) if isinstance(meta, dict) else {}

    raw_segments = data.get("segments")
    if not isinstance(raw_segments, list):
        raw_segments = []

    for raw_segment in raw_segments:
        if not isinstance(raw_segment, dict):
            continue
        language = str(raw_segment.get("language", transcript.language) or transcript.language)
        words: list[Word] = []
        for raw_word in raw_segment.get("words") or []:
            if not isinstance(raw_word, dict):
                continue
            word = _word_from_dict(raw_word, language)
            if word is not None:
                words.append(word)
        seg_start = raw_segment.get("start")
        seg_end = raw_segment.get("end")
        if seg_start is None and words:
            seg_start = words[0].start
        if seg_end is None and words:
            seg_end = words[-1].end
        segment = Segment(
            start=float(seg_start or 0.0),
            end=float(seg_end or 0.0),
            text=str(raw_segment.get("text", "") or ""),
            words=words,
            language=language,
            avg_logprob=float(raw_segment.get("avg_logprob", 0.0) or 0.0),
            no_speech_prob=float(raw_segment.get("no_speech_prob", 0.0) or 0.0),
        )
        transcript.segments.append(segment)

    # Certains exports placent les mots a la racine.
    root_words = data.get("words")
    if isinstance(root_words, list) and not any(seg.words for seg in transcript.segments):
        words = []
        for raw_word in root_words:
            if isinstance(raw_word, dict):
                word = _word_from_dict(raw_word, transcript.language)
                if word is not None:
                    words.append(word)
        if words:
            transcript.segments.append(
                Segment(
                    start=words[0].start,
                    end=words[-1].end,
                    text=" ".join(w.text for w in words),
                    words=words,
                    language=transcript.language,
                )
            )

    _link(transcript)
    return transcript


def _link(transcript: Transcript) -> Transcript:
    """Reconstruit ``transcript.words`` depuis les segments et renumerote."""
    transcript.words = []
    for seg_index, segment in enumerate(transcript.segments):
        segment.index = seg_index
        if not segment.words and segment.text.strip():
            # Segment sans mots : on repartit le texte uniformement. La precision
            # est mediocre mais le pipeline reste fonctionnel.
            tokens = segment.text.split()
            if tokens:
                step = segment.duration / len(tokens) if segment.duration > 0 else 0.25
                segment.words = [
                    Word(
                        text=token,
                        start=segment.start + i * step,
                        end=segment.start + (i + 1) * step,
                        probability=0.5,
                        language=segment.language,
                    )
                    for i, token in enumerate(tokens)
                ]
        for word in segment.words:
            word.segment_index = seg_index
            if not word.language:
                word.language = segment.language
            transcript.words.append(word)
    transcript.reindex()
    if not transcript.languages:
        seen: dict[str, float] = {}
        for segment in transcript.segments:
            if segment.language:
                seen[segment.language] = seen.get(segment.language, 0.0) + segment.duration
        transcript.languages = [k for k, _ in sorted(seen.items(), key=lambda kv: -kv[1])]
    if not transcript.language and transcript.languages:
        transcript.language = transcript.languages[0]
    return transcript


def link_transcript(transcript: Transcript) -> Transcript:
    """Expose ``_link`` pour les moteurs de transcription."""
    return _link(transcript)


# --------------------------------------------------------------------------- #
# Fichiers
# --------------------------------------------------------------------------- #
def save_transcript(transcript: Transcript, path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(transcript_to_dict(transcript), indent=1, ensure_ascii=False),
        encoding="utf-8",
    )
    return path


_SRT_TIME = re.compile(
    r"(\d{1,2}):(\d{2}):(\d{2})[,.](\d{1,3})\s*-->\s*(\d{1,2}):(\d{2}):(\d{2})[,.](\d{1,3})"
)


def _srt_seconds(hours: str, minutes: str, seconds: str, millis: str) -> float:
    return (
        int(hours) * 3600
        + int(minutes) * 60
        + int(seconds)
        + int(millis.ljust(3, "0")) / 1000.0
    )


def load_srt(path: str | Path) -> Transcript:
    """Import approximatif d'un ``.srt`` (pas d'horodatage par mot reel)."""
    text = Path(path).read_text(encoding="utf-8-sig", errors="replace")
    transcript = Transcript()
    blocks = re.split(r"\n\s*\n", text.strip())
    for block in blocks:
        lines = [ln for ln in block.splitlines() if ln.strip()]
        if len(lines) < 2:
            continue
        match = None
        content_start = 0
        for i, line in enumerate(lines[:2]):
            match = _SRT_TIME.search(line)
            if match:
                content_start = i + 1
                break
        if not match:
            continue
        start = _srt_seconds(*match.group(1, 2, 3, 4))
        end = _srt_seconds(*match.group(5, 6, 7, 8))
        content = " ".join(lines[content_start:]).strip()
        if not content:
            continue
        transcript.segments.append(Segment(start=start, end=end, text=content))
    return _link(transcript)


def load_transcript(path: str | Path) -> Transcript:
    """Charge une transcription ``.json`` ou ``.srt``."""
    path = Path(path)
    if not path.exists():
        raise AutoRushError(f"Transcription introuvable : {path}")
    if path.suffix.lower() in {".srt", ".vtt"}:
        return load_srt(path)
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise AutoRushError(
            f"Transcription JSON illisible : {path.name}", f"Erreur JSON : {exc}"
        ) from exc
    return transcript_from_dict(data)


def _srt_timestamp(seconds: float) -> str:
    seconds = max(0.0, seconds)
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    millis = int(round((seconds - int(seconds)) * 1000))
    if millis == 1000:
        millis = 999
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def write_srt(transcript: Transcript, path: str | Path) -> Path:
    """Ecrit un ``.srt`` a partir des segments (relecture humaine)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    counter = 1
    for segment in transcript.segments:
        content = segment.text.strip() or " ".join(w.clean for w in segment.words)
        if not content:
            continue
        lines.append(str(counter))
        lines.append(f"{_srt_timestamp(segment.start)} --> {_srt_timestamp(segment.end)}")
        lines.append(content)
        lines.append("")
        counter += 1
    path.write_text("\n".join(lines), encoding="utf-8")
    return path
