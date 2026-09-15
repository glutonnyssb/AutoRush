"""Export XML pour Adobe Premiere Pro (format XMEML v5, dit "FCP7 XML").

Pourquoi ce format
------------------
Premiere Pro importe le XMEML v5 en creant une vraie sequence : pistes video et
audio, points de coupe a l'image, et surtout **effets avec keyframes**. Les
zooms apparaissent donc dans *Options d'effet > Mouvement > Echelle*, avec
leurs keyframes visibles et modifiables. C'est la seule voie d'echange ouverte
qui garantisse ce resultat.

Comment les zooms sont ecrits
-----------------------------
* **Zoom direct** : une valeur d'echelle fixe sur le plan. Le changement est
  donc instantane au point de coupe, exactement comme demande.
* **Zoom progressif** et **zoom lance** : la courbe est echantillonnee puis
  simplifiee (voir ``autorush.zoom.curves``). Premiere interpole lineairement
  entre deux keyframes : en gardant suffisamment de points aux endroits ou la
  courbe se courbe, le rendu est fidele *et* la liste de keyframes reste
  lisible.

Base de temps des keyframes
---------------------------
Dans XMEML, le champ ``<when>`` d'une keyframe se compte dans la base de temps
du clip. Deux conventions existent selon les logiciels : temps **source** (le
plus courant, et celui de Premiere : les keyframes restent collees aux images
du rush) ou temps **clip** (relatif au debut du plan). AutoRush ecrit la
convention "source" par defaut et peut produire en plus une variante "clip"
(``--xml-variante both``) : si jamais les zooms apparaissent figes apres
import, il suffit d'importer l'autre fichier.
"""

from __future__ import annotations

import html
import uuid
from pathlib import Path

from autorush.editing.frames import FrameClip, frame_clips
from autorush.editing.timeline import Timeline
from autorush.errors import ExportError
from autorush.logging_setup import get_logger
from autorush.media.ffmpeg import MediaInfo
from autorush.utils import clamp, format_smpte
from autorush.zoom.planner import ZOOM_DIRECT, ZoomPlan

log = get_logger("export.fcp7xml")

#: Premiere compte le temps en "ticks" : 254 016 000 000 par seconde
PPRO_TICKS_PER_SECOND = 254016000000

#: nombre de canaux audio maximum exportes
MAX_AUDIO_TRACKS = 2


def _escape(text: str) -> str:
    return html.escape(str(text), quote=True)


def _bool(value: bool) -> str:
    return "TRUE" if value else "FALSE"


def _path_to_url(path: Path) -> str:
    """Chemin Windows ou POSIX -> ``file://localhost/...`` accepte par Premiere.

    Un chemin deja absolu est laisse tel quel : resoudre un chemin Windows
    (``C:/...``) depuis un autre systeme y accolerait le dossier courant.
    """
    text = str(path).replace("\\", "/")
    has_drive = len(text) > 1 and text[1] == ":"
    if not has_drive and not text.startswith("/"):
        text = str(Path(path).resolve()).replace("\\", "/")
        has_drive = len(text) > 1 and text[1] == ":"
    if has_drive:
        # C:/Users/... -> file://localhost/C:/Users/...
        return "file://localhost/" + _quote_path(text)
    if not text.startswith("/"):
        text = "/" + text
    return "file://localhost" + _quote_path(text)


def _quote_path(text: str) -> str:
    """Encode les caracteres problematiques d'une URL de fichier."""
    safe = "/:-_.!~*'()ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"
    out: list[str] = []
    for char in text:
        if char in safe:
            out.append(char)
        elif char == " ":
            out.append("%20")
        else:
            out.extend(f"%{byte:02X}" for byte in char.encode("utf-8"))
    return "".join(out)


