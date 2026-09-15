"""Enveloppe autour de ffmpeg / ffprobe.

AutoRush n'embarque pas ffmpeg : il le cherche, dans cet ordre,

1. la variable d'environnement ``AUTORUSH_FFMPEG`` (chemin complet) ;
2. un dossier ``ffmpeg/bin`` place a cote de l'executable ou du projet
   (mode portable, pratique pour une distribution Windows) ;
3. le ``PATH`` du systeme ;
4. les emplacements d'installation habituels sous Windows.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

from autorush.errors import DependencyMissingError, MediaError
from autorush.logging_setup import get_logger

log = get_logger("media.ffmpeg")

FFMPEG_HINT = (
    "Installez ffmpeg puis relancez AutoRush.\n"
    "  - Windows (recommande) : winget install Gyan.FFmpeg\n"
    "  - ou telechargez https://www.gyan.dev/ffmpeg/builds/ et placez le\n"
    "    dossier 'ffmpeg' (contenant bin/ffmpeg.exe) a cote d'AutoRush.exe\n"
    "  - ou definissez la variable AUTORUSH_FFMPEG vers ffmpeg.exe"
)

VIDEO_EXTENSIONS = {
    ".mp4", ".mov", ".m4v", ".mkv", ".avi", ".mxf", ".mts", ".m2ts",
    ".webm", ".wmv", ".flv", ".mpg", ".mpeg", ".3gp", ".ts", ".braw",
}

_CACHE: dict[str, str | None] = {}


# --------------------------------------------------------------------------- #
# Localisation des binaires
# --------------------------------------------------------------------------- #
def _app_root() -> Path:
    """Dossier de l'application (gere le mode PyInstaller)."""
    if getattr(sys, "frozen", False):  # pragma: no cover - build Windows
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


def _candidate_paths(name: str) -> list[Path]:
    exe = f"{name}.exe" if os.name == "nt" else name
    root = _app_root()
    candidates = [
        root / "ffmpeg" / "bin" / exe,
        root / "ffmpeg" / exe,
        root / "bin" / exe,
        root / exe,
        root.parent / "ffmpeg" / "bin" / exe,
    ]
    if os.name == "nt":  # pragma: no cover - specifique Windows
        program_files = [
            Path(os.environ.get("ProgramFiles", r"C:\Program Files")),
            Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")),
            Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WinGet" / "Packages",
        ]
        for base in program_files:
            if not str(base):
                continue
            candidates.append(base / "ffmpeg" / "bin" / exe)
            candidates.append(base / "ffmpeg" / exe)
        candidates.append(Path(r"C:\ffmpeg\bin") / exe)
    return candidates


def _locate(name: str, env_var: str) -> str | None:
    if name in _CACHE:
        return _CACHE[name]

    found: str | None = None

    override = os.environ.get(env_var, "").strip('" ')
    if override:
        path = Path(override)
        if path.is_dir():
            exe = f"{name}.exe" if os.name == "nt" else name
            path = path / exe
        if path.exists():
            found = str(path)
        else:
            log.warning("%s pointe vers un fichier inexistant : %s", env_var, override)

    if found is None:
        which = shutil.which(name)
        if which:
            found = which

    if found is None:
        for candidate in _candidate_paths(name):
            try:
                if candidate.exists():
                    found = str(candidate)
                    break
            except OSError:  # pragma: no cover - chemin invalide
                continue

    if found is None and os.name == "nt":  # pragma: no cover - recherche WinGet
        winget = Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WinGet" / "Packages"
        if winget.is_dir():
            for match in winget.glob(f"**/{name}.exe"):
                found = str(match)
                break

    _CACHE[name] = found
    if found:
        log.debug("%s trouve : %s", name, found)
    return found


def find_ffmpeg(required: bool = True) -> str | None:
    """Chemin de ``ffmpeg``. Leve ``DependencyMissingError`` si requis et absent."""
    path = _locate("ffmpeg", "AUTORUSH_FFMPEG")
    if path is None and required:
        raise DependencyMissingError("ffmpeg est introuvable sur ce systeme.", FFMPEG_HINT)
    return path


def find_ffprobe(required: bool = True) -> str | None:
    """Chemin de ``ffprobe`` (livre avec ffmpeg)."""
    path = _locate("ffprobe", "AUTORUSH_FFPROBE")
    if path is None:
        ffmpeg = _locate("ffmpeg", "AUTORUSH_FFMPEG")
        if ffmpeg:
            exe = "ffprobe.exe" if os.name == "nt" else "ffprobe"
            sibling = Path(ffmpeg).with_name(exe)
            if sibling.exists():
                path = str(sibling)
                _CACHE["ffprobe"] = path
    if path is None and required:
        raise DependencyMissingError("ffprobe est introuvable sur ce systeme.", FFMPEG_HINT)
    return path


def reset_cache() -> None:
    """Oublie les chemins memorises (utile apres une installation)."""
    _CACHE.clear()


