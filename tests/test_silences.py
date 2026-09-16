"""Traitement des blancs et effet reel des styles de rythme."""

from __future__ import annotations

import numpy as np
import pytest
from conftest import make_transcript

from autorush.analysis.decisions import analyze
from autorush.analysis.silences import plan_silences, summarize
from autorush.config import Settings
from autorush.media.audio import AudioProfile

LINES = [
    ("Bonjour a tous et bienvenue dans cette video.", 0.8),
    ("Aujourd'hui je vais vous parler du tournoi.", 0.9),
    ("Le niveau etait vraiment tres eleve.", 1.6),
    ("Et la finale a ete incroyable.", 0.5),
    ("Merci d'avoir regarde jusqu'au bout.", 2.4),
]


def removed_total(transcript, settings, profile=None):
    gaps = plan_silences(
        transcript, set(), settings.silence, profile, transcript.duration
    )
    return sum(gap.removed for gap in gaps)


def test_les_styles_donnent_des_rythmes_differents():
    """Le mode dynamique doit reellement serrer davantage que le naturel."""
    transcript = make_transcript(LINES)
    durations = {}
    for style in ("naturel", "dynamique", "tres_dynamique"):
        settings = Settings.for_style(style)
        result = analyze(transcript, settings, None, transcript.duration)
        durations[style] = result.timeline.duration
    assert durations["naturel"] > durations["dynamique"] > durations["tres_dynamique"]


def test_un_blanc_court_nest_jamais_touche():
    transcript = make_transcript([("Un test simple ici.", 0.6)])
    settings = Settings.for_style("naturel")
    gaps = plan_silences(
        transcript, set(), settings.silence, None, transcript.duration
    )
    for gap in gaps:
        if gap.kind == "respiration" and gap.duration <= settings.silence.keep_below:
            assert gap.removal is None


def test_une_pause_longue_est_resserree_sans_disparaitre():
    transcript = make_transcript(
        [("Premiere phrase ici.", 0.6), ("Deuxieme phrase la.", 3.5)]
    )
    settings = Settings.for_style("dynamique")
    gaps = plan_silences(
        transcript, set(), settings.silence, None, transcript.duration
    )
    longues = [g for g in gaps if g.kind == "pause_longue"]
    assert longues, "une pause de 3,5 s doit etre classee comme trop longue"
    gap = longues[0]
    assert gap.removed > 2.0
    assert gap.kept >= settings.silence.pad_in + settings.silence.pad_out


def test_une_pause_de_fin_de_phrase_est_mieux_preservee():
    """Apres un point, on garde plus de blanc qu'au milieu d'une phrase."""
    settings = Settings.for_style("dynamique")
    assert settings.silence.sentence_pause_target > settings.silence.target_gap


def test_la_respiration_audible_est_conservee():
    """Un blanc dont l'energie depasse le plancher de bruit est une respiration."""
    transcript = make_transcript(
        [("Premiere phrase ici.", 0.6), ("Deuxieme phrase la.", 0.45)]
    )
    sample_rate = 16000
    total = int(transcript.duration * sample_rate) + sample_rate
    rng = np.random.default_rng(3)
    samples = rng.normal(0, 0.0004, total).astype(np.float32)
    for word in transcript.words:
        start, end = int(word.start * sample_rate), int(word.end * sample_rate)
        samples[start:end] += rng.normal(0, 0.16, end - start).astype(np.float32)
    # respiration dans le blanc entre les deux phrases
    gap_start = transcript.segments[0].end
    gap_end = transcript.segments[1].start
    a, b = int(gap_start * sample_rate), int(gap_end * sample_rate)
    # une respiration se situe environ 30 dB sous la parole
    samples[a:b] += rng.normal(0, 0.004, b - a).astype(np.float32)
    profile = AudioProfile.analyze(samples, sample_rate)

    gaps = plan_silences(
        transcript, set(), Settings.for_style("dynamique").silence, profile,
        transcript.duration,
    )
    inter = [
        g
        for g in gaps
        if abs(g.start - gap_start) < 0.05 and abs(g.end - gap_end) < 0.05
    ]
    assert inter, "le blanc entre les deux phrases doit etre analyse"
    assert inter[0].kind == "respiration"
    assert inter[0].energy > 0.0


