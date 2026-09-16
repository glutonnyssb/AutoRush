"""Reglages d'AutoRush : styles de rythme, seuils d'analyse, zooms, export.

Un seul objet ``Settings`` circule dans tout le pipeline. Il est serialisable en
JSON (pour le rapport et pour rejouer un traitement a l'identique).

Philosophie des seuils
----------------------
Tous les seuils de suppression de parole sont volontairement conservateurs.
Chaque decision porte une confiance dans [0, 1] ; en dessous de
``min_delete_confidence`` la decision est retrogradee en simple signalement :
le segment est **garde** et apparait dans le rapport.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any, Literal

from autorush.errors import AutoRushError

StyleName = Literal["naturel", "dynamique", "tres_dynamique"]

STYLE_LABELS: dict[str, str] = {
    "naturel": "Naturel",
    "dynamique": "Dynamique",
    "tres_dynamique": "Tres dynamique",
}

STYLE_ALIASES: dict[str, str] = {
    "naturel": "naturel",
    "natural": "naturel",
    "nature": "naturel",
    "calme": "naturel",
    "dynamique": "dynamique",
    "dynamic": "dynamique",
    "punchy": "dynamique",
    "tres_dynamique": "tres_dynamique",
    "tres-dynamique": "tres_dynamique",
    "tresdynamique": "tres_dynamique",
    "very_dynamic": "tres_dynamique",
    "very-dynamic": "tres_dynamique",
    "max": "tres_dynamique",
}


def resolve_style(name: str) -> str:
    """Accepte ``Tres dynamique``, ``very-dynamic``... -> cle canonique."""
    from autorush.utils import normalize_text

    key = normalize_text(name or "").replace(" ", "_")
    if key in STYLE_ALIASES:
        return STYLE_ALIASES[key]
    raise AutoRushError(
        f"Style inconnu : {name!r}",
        "Styles disponibles : naturel, dynamique, tres_dynamique.",
    )


# --------------------------------------------------------------------------- #
# Silences
# --------------------------------------------------------------------------- #
@dataclass
class SilenceSettings:
    """Gestion des silences.

    Un blanc entre deux mots est classe puis traite :

    ``respiration``    -> tres court, garde (sinon le montage etouffe) ;
    ``pause_naturelle``-> ponctuation forte, on garde une partie ;
    ``hesitation``     -> blanc moyen sans ponctuation, on serre ;
    ``pause_longue``   -> au-dela de ``long_pause_threshold``, on coupe franchement ;
    ``mauvaise_prise`` -> blanc adjacent a une zone supprimee, on coupe tout.
    """

    #: en dessous, le blanc n'est jamais touche (respiration / articulation)
    keep_below: float = 0.34
    #: duree cible d'un blanc conserve apres raccourcissement
    target_gap: float = 0.26
    #: au-dela, le blanc est considere comme une pause longue
    long_pause_threshold: float = 1.10
    #: duree cible d'une pause longue (on en garde un peu pour la respiration)
    long_pause_target: float = 0.34
    #: blanc conserve apres une ponctuation forte (. ! ?) - respiration de sens
    sentence_pause_target: float = 0.40
    #: blanc conserve apres une virgule
    comma_pause_target: float = 0.24
    #: marge conservee avant le premier mot d'un plan (evite de couper l'attaque)
    pad_in: float = 0.075
    #: marge conservee apres le dernier mot d'un plan (evite de couper la chute)
    pad_out: float = 0.130
    #: silence de tete / de queue du rush conserve
    edge_silence: float = 0.30
    #: duree minimale d'un plan apres coupe (evite les micro-plans robotiques)
    min_shot_duration: float = 0.42
    #: on ne coupe pas un blanc s'il faut retirer moins que ca (bruit de coupe)
    min_removal: float = 0.09
    #: marge toujours preservee autour d'un mot conserve, quitte a la prendre
    #: sur la parole supprimee. Elle absorbe l'imprecision du moteur de
    #: transcription, qui rapporte les fins de mot trop tot et les attaques
    #: trop tard. Ce n'est pas un choix de rythme : c'est une compensation
    #: technique, donc identique pour les trois styles.
    word_edge_tolerance: float = 0.060
    #: garde une respiration audible si l'energie du blanc est elevee
    breath_energy_ratio: float = 0.055
    #: energie maximale d'un blanc encore considere comme une respiration
    breath_max_energy: float = 0.50
    #: duree maximale d'une respiration reconnue comme telle
    breath_max_duration: float = 0.55
    #: duree conservee au maximum pour une respiration audible
    breath_keep: float = 0.32


# --------------------------------------------------------------------------- #
# Hesitations / bafouillages
# --------------------------------------------------------------------------- #
@dataclass
class DisfluencySettings:
    """Nettoyage des tics de parole."""

    remove_fillers: bool = True
    #: tics de langage ("du coup", "genre", "like") - desactive par defaut car
    #: ils portent parfois du sens ; active en mode tres dynamique.
    remove_soft_fillers: bool = False
    remove_stutters: bool = True
    remove_abandoned_words: bool = True
    #: confiance minimale pour supprimer un tic (sinon simple signalement)
    min_delete_confidence: float = 0.60
    #: un "euh" isole plus long que ca est garde (probable mot mal transcrit)
    filler_max_duration: float = 1.20
    #: duree maximale d'un debut de mot abandonne ("Mal-")
    abandoned_max_duration: float = 0.85
    #: nombre minimal de lettres communes pour reconnaitre "Mal-" / "Malgre"
    abandoned_min_prefix: int = 2
    #: on ne supprime jamais plus de N mots consecutifs au titre du bafouillage
    max_consecutive_removed: int = 4
    #: repetitions volontaires a proteger ("tres tres fort")
    protect_intensifiers: bool = True
    #: garde le dernier mot d'une repetition (et non le premier) : meilleure diction
    keep_last_repetition: bool = True
    #: une repetition espacee de plus de N secondes n'est pas un bafouillage
    repetition_max_gap: float = 0.90


# --------------------------------------------------------------------------- #
# Reprises de phrases
# --------------------------------------------------------------------------- #
@dataclass
class RetakeSettings:
    """Detection des reprises (plusieurs tentatives pour une meme phrase)."""

    enabled: bool = True
    #: similarite minimale quand un marqueur explicite ("je recommence") est present
    similarity_with_marker: float = 0.32
    #: similarite minimale sans marqueur (plus severe : la preuve doit venir
    #: du seul vocabulaire). Mesure sur rush reel : une vraie reprise depasse
    #: 0.56, deux phrases voisines du meme sujet montent au plus a 0.46.
    similarity_without_marker: float = 0.50
    #: fenetre de recherche de la nouvelle tentative (secondes)
    search_window: float = 26.0
    #: nombre d'enonces separant au maximum deux tentatives
    max_utterance_distance: int = 5
    #: mots de contenu communs minimum entre deux tentatives
    min_shared_content_words: int = 2
    #: une tentative abandonnee de plus de N secondes demande un marqueur explicite
    long_attempt_duration: float = 7.5
    #: part de l'information de la tentative qui peut disparaitre. Compter les
    #: mots perdus punissait les reprises qui abregent ("Au debut du Ultimate"
    #: -> "Au debut") autant que celles qui perdent l'essentiel.
    max_information_loss_ratio: float = 0.50
    #: au-dela de ce nombre de tokens, l'enonce est traite comme une phrase complete
    complete_utterance_tokens: int = 7
    #: confiance minimale pour supprimer reellement (sinon : signalement)
    min_delete_confidence: float = 0.60
    #: part maximale de la parole supprimable par la detection de reprises.
    #: Ce filet ne sert qu'a rattraper un emballement de l'analyse sur un rush
    #: entier : il ne doit jamais contredire une consigne explicite.
    max_removed_speech_ratio: float = 0.55
    #: le filet ne s'applique qu'au-dela de cette duree de parole (secondes).
    #: Sur un extrait de quelques secondes, une seule reprise depasserait
    #: mecaniquement n'importe quel pourcentage.
    cap_min_speech_duration: float = 60.0
    #: une suppression au moins aussi sure que cela n'est jamais annulee par le
    #: filet : c'est le cas quand la personne a dit « je recommence ».
    cap_exempt_confidence: float = 0.75


# --------------------------------------------------------------------------- #
# Fragments
# --------------------------------------------------------------------------- #
@dataclass
class FragmentSettings:
    """Petits morceaux orphelins laisses entre deux tentatives."""

    enabled: bool = True
    #: duree maximale d'un fragment supprimable
    max_duration: float = 2.6
    #: nombre de tokens maximum d'un fragment supprimable
    max_tokens: int = 6
    #: un fragment doit etre entoure d'au moins un indice (marqueur ou reprise)
    require_context: bool = True
    min_delete_confidence: float = 0.62


# --------------------------------------------------------------------------- #
# Raccords
# --------------------------------------------------------------------------- #
@dataclass
class SeamSettings:
    """Controle qualite des raccords apres montage."""

    enabled: bool = True
    #: un plan plus court que ca declenche une alerte
    min_shot_warn: float = 0.55
    #: signale un raccord ou la phrase repart sur un mot de liaison orphelin
    flag_dangling_connectors: bool = True
    #: signale un raccord ou un marqueur de correction a survecu
    flag_surviving_markers: bool = True
    #: signale deux coupes trop rapprochees
    min_cut_spacing_warn: float = 0.75


# --------------------------------------------------------------------------- #
# Zooms
# --------------------------------------------------------------------------- #
@dataclass
class ZoomSettings:
    """Zooms automatiques.

    ``intensity`` (0-100) est le curseur unique expose dans l'interface : il
    pilote l'amplitude ET la densite des zooms.
    """

    enabled: bool = True
    intensity: float = 55.0
    #: amplitude de zoom (en %) pour intensity=0 et intensity=100
    scale_min_delta: float = 4.0
    scale_max_delta: float = 20.0
    #: bornes dures : jamais de zoom arriere (pas de bords noirs), jamais > 145 %
    scale_floor: float = 100.0
    scale_ceiling: float = 145.0
    #: duree typique d'un zoom lance
    launched_duration: float = 1.05
    launched_duration_jitter: float = 0.18
    #: depassement du zoom lance, en fraction de l'amplitude (effet ballon)
    launched_overshoot: float = 0.11
    #: raideur de la decelerration du zoom lance (plus grand = depart plus sec)
    launched_stiffness: float = 5.6
    #: duree minimale d'un plan pour recevoir un zoom progressif
    progressive_min_shot: float = 2.6
    #: duree minimale d'un plan pour recevoir un zoom lance
    launched_min_shot: float = 1.5
    #: duree minimale d'un plan pour recevoir un zoom direct
    direct_min_shot: float = 0.8
    #: ecart minimal entre deux zooms (secondes) - anti-fatigue
    min_spacing: float = 7.0
    #: au-dela de cette duree sans zoom, on force un mouvement
    max_static_duration: float = 26.0
    #: nombre maximum de zooms par minute
    max_per_minute: float = 5.0
    #: interdit deux fois le meme type d'affilee
    avoid_repeating_type: bool = True
    #: n'utilise jamais un plan qui commence juste apres une hesitation supprimee
    avoid_after_disfluency: bool = True
    #: point de mise au point vertical (0 = haut, 0.5 = centre) pour le recadrage
    focus_y: float = 0.5
    focus_x: float = 0.5
    #: probabilites relatives des trois types (normalisees ensuite)
    weight_direct: float = 1.0
    weight_progressive: float = 1.0
    weight_launched: float = 1.1
    #: nombre maximum de keyframes ecrites par parametre anime
    max_keyframes: int = 90
    #: tolerance d'echantillonnage de la courbe (en % de scale)
    keyframe_tolerance: float = 0.22


# --------------------------------------------------------------------------- #
# Transcription
# --------------------------------------------------------------------------- #
@dataclass
class TranscriptionSettings:
    """Reglages du moteur de transcription."""

    model: str = "large-v3"
    #: ``auto`` = detection ; ``fr``/``en`` = force ; ``multi`` = detection par bloc
    language: str = "auto"
    device: str = "auto"  # auto | cpu | cuda
    compute_type: str = "auto"  # auto | int8 | int8_float16 | float16 | float32
    beam_size: int = 5
    vad_filter: bool = True
    vad_min_silence_ms: int = 250
    condition_on_previous_text: bool = False
    temperature: tuple[float, ...] = (0.0, 0.2, 0.4)
    word_timestamps: bool = True
    #: taille des blocs pour la detection de langue en mode ``multi`` (secondes)
    multilang_chunk: float = 30.0
    #: cache les transcriptions (clef = empreinte du fichier + reglages)
    use_cache: bool = True
    #: nombre de threads CPU (0 = automatique)
    cpu_threads: int = 0
    initial_prompt: str = ""


# --------------------------------------------------------------------------- #
# Export
# --------------------------------------------------------------------------- #
@dataclass
class ExportSettings:
    """Fichiers de sortie."""

    write_premiere_xml: bool = True
    write_edl: bool = True
    write_json: bool = True
    write_report_html: bool = True
    write_report_markdown: bool = True
    write_preview: bool = False

    #: sequence Premiere : 0 = herite de la source
    fps: float = 0.0
    width: int = 0
    height: int = 0

    #: fondus audio aux points de coupe
    audio_crossfade: bool = True
    audio_crossfade_frames: int = 3
    #: micro-fondu de niveau quand aucune poignee media n'est disponible
    audio_level_fallback: bool = True
    #: base de temps des keyframes du XML FCP7 : ``source`` (defaut) ou ``clip``
    keyframe_time_base: str = "source"
    #: nom de la sequence dans Premiere ( {name} = nom du rush )
    sequence_name: str = "{name} - AutoRush"

    #: preview MP4
    preview_height: int = 720
    preview_crf: int = 20
    preview_preset: str = "veryfast"
    preview_audio_bitrate: str = "160k"
    #: rendu de la preview : ``ffmpeg`` (segments + concat)
    preview_threads: int = 0


# --------------------------------------------------------------------------- #
# Reglages globaux
# --------------------------------------------------------------------------- #
@dataclass
class Settings:
    """Ensemble des reglages d'un traitement."""

    style: str = "dynamique"
    silence: SilenceSettings = field(default_factory=SilenceSettings)
    disfluency: DisfluencySettings = field(default_factory=DisfluencySettings)
    retake: RetakeSettings = field(default_factory=RetakeSettings)
    fragment: FragmentSettings = field(default_factory=FragmentSettings)
    seam: SeamSettings = field(default_factory=SeamSettings)
    zoom: ZoomSettings = field(default_factory=ZoomSettings)
    transcription: TranscriptionSettings = field(default_factory=TranscriptionSettings)
    export: ExportSettings = field(default_factory=ExportSettings)

    #: mode prudent renforce : aucune suppression de parole, silences seulement
    silences_only: bool = False
    #: conserve tout et se contente de signaler (utile pour auditer un rush)
    dry_run_decisions: bool = False
    #: graine du generateur pseudo-aleatoire (variete des zooms reproductible)
    seed: int = 20260915

    # ------------------------------------------------------------------ #
    def with_style(self, style: str) -> Settings:
        """Retourne une copie avec le preset de rythme applique."""
        return apply_style(self, style)

    # ------------------------------------------------------------------ #
    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["transcription"]["temperature"] = list(self.transcription.temperature)
        return data

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=False)

    def save(self, path: str | Path) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.to_json(), encoding="utf-8")
        return path

    # ------------------------------------------------------------------ #
    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Settings:
        """Reconstruit des reglages depuis un dictionnaire (tolerant)."""
        sections = {
            "silence": SilenceSettings,
            "disfluency": DisfluencySettings,
            "retake": RetakeSettings,
            "fragment": FragmentSettings,
            "seam": SeamSettings,
            "zoom": ZoomSettings,
            "transcription": TranscriptionSettings,
            "export": ExportSettings,
        }
        kwargs: dict[str, Any] = {}
        for key, klass in sections.items():
            raw = data.get(key) or {}
            valid = {f for f in klass.__dataclass_fields__}  # type: ignore[attr-defined]
            filtered = {k: v for k, v in raw.items() if k in valid}
            if key == "transcription" and "temperature" in filtered:
                filtered["temperature"] = tuple(filtered["temperature"])
            kwargs[key] = klass(**filtered)
        for key in ("style", "silences_only", "dry_run_decisions", "seed"):
            if key in data:
                kwargs[key] = data[key]
        settings = cls(**kwargs)
        return settings

    @classmethod
    def load(cls, path: str | Path) -> Settings:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls.from_dict(raw)

    @classmethod
    def for_style(cls, style: str) -> Settings:
        return apply_style(cls(), style)