def ffmpeg_available() -> bool:
    return find_ffmpeg(required=False) is not None


# --------------------------------------------------------------------------- #
# Informations media
# --------------------------------------------------------------------------- #
@dataclass
class MediaInfo:
    """Caracteristiques utiles d'un fichier video."""

    path: Path
    duration: float = 0.0
    fps: float = 25.0
    #: base de temps exacte pour Premiere (``timebase``, ``ntsc``)
    timebase: int = 25
    ntsc: bool = False
    width: int = 1920
    height: int = 1080
    #: rapport de forme des pixels (1.0 = carre)
    pixel_aspect: float = 1.0
    video_codec: str = ""
    has_audio: bool = True
    audio_channels: int = 2
    audio_sample_rate: int = 48000
    audio_codec: str = ""
    #: rotation declaree dans les metadonnees (0, 90, 180, 270)
    rotation: int = 0
    #: nombre total d'images (estime si absent)
    frame_count: int = 0
    raw: dict = field(default_factory=dict)

    @property
    def name(self) -> str:
        return self.path.stem

    @property
    def display_width(self) -> int:
        """Largeur apres rotation."""
        return self.height if self.rotation in (90, 270) else self.width

    @property
    def display_height(self) -> int:
        return self.width if self.rotation in (90, 270) else self.height

    def as_dict(self) -> dict:
        return {
            "path": str(self.path),
            "duration": round(self.duration, 3),
            "fps": round(self.fps, 6),
            "timebase": self.timebase,
            "ntsc": self.ntsc,
            "width": self.width,
            "height": self.height,
            "video_codec": self.video_codec,
            "has_audio": self.has_audio,
            "audio_channels": self.audio_channels,
            "audio_sample_rate": self.audio_sample_rate,
            "audio_codec": self.audio_codec,
            "rotation": self.rotation,
        }


def _parse_fraction(value: str | None, default: float = 0.0) -> float:
    if not value:
        return default
    text = str(value).strip()
    if "/" in text:
        numerator, _, denominator = text.partition("/")
        try:
            num = float(numerator)
            den = float(denominator)
        except ValueError:
            return default
        if den == 0:
            return default
        return num / den
    try:
        return float(text)
    except ValueError:
        return default


#: familles d'images/seconde acceptees par Premiere, avec leur base de temps
_KNOWN_RATES: tuple[tuple[float, int, bool], ...] = (
    (23.976, 24, True),
    (24.0, 24, False),
    (25.0, 25, False),
    (29.97, 30, True),
    (30.0, 30, False),
    (47.952, 48, True),
    (48.0, 48, False),
    (50.0, 50, False),
    (59.94, 60, True),
    (60.0, 60, False),
    (100.0, 100, False),
    (119.88, 120, True),
    (120.0, 120, False),
)


def resolve_timebase(fps: float) -> tuple[int, bool, float]:
    """``29.97`` -> ``(30, True, 29.97)``. Arrondit au format le plus proche."""
    if fps <= 0:
        return 25, False, 25.0
    best = min(_KNOWN_RATES, key=lambda item: abs(item[0] - fps))
    if abs(best[0] - fps) <= 0.08:
        exact = best[1] * 1000.0 / 1001.0 if best[2] else float(best[1])
        return best[1], best[2], exact
    # cadence exotique : on arrondit a l'entier le plus proche
    timebase = max(1, int(round(fps)))
    return timebase, False, float(timebase)