class Fcp7Writer:
    """Genere le XML d'une sequence Premiere."""

    def __init__(
        self,
        media: MediaInfo,
        timeline: Timeline,
        zooms: ZoomPlan | None = None,
        sequence_name: str = "",
        fps: float = 0.0,
        width: int = 0,
        height: int = 0,
        audio_crossfade: bool = True,
        crossfade_frames: int = 3,
        audio_level_fallback: bool = True,
        keyframe_time_base: str = "source",
        max_keyframes: int = 90,
        keyframe_tolerance: float = 0.22,
    ) -> None:
        self.media = media
        self.timeline = timeline
        self.zooms = zooms
        self.sequence_name = sequence_name or f"{media.name} - AutoRush"

        self.fps = fps if fps > 0 else media.fps
        self.timebase = media.timebase
        self.ntsc = media.ntsc
        if fps > 0 and abs(fps - media.fps) > 0.01:
            from autorush.media.ffmpeg import resolve_timebase

            self.timebase, self.ntsc, self.fps = resolve_timebase(fps)

        self.width = width or media.display_width
        self.height = height or media.display_height
        self.audio_crossfade = audio_crossfade
        self.crossfade_frames = max(0, int(crossfade_frames))
        self.audio_level_fallback = audio_level_fallback
        self.keyframe_time_base = (
            keyframe_time_base if keyframe_time_base in ("source", "clip") else "source"
        )
        self.max_keyframes = max(2, int(max_keyframes))
        self.keyframe_tolerance = max(0.01, float(keyframe_tolerance))

        self.audio_tracks = (
            min(MAX_AUDIO_TRACKS, max(1, media.audio_channels)) if media.has_audio else 0
        )
        self.media_frames = max(1, int(round(media.duration * self.fps)))
        self._clips = self._build_clips()

    # ------------------------------------------------------------------ #
    def _frames(self, seconds: float) -> int:
        return int(round(seconds * self.fps))

    def _ticks(self, seconds: float) -> int:
        return int(round(seconds * PPRO_TICKS_PER_SECOND))

    def _build_clips(self) -> list[FrameClip]:
        """Convertit les plans en images, sans trou ni recouvrement.

        On passe par ``autorush.editing.frames`` : le XML et la preview
        partagent ainsi exactement les memes points de coupe.
        """
        return frame_clips(self.timeline, self.fps, self.media_frames)

    @property
    def total_frames(self) -> int:
        return self._clips[-1].end_frame if self._clips else 0

    # ------------------------------------------------------------------ #
    # briques XML
    # ------------------------------------------------------------------ #
    def _rate(self, indent: str) -> list[str]:
        return [
            f"{indent}<rate>",
            f"{indent}\t<timebase>{self.timebase}</timebase>",
            f"{indent}\t<ntsc>{_bool(self.ntsc)}</ntsc>",
            f"{indent}</rate>",
        ]

    def _file_element(self, indent: str, full: bool) -> list[str]:
        if not full:
            return [f'{indent}<file id="file-1"/>']
        media = self.media
        lines = [
            f'{indent}<file id="file-1">',
            f"{indent}\t<name>{_escape(media.path.name)}</name>",
            f"{indent}\t<pathurl>{_escape(_path_to_url(media.path))}</pathurl>",
        ]
        lines += self._rate(indent + "\t")
        lines += [
            f"{indent}\t<duration>{self.media_frames}</duration>",
            f"{indent}\t<timecode>",
        ]
        lines += self._rate(indent + "\t\t")
        lines += [
            f"{indent}\t\t<string>00:00:00:00</string>",
            f"{indent}\t\t<frame>0</frame>",
            f"{indent}\t\t<displayformat>{'DF' if self.ntsc else 'NDF'}</displayformat>",
            f"{indent}\t</timecode>",
            f"{indent}\t<media>",
            f"{indent}\t\t<video>",
            f"{indent}\t\t\t<samplecharacteristics>",
        ]
        lines += self._rate(indent + "\t\t\t\t")
        lines += [
            f"{indent}\t\t\t\t<width>{self.width}</width>",
            f"{indent}\t\t\t\t<height>{self.height}</height>",
            f"{indent}\t\t\t\t<anamorphic>FALSE</anamorphic>",
            f"{indent}\t\t\t\t<pixelaspectratio>square</pixelaspectratio>",
            f"{indent}\t\t\t\t<fielddominance>none</fielddominance>",
            f"{indent}\t\t\t</samplecharacteristics>",
            f"{indent}\t\t</video>",
        ]
        if self.audio_tracks:
            lines += [
                f"{indent}\t\t<audio>",
                f"{indent}\t\t\t<samplecharacteristics>",
                f"{indent}\t\t\t\t<depth>16</depth>",
                f"{indent}\t\t\t\t<samplerate>{media.audio_sample_rate}</samplerate>",
                f"{indent}\t\t\t</samplecharacteristics>",
                f"{indent}\t\t\t<channelcount>{max(1, media.audio_channels)}</channelcount>",
                f"{indent}\t\t</audio>",
            ]
        lines += [
            f"{indent}\t</media>",
            f"{indent}</file>",
        ]
        return lines

    # ------------------------------------------------------------------ #
    def _motion_filter(self, indent: str, clip: FrameClip) -> list[str]:
        """Filtre "Basic Motion" -> Mouvement/Echelle dans Premiere."""
        event = None
        if self.zooms is not None:
            event = self.zooms.by_shot().get(clip.index)

        if event is None:
            return []

        keyframes = event.keyframes(
            fps=self.fps,
            max_keyframes=self.max_keyframes,
            tolerance=self.keyframe_tolerance,
        )
        static_scale = event.end_scale if event.kind == ZOOM_DIRECT else event.start_scale

        offset = clip.in_frame if self.keyframe_time_base == "source" else 0
        last_frame = offset + clip.length

        lines = [
            f"{indent}<filter>",
            f"{indent}\t<effect>",
            f"{indent}\t\t<name>Basic Motion</name>",
            f"{indent}\t\t<effectid>basic</effectid>",
            f"{indent}\t\t<effectcategory>motion</effectcategory>",
            f"{indent}\t\t<effecttype>motion</effecttype>",
            f"{indent}\t\t<mediatype>video</mediatype>",
            f"{indent}\t\t<pproBypass>false</pproBypass>",
            # -- echelle -------------------------------------------------- #
            f'{indent}\t\t<parameter authoringApp="PremierePro">',
            f"{indent}\t\t\t<parameterid>scale</parameterid>",
            f"{indent}\t\t\t<name>Scale</name>",
            f"{indent}\t\t\t<valuemin>0</valuemin>",
            f"{indent}\t\t\t<valuemax>1000</valuemax>",
            f"{indent}\t\t\t<value>{static_scale:.4f}</value>",
        ]
        for time, value in keyframes:
            when = clamp(offset + round(time * self.fps), offset, last_frame)
            lines.append(
                f"{indent}\t\t\t<keyframe>"
                f"<when>{int(when)}</when><value>{value:.4f}</value>"
                f"</keyframe>"
            )
        lines.append(f"{indent}\t\t</parameter>")

        # -- rotation (laissee a zero, mais presente pour que Premiere
        #    reconnaisse un filtre Basic Motion complet) ------------------ #
        lines += [
            f'{indent}\t\t<parameter authoringApp="PremierePro">',
            f"{indent}\t\t\t<parameterid>rotation</parameterid>",
            f"{indent}\t\t\t<name>Rotation</name>",
            f"{indent}\t\t\t<valuemin>-8640</valuemin>",
            f"{indent}\t\t\t<valuemax>8640</valuemax>",
            f"{indent}\t\t\t<value>0</value>",
            f"{indent}\t\t</parameter>",
        ]

        # -- centre : recadrage vers le point de mise au point ------------ #
        lines += self._center_parameter(indent + "\t\t", event, keyframes, offset, last_frame)

        lines += [
            f'{indent}\t\t<parameter authoringApp="PremierePro">',
            f"{indent}\t\t\t<parameterid>centeroffset</parameterid>",
            f"{indent}\t\t\t<name>Anchor Point</name>",
            f"{indent}\t\t\t<value><horiz>0</horiz><vert>0</vert></value>",
            f"{indent}\t\t</parameter>",
            f"{indent}\t</effect>",
            f"{indent}</filter>",
        ]
        return lines

    def _center_parameter(
        self,
        indent: str,
        event,
        keyframes: list[tuple[float, float]],
        offset: int,
        last_frame: int,
    ) -> list[str]:
        """Deplace le centre pour garder le point de mise au point fixe.

        Quand on agrandit l'image d'un facteur ``s`` autour du centre, un point
        situe a la hauteur normalisee ``f`` se deplace. Pour le laisser en place
        il faut translater l'image de ``(f - 0.5) * (1 - s)``. Avec le point de
        mise au point au centre (valeur par defaut), le decalage est nul.
        """
        shift_x = event.focus_x - 0.5
        shift_y = event.focus_y - 0.5
        lines = [
            f'{indent}<parameter authoringApp="PremierePro">',
            f"{indent}\t<parameterid>center</parameterid>",
            f"{indent}\t<name>Center</name>",
        ]
        if abs(shift_x) < 1e-6 and abs(shift_y) < 1e-6:
            lines += [
                f"{indent}\t<value><horiz>0</horiz><vert>0</vert></value>",
                f"{indent}</parameter>",
            ]
            return lines

        def offsets(scale: float) -> tuple[float, float]:
            factor = 1.0 - scale / 100.0
            return shift_x * factor, shift_y * factor

        if not keyframes:
            horiz, vert = offsets(event.end_scale)
            lines += [
                f"{indent}\t<value><horiz>{horiz:.6f}</horiz>"
                f"<vert>{vert:.6f}</vert></value>",
                f"{indent}</parameter>",
            ]
            return lines

        for time, value in keyframes:
            when = clamp(offset + round(time * self.fps), offset, last_frame)
            horiz, vert = offsets(value)
            lines.append(
                f"{indent}\t<keyframe><when>{int(when)}</when>"
                f"<value><horiz>{horiz:.6f}</horiz><vert>{vert:.6f}</vert></value>"
                f"</keyframe>"
            )
        lines.append(f"{indent}</parameter>")
        return lines

    # ------------------------------------------------------------------ #
    def _audio_levels_filter(self, indent: str, clip: FrameClip, fade: int) -> list[str]:
        """Micro-fondus de niveau, quand une transition n'est pas possible."""
        if fade <= 0:
            return []
        offset = clip.in_frame if self.keyframe_time_base == "source" else 0
        length = clip.length
        fade = min(fade, max(1, length // 3))
        points = [
            (offset, 0.0),
            (offset + fade, 1.0),
            (offset + max(fade, length - fade), 1.0),
            (offset + length, 0.0),
        ]
        lines = [
            f"{indent}<filter>",
            f"{indent}\t<effect>",
            f"{indent}\t\t<name>Audio Levels</name>",
            f"{indent}\t\t<effectid>audiolevels</effectid>",
            f"{indent}\t\t<effectcategory>audiolevels</effectcategory>",
            f"{indent}\t\t<effecttype>audiolevels</effecttype>",
            f"{indent}\t\t<mediatype>audio</mediatype>",
            f"{indent}\t\t<pproBypass>false</pproBypass>",
            f"{indent}\t\t<parameter>",
            f"{indent}\t\t\t<parameterid>level</parameterid>",
            f"{indent}\t\t\t<name>Level</name>",
            f"{indent}\t\t\t<valuemin>0</valuemin>",
            f"{indent}\t\t\t<valuemax>3.98109</valuemax>",
        ]
        for when, value in points:
            lines.append(
                f"{indent}\t\t\t<keyframe><when>{int(when)}</when>"
                f"<value>{value:.4f}</value></keyframe>"
            )
        lines += [
            f"{indent}\t\t</parameter>",
            f"{indent}\t</effect>",
            f"{indent}</filter>",
        ]
        return lines

    # ------------------------------------------------------------------ #
    def _clipitem(
        self,
        indent: str,
        clip: FrameClip,
        media_type: str,
        clip_id: str,
        first_file: bool,
        audio_channel: int = 1,
        audio_fade: int = 0,
    ) -> list[str]:
        name = self.media.path.name
        extra = ' premiereChannelType="stereo"' if media_type == "audio" else ""
        lines = [
            f'{indent}<clipitem id="{clip_id}"{extra}>',
            f"{indent}\t<masterclipid>masterclip-1</masterclipid>",
            f"{indent}\t<name>{_escape(name)}</name>",
            f"{indent}\t<enabled>TRUE</enabled>",
            f"{indent}\t<duration>{self.media_frames}</duration>",
        ]
        lines += self._rate(indent + "\t")
        lines += [
            f"{indent}\t<start>{clip.start_frame}</start>",
            f"{indent}\t<end>{clip.end_frame}</end>",
            f"{indent}\t<in>{clip.in_frame}</in>",
            f"{indent}\t<out>{clip.out_frame}</out>",
            f"{indent}\t<pproTicksIn>{self._ticks(clip.in_frame / self.fps)}</pproTicksIn>",
            f"{indent}\t<pproTicksOut>{self._ticks(clip.out_frame / self.fps)}</pproTicksOut>",
            f"{indent}\t<alphatype>none</alphatype>",
            f"{indent}\t<pixelaspectratio>square</pixelaspectratio>",
            f"{indent}\t<anamorphic>FALSE</anamorphic>",
        ]
        lines += self._file_element(indent + "\t", first_file)

        if media_type == "audio":
            lines += [
                f"{indent}\t<sourcetrack>",
                f"{indent}\t\t<mediatype>audio</mediatype>",
                f"{indent}\t\t<trackindex>{audio_channel}</trackindex>",
                f"{indent}\t</sourcetrack>",
            ]
            lines += self._audio_levels_filter(indent + "\t", clip, audio_fade)
        else:
            lines += [
                f"{indent}\t<sourcetrack>",
                f"{indent}\t\t<mediatype>video</mediatype>",
                f"{indent}\t\t<trackindex>1</trackindex>",
                f"{indent}\t</sourcetrack>",
            ]
            lines += self._motion_filter(indent + "\t", clip)

        lines += self._links(indent + "\t", clip)
        lines.append(f"{indent}</clipitem>")
        return lines

    def _links(self, indent: str, clip: FrameClip) -> list[str]:
        """Lie le plan video et ses pistes audio (groupe dans Premiere)."""
        lines: list[str] = [
            f"{indent}<link>",
            f"{indent}\t<linkclipref>clipitem-v{clip.index + 1}</linkclipref>",
            f"{indent}\t<mediatype>video</mediatype>",
            f"{indent}\t<trackindex>1</trackindex>",
            f"{indent}\t<clipindex>{clip.index + 1}</clipindex>",
            f"{indent}</link>",
        ]
        for channel in range(1, self.audio_tracks + 1):
            lines += [
                f"{indent}<link>",
                f"{indent}\t<linkclipref>clipitem-a{channel}-{clip.index + 1}</linkclipref>",
                f"{indent}\t<mediatype>audio</mediatype>",
                f"{indent}\t<trackindex>{channel}</trackindex>",
                f"{indent}\t<clipindex>{clip.index + 1}</clipindex>",
                f"{indent}\t<groupindex>1</groupindex>",
                f"{indent}</link>",
            ]
        return lines

    # ------------------------------------------------------------------ #
    def _transitions(self, indent: str) -> dict[int, list[str]]:
        """Transitions audio (fondu enchaine) aux points de coupe.

        Une transition demande des poignees media de chaque cote. Quand elles
        manquent, le point de coupe est traite par un micro-fondu de niveau.
        """
        result: dict[int, list[str]] = {}
        if not self.audio_crossfade or self.crossfade_frames <= 0:
            return result
        half = max(1, self.crossfade_frames // 2)
        for position in range(1, len(self._clips)):
            left = self._clips[position - 1]
            right = self._clips[position]
            if left.out_frame + half > self.media_frames:
                continue
            if right.in_frame - half < 0:
                continue
            if left.length <= half * 2 or right.length <= half * 2:
                continue
            cut = right.start_frame
            lines = [
                f"{indent}<transitionitem>",
            ]
            lines += self._rate(indent + "\t")
            lines += [
                f"{indent}\t<start>{cut - half}</start>",
                f"{indent}\t<end>{cut + half}</end>",
                f"{indent}\t<alignment>center</alignment>",
                f"{indent}\t<cutPointTicks>{self._ticks(cut / self.fps)}</cutPointTicks>",
                f"{indent}\t<effect>",
                f"{indent}\t\t<name>Cross Fade (+3dB)</name>",
                f"{indent}\t\t<effectid>KGAudioTransitionCurves</effectid>",
                f"{indent}\t\t<effectcategory>Dissolve</effectcategory>",
                f"{indent}\t\t<effecttype>transition</effecttype>",
                f"{indent}\t\t<mediatype>audio</mediatype>",
                f"{indent}\t\t<wipecode>0</wipecode>",
                f"{indent}\t\t<wipeaccuracy>100</wipeaccuracy>",
                f"{indent}\t\t<startratio>0</startratio>",
                f"{indent}\t\t<endratio>1</endratio>",
                f"{indent}\t\t<reverse>FALSE</reverse>",
                f"{indent}\t</effect>",
                f"{indent}</transitionitem>",
            ]
            result[position] = lines
        return result

    # ------------------------------------------------------------------ #
    def render(self) -> str:
        """Retourne le XML complet."""
        if not self._clips:
            raise ExportError(
                "Aucun plan a exporter : le montage est vide.",
                "Verifiez que le rush contient de la parole.",
            )

        transitions = self._transitions("\t\t\t\t\t")
        fallback_fade = (
            max(1, self.crossfade_frames)
            if (self.audio_level_fallback and self.audio_crossfade)
            else 0
        )

        lines: list[str] = [
            '<?xml version="1.0" encoding="UTF-8"?>',
            "<!DOCTYPE xmeml>",
            '<xmeml version="5">',
            '\t<sequence id="sequence-1">',
            f"\t\t<uuid>{uuid.uuid5(uuid.NAMESPACE_URL, str(self.media.path)).urn[9:]}</uuid>",
            f"\t\t<duration>{self.total_frames}</duration>",
        ]
        lines += self._rate("\t\t")
        lines += [
            f"\t\t<name>{_escape(self.sequence_name)}</name>",
            "\t\t<media>",
            "\t\t\t<video>",
            "\t\t\t\t<format>",
            "\t\t\t\t\t<samplecharacteristics>",
        ]
        lines += self._rate("\t\t\t\t\t\t")
        lines += [
            f"\t\t\t\t\t\t<width>{self.width}</width>",
            f"\t\t\t\t\t\t<height>{self.height}</height>",
            "\t\t\t\t\t\t<anamorphic>FALSE</anamorphic>",
            "\t\t\t\t\t\t<pixelaspectratio>square</pixelaspectratio>",
            "\t\t\t\t\t\t<fielddominance>none</fielddominance>",
            "\t\t\t\t\t\t<colordepth>24</colordepth>",
            "\t\t\t\t\t</samplecharacteristics>",
            "\t\t\t\t</format>",
            "\t\t\t\t<track>",
        ]

        first_file = True
        for clip in self._clips:
            lines += self._clipitem(
                "\t\t\t\t\t",
                clip,
                "video",
                f"clipitem-v{clip.index + 1}",
                first_file,
            )
            first_file = False
        lines += [
            "\t\t\t\t\t<enabled>TRUE</enabled>",
            "\t\t\t\t\t<locked>FALSE</locked>",
            "\t\t\t\t</track>",
            "\t\t\t</video>",
        ]

        if self.audio_tracks:
            lines += [
                "\t\t\t<audio>",
                f"\t\t\t\t<numOutputChannels>{self.audio_tracks}</numOutputChannels>",
                "\t\t\t\t<format>",
                "\t\t\t\t\t<samplecharacteristics>",
                "\t\t\t\t\t\t<depth>16</depth>",
                f"\t\t\t\t\t\t<samplerate>{self.media.audio_sample_rate}</samplerate>",
                "\t\t\t\t\t</samplecharacteristics>",
                "\t\t\t\t</format>",
            ]
            for channel in range(1, self.audio_tracks + 1):
                lines += [
                    f'\t\t\t\t<track currentExplodedTrackIndex="{channel - 1}"'
                    f' totalExplodedTrackCount="{self.audio_tracks}"'
                    ' premiereTrackType="Stereo">',
                ]
                for clip in self._clips:
                    needs_fade = clip.index not in transitions and (
                        clip.index + 1 not in transitions
                    )
                    lines += self._clipitem(
                        "\t\t\t\t\t",
                        clip,
                        "audio",
                        f"clipitem-a{channel}-{clip.index + 1}",
                        False,
                        audio_channel=channel,
                        audio_fade=fallback_fade if needs_fade else 0,
                    )
                    if clip.index + 1 in transitions:
                        lines += transitions[clip.index + 1]
                lines += [
                    "\t\t\t\t\t<enabled>TRUE</enabled>",
                    "\t\t\t\t\t<locked>FALSE</locked>",
                    f"\t\t\t\t\t<outputchannelindex>{channel}</outputchannelindex>",
                    "\t\t\t\t</track>",
                ]
            lines.append("\t\t\t</audio>")

        lines += [
            "\t\t</media>",
            "\t\t<timecode>",
        ]
        lines += self._rate("\t\t\t")
        lines += [
            f"\t\t\t<string>{format_smpte(0.0, self.fps)}</string>",
            "\t\t\t<frame>0</frame>",
            f"\t\t\t<displayformat>{'DF' if self.ntsc else 'NDF'}</displayformat>",
            "\t\t</timecode>",
            "\t\t<labels><label2>Forest</label2></labels>",
            "\t</sequence>",
            "</xmeml>",
            "",
        ]
        return "\n".join(lines)


# --------------------------------------------------------------------------- #
def write_premiere_xml(
    path: str | Path,
    media: MediaInfo,
    timeline: Timeline,
    zooms: ZoomPlan | None = None,
    sequence_name: str = "",
    fps: float = 0.0,
    width: int = 0,
    height: int = 0,
    audio_crossfade: bool = True,
    crossfade_frames: int = 3,
    audio_level_fallback: bool = True,
    keyframe_time_base: str = "source",
    max_keyframes: int = 90,
    keyframe_tolerance: float = 0.22,
) -> Path:
    """Ecrit la sequence Premiere et retourne le chemin du fichier."""
    writer = Fcp7Writer(
        media=media,
        timeline=timeline,
        zooms=zooms,
        sequence_name=sequence_name,
        fps=fps,
        width=width,
        height=height,
        audio_crossfade=audio_crossfade,
        crossfade_frames=crossfade_frames,
        audio_level_fallback=audio_level_fallback,
        keyframe_time_base=keyframe_time_base,
        max_keyframes=max_keyframes,
        keyframe_tolerance=keyframe_tolerance,
    )
    xml = writer.render()
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(xml, encoding="utf-8")
    log.info(
        "Sequence Premiere ecrite : %s (%d plans, %d images, keyframes base %s)",
        path.name, len(writer._clips), writer.total_frames, writer.keyframe_time_base,
    )
    return path