# --------------------------------------------------------------------------- #
# Presets de rythme
# --------------------------------------------------------------------------- #
#: Chaque preset ecrase un sous-ensemble de champs. Les valeurs sont le fruit du
#: compromis "serrer le rythme sans hacher la parole".
STYLE_PRESETS: dict[str, dict[str, dict[str, Any]]] = {
    "naturel": {
        "silence": {
            "keep_below": 0.42,
            "target_gap": 0.34,
            "long_pause_threshold": 1.40,
            "long_pause_target": 0.46,
            "sentence_pause_target": 0.52,
            "comma_pause_target": 0.32,
            "pad_in": 0.090,
            "pad_out": 0.150,
            "edge_silence": 0.40,
            "min_shot_duration": 0.50,
        },
        "disfluency": {
            "remove_fillers": True,
            "remove_soft_fillers": False,
            "remove_stutters": True,
            "remove_abandoned_words": True,
            "max_consecutive_removed": 3,
            "min_delete_confidence": 0.64,
        },
        "retake": {
            "similarity_with_marker": 0.36,
            "similarity_without_marker": 0.56,
            "min_delete_confidence": 0.64,
            "max_removed_speech_ratio": 0.5,
        },
        "fragment": {"max_duration": 2.2, "max_tokens": 5, "min_delete_confidence": 0.66},
        "zoom": {
            "intensity": 38.0,
            "min_spacing": 11.0,
            "max_per_minute": 3.0,
            "max_static_duration": 34.0,
            "weight_direct": 0.8,
            "weight_progressive": 1.4,
            "weight_launched": 0.7,
            "launched_overshoot": 0.085,
        },
    },
    "dynamique": {
        "silence": {
            "keep_below": 0.30,
            "target_gap": 0.22,
            "long_pause_threshold": 0.95,
            "long_pause_target": 0.30,
            "sentence_pause_target": 0.34,
            "comma_pause_target": 0.20,
            "pad_in": 0.070,
            "pad_out": 0.120,
            "edge_silence": 0.25,
            "min_shot_duration": 0.42,
        },
        "disfluency": {
            "remove_fillers": True,
            "remove_soft_fillers": False,
            "remove_stutters": True,
            "remove_abandoned_words": True,
            "max_consecutive_removed": 4,
            "min_delete_confidence": 0.60,
        },
        "retake": {
            "similarity_with_marker": 0.32,
            "similarity_without_marker": 0.50,
            "min_delete_confidence": 0.60,
            "max_removed_speech_ratio": 0.55,
        },
        "fragment": {"max_duration": 2.6, "max_tokens": 6, "min_delete_confidence": 0.62},
        "zoom": {
            "intensity": 55.0,
            "min_spacing": 6.5,
            "max_per_minute": 5.5,
            "max_static_duration": 22.0,
            "weight_direct": 1.0,
            "weight_progressive": 1.0,
            "weight_launched": 1.2,
            "launched_overshoot": 0.11,
        },
    },
    "tres_dynamique": {
        "silence": {
            "keep_below": 0.19,
            "target_gap": 0.14,
            "long_pause_threshold": 0.62,
            "long_pause_target": 0.20,
            "sentence_pause_target": 0.22,
            "comma_pause_target": 0.13,
            "pad_in": 0.055,
            "pad_out": 0.100,
            "edge_silence": 0.18,
            "min_shot_duration": 0.36,
        },
        "disfluency": {
            "remove_fillers": True,
            "remove_soft_fillers": True,
            "remove_stutters": True,
            "remove_abandoned_words": True,
            "max_consecutive_removed": 5,
            "min_delete_confidence": 0.56,
        },
        "retake": {
            "similarity_with_marker": 0.28,
            "similarity_without_marker": 0.46,
            "min_delete_confidence": 0.56,
            "max_removed_speech_ratio": 0.62,
        },
        "fragment": {"max_duration": 3.0, "max_tokens": 7, "min_delete_confidence": 0.58},
        "zoom": {
            "intensity": 72.0,
            "min_spacing": 4.5,
            "max_per_minute": 8.0,
            "max_static_duration": 15.0,
            "weight_direct": 1.3,
            "weight_progressive": 0.9,
            "weight_launched": 1.5,
            "launched_overshoot": 0.13,
        },
    },
}


def apply_style(settings: Settings, style: str) -> Settings:
    """Applique un preset de rythme et retourne une **nouvelle** instance."""
    key = resolve_style(style)
    preset = STYLE_PRESETS[key]
    new = Settings.from_dict(settings.to_dict())
    new.style = key
    for section_name, overrides in preset.items():
        section = getattr(new, section_name)
        setattr(new, section_name, replace(section, **overrides))
    return new


def style_label(style: str) -> str:
    return STYLE_LABELS.get(resolve_style(style), style)