def probe_media(path: str | Path) -> MediaInfo:
    """Interroge ffprobe et retourne les caracteristiques du fichier."""
    path = Path(path)
    if not path.exists():
        raise MediaError(f"Fichier introuvable : {path}")
    if path.suffix.lower() not in VIDEO_EXTENSIONS:
        log.warning("Extension inhabituelle (%s), tentative de lecture quand meme.", path.suffix)

    ffprobe = find_ffprobe()
    command = [
        ffprobe,
        "-v", "error",
        "-print_format", "json",
        "-show_format",
        "-show_streams",
        str(path),
    ]
    try:
        completed = subprocess.run(
            command, capture_output=True, text=True, encoding="utf-8", errors="replace",
            check=False,
        )
    except OSError as exc:  # pragma: no cover - ffprobe casse
        raise MediaError(f"ffprobe n'a pas pu etre lance : {exc}", FFMPEG_HINT) from exc

    if completed.returncode != 0:
        raise MediaError(
            f"ffprobe n'a pas pu lire {path.name}.",
            (completed.stderr or "").strip()[:500] or "Fichier corrompu ou format non supporte ?",
        )

    try:
        data = json.loads(completed.stdout or "{}")
    except json.JSONDecodeError as exc:  # pragma: no cover
        raise MediaError(f"Reponse ffprobe illisible pour {path.name}.") from exc

    streams = data.get("streams") or []
    fmt = data.get("format") or {}
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)

    if video is None:
        raise MediaError(
            f"{path.name} ne contient pas de piste video.",
            "AutoRush attend un rush facecam (MP4, MOV...).",
        )

    info = MediaInfo(path=path, raw=data)
    info.duration = _parse_fraction(fmt.get("duration"), 0.0)
    if info.duration <= 0:
        info.duration = _parse_fraction(video.get("duration"), 0.0)

    fps = _parse_fraction(video.get("avg_frame_rate"), 0.0)
    if fps <= 0:
        fps = _parse_fraction(video.get("r_frame_rate"), 25.0)
    info.timebase, info.ntsc, info.fps = resolve_timebase(fps)

    info.width = int(video.get("width") or 1920)
    info.height = int(video.get("height") or 1080)
    info.video_codec = str(video.get("codec_name") or "")
    sar = str(video.get("sample_aspect_ratio") or "1:1").replace(":", "/")
    info.pixel_aspect = _parse_fraction(sar, 1.0) or 1.0
    frames = video.get("nb_frames")
    if frames:
        try:
            info.frame_count = int(frames)
        except (TypeError, ValueError):
            info.frame_count = 0
    if not info.frame_count and info.duration > 0:
        info.frame_count = int(round(info.duration * info.fps))

    info.rotation = _extract_rotation(video)

    if audio is not None:
        info.has_audio = True
        info.audio_channels = int(audio.get("channels") or 2)
        info.audio_sample_rate = int(_parse_fraction(audio.get("sample_rate"), 48000))
        info.audio_codec = str(audio.get("codec_name") or "")
    else:
        info.has_audio = False
        info.audio_channels = 0

    if not info.has_audio:
        raise MediaError(
            f"{path.name} ne contient pas de piste audio.",
            "AutoRush a besoin de la parole pour monter la video.",
        )

    log.info(
        "Media : %s | %.2f s | %dx%d | %.3f fps (timebase %d%s) | audio %d canaux",
        path.name, info.duration, info.width, info.height, info.fps,
        info.timebase, " NTSC" if info.ntsc else "", info.audio_channels,
    )
    return info


def _extract_rotation(video_stream: dict) -> int:
    """Rotation declaree (tag ``rotate`` ou ``side_data_list``)."""
    tags = video_stream.get("tags") or {}
    raw = tags.get("rotate")
    if raw is not None:
        try:
            return int(float(raw)) % 360
        except (TypeError, ValueError):
            pass
    for side in video_stream.get("side_data_list") or []:
        if "rotation" in side:
            try:
                return int(round(-float(side["rotation"]))) % 360
            except (TypeError, ValueError):
                continue
    return 0


# --------------------------------------------------------------------------- #
# Execution
# --------------------------------------------------------------------------- #
_TIME_RE = re.compile(r"out_time_ms=(\d+)")


def run_ffmpeg(
    args: list[str],
    total_duration: float = 0.0,
    on_progress=None,
    description: str = "ffmpeg",
) -> None:
    """Lance ffmpeg et remonte la progression via ``-progress pipe:1``.

    ``on_progress`` recoit une fraction dans [0, 1].
    """
    ffmpeg = find_ffmpeg()
    command = [ffmpeg, "-hide_banner", "-nostdin", "-y"]
    if total_duration > 0 and on_progress is not None:
        command += ["-progress", "pipe:1", "-nostats"]
    else:
        command += ["-loglevel", "error"]
    command += args

    log.debug("%s : %s", description, " ".join(command[:14]) + " ...")

    creation_flags = 0
    if os.name == "nt":  # pragma: no cover - evite la console qui clignote
        creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)

    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=creation_flags,
    )
    assert process.stdout is not None

    if total_duration > 0 and on_progress is not None:
        for line in process.stdout:
            match = _TIME_RE.search(line)
            if match:
                seconds = int(match.group(1)) / 1_000_000.0
                on_progress(min(1.0, seconds / total_duration))
    stdout, stderr = process.communicate()
    del stdout

    if process.returncode != 0:
        tail = "\n".join((stderr or "").strip().splitlines()[-12:])
        raise MediaError(f"{description} a echoue (code {process.returncode}).", tail)


def extract_audio(
    source: str | Path,
    destination: str | Path,
    sample_rate: int = 16000,
    mono: bool = True,
    on_progress=None,
    duration: float = 0.0,
) -> Path:
    """Extrait la piste audio en WAV PCM 16 bits (format attendu par Whisper)."""
    source = Path(source)
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    args = [
        "-i", str(source),
        "-vn",
        "-sn",
        "-dn",
        "-map", "0:a:0",
        "-ac", "1" if mono else "2",
        "-ar", str(sample_rate),
        "-c:a", "pcm_s16le",
        str(destination),
    ]
    run_ffmpeg(args, total_duration=duration, on_progress=on_progress, description="Extraction audio")
    if not destination.exists() or destination.stat().st_size < 1024:
        raise MediaError(
            "L'extraction audio n'a produit aucun son.",
            "La piste audio du rush est-elle vide ?",
        )
    return destination