def test_un_blanc_contenant_de_la_parole_supprimee_est_ferme():
    """Le blanc qui englobe un mot retire est ramene aux seules marges."""
    transcript = make_transcript([("Je je pense que oui.", 0.6)])
    settings = Settings.for_style("dynamique")
    # on supprime le deuxieme « je », entoure de mots conserves
    gaps = plan_silences(
        transcript, {1}, settings.silence, None, transcript.duration
    )
    mauvaises = [g for g in gaps if g.kind == "mauvaise_prise"]
    assert mauvaises
    gap = mauvaises[0]
    assert gap.removal is not None
    assert gap.contains_removed_speech
    # on ne garde que les marges : pas plus que pad_in + pad_out
    assert gap.kept <= settings.silence.pad_in + settings.silence.pad_out + 1e-6


def test_le_silence_de_tete_et_de_queue_est_raccourci():
    transcript = make_transcript([("Bonjour tout le monde.", 3.0)])
    transcript.duration += 4.0
    settings = Settings.for_style("dynamique")
    gaps = plan_silences(
        transcript, set(), settings.silence, None, transcript.duration
    )
    bords = [g for g in gaps if g.kind == "bord"]
    assert len(bords) == 2
    for gap in bords:
        assert gap.removal is not None
        assert gap.kept == pytest.approx(settings.silence.edge_silence, abs=0.01)


def test_resume_des_blancs():
    transcript = make_transcript(LINES)
    settings = Settings.for_style("dynamique")
    gaps = plan_silences(
        transcript, set(), settings.silence, None, transcript.duration
    )
    resume = summarize(gaps)
    assert resume
    assert all("count" in bucket and "removed" in bucket for bucket in resume.values())
    assert removed_total(transcript, settings) > 0


# --------------------------------------------------------------------------- #
# Bords des mots conserves aux raccords de suppression
# --------------------------------------------------------------------------- #
def test_la_marge_est_prise_sur_la_parole_supprimee_faute_de_silence():
    """Une reprise enchainee n'offre aucun silence ou poser la marge.

    Le moteur de transcription rapporte les attaques de mot trop tard. Si la
    coupe tombe pile sur son horodatage, l'attaque du mot conserve est rognee.
    La marge doit donc etre prise sur la parole supprimee, qui part de toute
    facon.
    """
    from conftest import make_transcript

    from autorush.analysis.decisions import analyze
    from autorush.config import Settings

    transcript = make_transcript(
        [
            ("C'est que le Japon est devenu la region la plus forte.", 0.8),
            # la personne enchaine sans pause sur sa tentative ratee
            ("Au debut du Ultimate c'etait plutot les Etats-Unis qui roulaient"
             " sur tout le monde au debut c'etait vraiment les Etats-Unis qui"
             " roulaient completement sur tout le monde.", 0.35),
            ("Je pense notamment a Zachray qui etait clairement le meilleur.", 0.9),
        ]
    )
    settings = Settings.for_style("dynamique")
    result = analyze(transcript, settings, None, transcript.duration)

    coupes = [g for g in result.gaps if g.removal and g.contains_removed_speech]
    assert coupes, "la tentative ratee doit produire une coupe"
    tolerance = settings.silence.word_edge_tolerance
    for gap in coupes:
        cut_start, cut_end = gap.removal
        marge_avant = gap.end - cut_end
        marge_apres = cut_start - gap.start
        assert marge_avant >= tolerance - 1e-6, (
            f"attaque du mot conserve rognee : {marge_avant * 1000:.1f} ms"
        )
        assert marge_apres >= tolerance - 1e-6, (
            f"chute du mot conserve rognee : {marge_apres * 1000:.1f} ms"
        )


def test_la_marge_ne_devore_jamais_la_coupe():
    """Les marges ne doivent pas annuler la suppression elle-meme.

    Sinon les mots seraient marques supprimes mais leur audio resterait.
    """
    from conftest import make_transcript

    from autorush.analysis.decisions import analyze
    from autorush.config import Settings

    transcript = make_transcript(
        [
            ("Le niveau global a explose cette saison.", 0.7),
            ("Non, je recommence.", 0.12),
            ("Le niveau global a vraiment explose cette saison.", 0.12),
        ]
    )
    settings = Settings.for_style("dynamique")
    result = analyze(transcript, settings, None, transcript.duration)
    for gap in result.gaps:
        if not gap.removal:
            continue
        cut_start, cut_end = gap.removal
        assert cut_end > cut_start, "coupe inversee"
        assert cut_start >= gap.start - 1e-6
        assert cut_end <= gap.end + 1e-6
