"""Banc d'essai de la detection de reprises, sur du materiau reel.

Les enonces viennent d'un vrai rush facecam transcrit par faster-whisper
(3 min, sujet Smash Ultimate). Ce rush avait mis en evidence le defaut que
ces tests verrouillent : cinq reprises evidentes passaient inapercues parce
que la similarite etait mesuree sur le prefixe litteral et le vocabulaire
brut, deux mesures qu'une reformulation orale casse systematiquement.

La regle qui separe une reprise d'un simple voisinage thematique est la
**charpente commune** : la personne redemarre sur la meme attaque, ou aboutit
a la meme chute. Sans cette preuve, du vocabulaire partage ne prouve rien.
"""

from __future__ import annotations

from conftest import final_text, make_transcript

from autorush.analysis.decisions import analyze
from autorush.analysis.similarity import text_similarity
from autorush.config import Settings

# --------------------------------------------------------------------------- #
# Le rush : (texte, blanc avant, doit etre retire)
# --------------------------------------------------------------------------- #
RUSH: list[tuple[str, float, bool]] = [
    ("Aujourd'hui, quand on parle de Smash Ultimate, il y a un point sur lequel"
     " tout le monde est d'accord.", 0.5, False),
    ("C'est que le Japon est devenu la region la plus forte.", 1.4, False),
    ("Et pourtant, il faut savoir que ce n'est pas toujours ete le cas.", 1.8, True),
    ("Mais pourtant, en fait, ca n'a pas toujours ete le cas.", 2.1, False),
    ("Au debut du Ultimate, c'etait plutot les Etats-Unis qui roulaient sur"
     " tout le monde.", 1.6, True),
    ("Au debut, c'etait vraiment les Etats-Unis qui roulaient completement sur"
     " tout le monde.", 2.4, False),
    ("Ou plutot l'Amerique du Nord.", 1.3, True),
    ("Ou plus precisement l'Amerique du Nord, puisqu'en fait, on avait les"
     " Mexicains, dont MKLeo qui etait le meilleur joueur du monde, qui etaient"
     " les plus dominants.", 1.9, False),
    ("C'est simple, il a fallu plusieurs annees pour que quelqu'un lui prenne"
     " un tournoi.", 1.5, False),
    ("Malgre tout, on avait quand meme pas mal de joueurs au Japon qui se"
     " defendaient tres bien.", 1.7, True),
    ("Malgre tout, on avait quand meme quelques excellents joueurs au Japon.", 2.2, False),
    ("Je pense notamment a Zachray, qui etait clairement l'un des meilleurs"
     " joueurs du monde des le debut.", 1.4, False),
    ("Mais globalement, la scene semblait un petit peu un cran en dessous.", 1.6, False),
    ("Au debut, ils cherchaient un petit peu leur facon de jouer, leur propre"
     " meta.", 1.5, False),
    ("Ils jouaient des personnages un peu bizarres, mais c'etait pas si efficace"
     " que ca en fait.", 1.3, False),
    ("Et quand ils essayaient de jouer des persos qui etaient un peu plus"
     " simples, un peu petit peu...", 1.8, True),
    ("Et lorsqu'ils essayaient de jouer des personnages un petit peu plus"
     " simples, un petit peu plus bases sur les fondamentaux du jeu, ca passait"
     " pas trop.", 2.3, False),
]

#: reprises reelles : (tentative ratee, version conservee)
REPRISES: list[tuple[str, str]] = [
    ("Et pourtant, il faut savoir que ce n'est pas toujours ete le cas.",
     "Mais pourtant, en fait, ca n'a pas toujours ete le cas."),
    ("Au debut du Ultimate, c'etait plutot les Etats-Unis qui roulaient sur"
     " tout le monde.",
     "Au debut, c'etait vraiment les Etats-Unis qui roulaient completement sur"
     " tout le monde."),
    ("Ou plutot l'Amerique du Nord.",
     "Ou plus precisement l'Amerique du Nord, puisqu'en fait, on avait les"
     " Mexicains, dont MKLeo qui etait le meilleur joueur du monde."),
    ("Malgre tout, on avait quand meme pas mal de joueurs au Japon qui se"
     " defendaient tres bien.",
     "Malgre tout, on avait quand meme quelques excellents joueurs au Japon."),
    ("Et quand ils essayaient de jouer des persos qui etaient un peu plus"
     " simples, un peu petit peu",
     "Et lorsqu'ils essayaient de jouer des personnages un petit peu plus"
     " simples, un petit peu plus bases sur les fondamentaux du jeu."),
]

#: paires trompeuses : meme sujet, vocabulaire partage, sens different
VOISINAGES: list[tuple[str, str]] = [
    ("Mais globalement, la scene semblait un petit peu un cran en dessous.",
     "Au debut, ils cherchaient un petit peu leur facon de jouer, leur propre meta."),
    ("Au debut, ils cherchaient un petit peu leur facon de jouer, leur propre meta.",
     "Ils jouaient des personnages un peu bizarres, mais c'etait pas si efficace"
     " que ca en fait."),
    ("Ils jouaient des personnages un peu bizarres, mais c'etait pas si efficace"
     " que ca en fait.",
     "Et quand ils essayaient de jouer des persos qui etaient un peu plus simples."),
    ("Le niveau global a explose cette saison.",
     "Le niveau des equipes europeennes progresse aussi."),
    ("Les joueurs japonais ont domine la competition.",
     "Les joueurs coreens ont fini juste derriere."),
    ("Le tournoi se deroulait a Tokyo au mois de juillet.",
     "Le tournoi suivant aura lieu a Osaka en septembre."),
    ("Voici le classement complet de cette saison.",
     "Le classement a beaucoup bouge en fin de saison."),
    ("Malgre tout, on avait quand meme quelques excellents joueurs au Japon.",
     "Je pense notamment a Zachray, qui etait clairement l'un des meilleurs"
     " joueurs du monde des le debut."),
]


def _utterance_spans(result):
    """Indices des mots de chaque enonce du rush, dans l'ordre."""
    words = result.transcript.words
    spans = []
    cursor = 0
    for line, _, _ in RUSH:
        count = len(line.replace("...", " ").split())
        spans.append([w.index for w in words[cursor : cursor + count]])
        cursor += count
    return spans


def _run(style="dynamique"):
    transcript = make_transcript([(text, gap) for text, gap, _ in RUSH])
    settings = Settings.for_style(style)
    return analyze(transcript, settings, None, transcript.duration)


# --------------------------------------------------------------------------- #
# La preuve de redemarrage : ce qui separe une reprise d'un voisinage
# --------------------------------------------------------------------------- #
def test_toute_reprise_montre_un_redemarrage():
    """Attaque commune ou chute commune : une reprise laisse toujours une trace."""
    for attempt, kept in REPRISES:
        result = text_similarity(attempt, kept)
        assert result.restart_prefix or result.restart_structure, (
            f"aucune preuve de redemarrage pour {attempt[:40]!r}"
        )


def test_aucun_voisinage_thematique_ne_montre_de_redemarrage():
    """Deux phrases differentes du meme rush ne doivent jamais faire illusion."""
    for first, second in VOISINAGES:
        result = text_similarity(first, second)
        assert not (result.restart_prefix or result.restart_structure), (
            f"faux redemarrage entre {first[:40]!r} et {second[:40]!r}"
        )


def test_les_reprises_scorent_au_dessus_des_voisinages():
    """La mesure doit separer les deux populations, pas seulement les classer."""
    reprises = [text_similarity(a, b).score for a, b in REPRISES]
    voisinages = [text_similarity(a, b).score for a, b in VOISINAGES]
    # la reprise la plus faible reste au-dessus du voisinage le plus trompeur
    assert min(reprises) > max(voisinages), (
        f"populations melangees : reprise min {min(reprises):.3f}"
        f" <= voisinage max {max(voisinages):.3f}"
    )


def test_le_seuil_par_defaut_tombe_entre_les_deux_populations():
    seuil = Settings.for_style("dynamique").retake.similarity_without_marker
    voisinages = [text_similarity(a, b).score for a, b in VOISINAGES]
    assert max(voisinages) < seuil, "un voisinage thematique franchit le seuil"


# --------------------------------------------------------------------------- #
# Bout en bout sur le rush
# --------------------------------------------------------------------------- #
def test_aucune_phrase_valable_nest_supprimee():
    """La regle non negociable, verifiee sur les 12 phrases valables du rush."""
    result = _run()
    removed = result.removed_word_indices
    detruites = []
    for (line, _, expected), span in zip(RUSH, _utterance_spans(result), strict=True):
        if expected or not span:
            continue
        disparu = sum(1 for index in span if index in removed) / len(span)
        if disparu > 0.5:
            detruites.append(line[:50])
    assert not detruites, f"phrases valables supprimees : {detruites}"


def test_la_majorite_des_reprises_est_retiree():
    result = _run()
    removed = result.removed_word_indices
    trouvees = 0
    attendues = 0
    for (_, _, expected), span in zip(RUSH, _utterance_spans(result), strict=True):
        if not expected or not span:
            continue
        attendues += 1
        disparu = sum(1 for index in span if index in removed) / len(span)
        trouvees += disparu > 0.5
    assert attendues == 5
    assert trouvees >= 4, f"seulement {trouvees}/5 reprises retirees"


def test_toute_reprise_est_au_moins_signalee():
    """Une reprise non supprimee doit apparaitre dans le rapport, jamais disparaitre
    du radar : c'est ce qui manquait avant."""
    result = _run()
    assert len(result.retake_groups) >= 5, (
        f"seulement {len(result.retake_groups)} groupes reperes sur 5 reprises"
    )


def test_le_texte_final_ne_bafouille_plus():
    """Les formulations doublees ont disparu du montage."""
    text = final_text(result := _run()).lower()
    assert "au debut du ultimate" not in text
    assert "ou plutot l'amerique" not in text
    assert "des persos" not in text
    # la bonne version de chaque paire est bien la
    assert "au debut, c'etait vraiment les etats-unis" in text
    assert "ou plus precisement l'amerique du nord" in text
    assert "des personnages un petit peu plus simples" in text
    assert result.retake_groups


def test_les_trois_styles_preservent_les_phrases_valables():
    for style in ("naturel", "dynamique", "tres_dynamique"):
        result = _run(style)
        removed = result.removed_word_indices
        for (line, _, expected), span in zip(
            RUSH, _utterance_spans(result), strict=True
        ):
            if expected or not span:
                continue
            disparu = sum(1 for index in span if index in removed) / len(span)
            assert disparu <= 0.5, f"[{style}] phrase valable supprimee : {line[:50]}"


# --------------------------------------------------------------------------- #
# Les mesures elles-memes
# --------------------------------------------------------------------------- #
def test_appariement_tolere_la_flexion():
    """La transcription ne redit jamais deux fois la meme forme flechie."""
    from autorush.analysis.similarity import token_match

    assert token_match("roulait", "roulaient") > 0.0
    assert token_match("joueur", "joueurs") > 0.0
    # troncature familiere : "persos" pour "personnages"
    assert token_match("personnages", "persos") > 0.0


def test_appariement_napparie_pas_nimporte_quoi():
    """Un radical trop court rapprocherait des mots sans rapport."""
    from autorush.analysis.similarity import token_match

    assert token_match("cas", "ca") == 0.0
    assert token_match("tournoi", "niveau") == 0.0
    assert token_match("japon", "juillet") == 0.0
    # "jou" ne fait que trois lettres : trop court pour apparier a l'aveugle
    assert token_match("jouer", "jouaient") == 0.0


def test_une_chute_commune_suffit_a_prouver_le_redemarrage():
    """Le debut peut changer entierement, la fin trahit la reprise."""
    result = text_similarity(
        "Et pourtant, il faut savoir que ce n'est pas toujours ete le cas.",
        "Mais pourtant, en fait, ca n'a pas toujours ete le cas.",
    )
    assert result.tail > result.opening
    assert result.restart_structure


def test_la_perte_dinformation_est_mesuree_en_part_et_non_en_nombre():
    """Abreger n'est pas perdre : ``du Ultimate`` disparait sans dommage."""
    abrege = text_similarity(
        "Au debut du Ultimate, c'etait plutot les Etats-Unis qui roulaient.",
        "Au debut, c'etait vraiment les Etats-Unis qui roulaient.",
    )
    tronque = text_similarity(
        "On a eu Acola, Miya, Asimo, Hurt, Zackray, Yoshidora et Raru.",
        "On a eu Acola.",
    )
    assert abrege.loss_ratio < 0.5, "une reprise qui abrege ne perd pas l'essentiel"
    assert tronque.loss_ratio > 0.5, "une troncature perd bien l'essentiel"


def test_un_fragment_sans_mot_de_contenu_ne_contient_rien():
    """``et euh le`` n'est pas une tentative de phrase : c'est du bafouillage."""
    result = text_similarity("et euh le", "et euh le donc")
    assert result.containment == 0.0
    assert not result.shared_content


def test_la_version_allongee_reste_pleinement_contenue():
    result = text_similarity(
        "On a eu Acola, Miya, Asimo",
        "On a eu Acola, Miya, Asimo, Hurt, Zackray, Yoshidora et Raru.",
    )
    assert result.containment == 1.0
    assert result.loss_ratio == 0.0
    assert not result.lost_content


# --------------------------------------------------------------------------- #
# Reprises a l'interieur d'un seul segment de transcription
# --------------------------------------------------------------------------- #
# faster-whisper livre des segments de plusieurs secondes. Une tentative ratee
# et sa reprise y atterrissent regulierement ENSEMBLE, sans ponctuation entre
# les deux. La detection compare les enonces entre eux : sans decoupage
# prealable sur le redemarrage, elle ne voit rien du tout.

#: (segment tel que Whisper le livre, fragment qui doit disparaitre)
SEGMENTS_FUSIONNES: list[tuple[str, str]] = [
    ("Et pourtant il faut savoir que ce n'est pas toujours ete le cas mais"
     " pourtant en fait ca n'a pas toujours ete le cas.",
     "il faut savoir"),
    ("Au debut du Ultimate c'etait plutot les Etats-Unis qui roulaient sur tout"
     " le monde au debut c'etait vraiment les Etats-Unis qui roulaient"
     " completement sur tout le monde.",
     "du ultimate"),
    ("Ou plutot l'Amerique du Nord ou plus precisement l'Amerique du Nord"
     " puisqu'en fait on avait les Mexicains dont MKLeo.",
     "ou plutot"),
    ("Malgre tout on avait quand meme pas mal de joueurs au Japon qui se"
     " defendaient tres bien malgre tout on avait quand meme quelques"
     " excellents joueurs au Japon.",
     "se defendaient"),
    ("On a eu Acola Miya Asimo on a eu Acola Miya Asimo Hurt Zackray Yoshidora"
     " et Raru.",
     "asimo on a eu"),
]

#: phrases normales, longues, qui ne doivent JAMAIS etre scindees
SEGMENTS_NORMAUX: list[str] = [
    "Le niveau global a explose cette saison et les joueurs europeens"
    " progressent aussi beaucoup depuis le debut.",
    "On a eu Acola Miya Asimo Hurt Zackray Yoshidora et Raru cette annee au"
    " Japon.",
    "C'etait vraiment vraiment tres tres fort le niveau de jeu de ces joueurs"
    " japonais.",
    "Le tournoi de Tokyo etait complet et le tournoi suivant aura lieu a Osaka"
    " en septembre prochain.",
    "Et pourtant les resultats ne suivent pas et pourtant tout le monde y"
    " croyait encore hier soir.",
    "Je pense que le niveau a beaucoup progresse mais je pense aussi que la"
    " concurrence s'est renforcee.",
    "Au debut de la saison il y avait douze equipes et a la fin il n'en restait"
    " plus que quatre.",
    "Voici le classement complet de cette saison et voila les resultats"
    " detailles par region du monde.",
]


def test_un_segment_qui_contient_sa_reprise_est_scinde():
    """Sans ce decoupage, la reprise reste invisible : rien n'est compare."""
    from autorush.analysis.utterances import build_utterances

    for segment, _ in SEGMENTS_FUSIONNES:
        transcript = make_transcript([(segment, 1.0)])
        utterances = build_utterances(transcript)
        assert len(utterances) > 1, f"segment non scinde : {segment[:50]!r}"


def test_la_reprise_interne_est_bien_nettoyee():
    for segment, bafouillage in SEGMENTS_FUSIONNES:
        transcript = make_transcript([(segment, 1.0)])
        result = analyze(
            transcript, Settings.for_style("dynamique"), None, transcript.duration
        )
        texte = final_text(result).lower()
        assert bafouillage not in texte, (
            f"{bafouillage!r} devrait disparaitre de {texte[:70]!r}"
        )
        assert texte.strip(), "le segment ne doit pas etre vide apres nettoyage"


def test_une_phrase_normale_nest_jamais_scindee():
    """Le decoupage sur redemarrage ne doit pas hacher les phrases valables."""
    from autorush.analysis.utterances import build_utterances

    for phrase in SEGMENTS_NORMAUX:
        transcript = make_transcript([(phrase, 1.0)])
        utterances = build_utterances(transcript)
        assert len(utterances) == 1, f"phrase scindee a tort : {phrase[:50]!r}"


def test_une_phrase_normale_ne_perd_aucun_mot():
    for phrase in SEGMENTS_NORMAUX:
        transcript = make_transcript([(phrase, 1.0)])
        result = analyze(
            transcript, Settings.for_style("dynamique"), None, transcript.duration
        )
        assert not result.removed_word_indices, f"mots retires de {phrase[:50]!r}"


def test_bout_en_bout_sur_segmentation_reelle():
    """Le rush entier, segmente comme faster-whisper le livre vraiment."""
    from autorush.analysis.utterances import build_utterances

    segments = [
        ("Aujourd'hui quand on parle de Smash Ultimate il y a un point sur"
         " lequel tout le monde est d'accord.", 0.5),
        ("C'est que le Japon est devenu la region la plus forte.", 1.4),
        (SEGMENTS_FUSIONNES[0][0], 1.8),
        (SEGMENTS_FUSIONNES[1][0], 1.6),
        (SEGMENTS_FUSIONNES[2][0], 1.3),
        ("C'est simple il a fallu plusieurs annees pour que quelqu'un lui"
         " prenne un tournoi.", 1.5),
        (SEGMENTS_FUSIONNES[3][0], 1.7),
        ("Je pense notamment a Zachray qui etait clairement l'un des meilleurs"
         " joueurs du monde des le debut.", 1.4),
        ("Mais globalement la scene semblait un petit peu un cran en dessous.", 1.6),
        ("Ils jouaient des personnages un peu bizarres mais c'etait pas si"
         " efficace que ca en fait.", 1.3),
    ]
    transcript = make_transcript(segments)
    result = analyze(
        transcript, Settings.for_style("dynamique"), None, transcript.duration
    )
    texte = final_text(result).lower()

    # les quatre bafouillages fusionnes ont disparu
    for _, bafouillage in SEGMENTS_FUSIONNES[:4]:
        assert bafouillage not in texte, f"{bafouillage!r} subsiste"

    # et tout le contenu utile est la
    for garde in (
        "smash ultimate",
        "region la plus forte",
        "n'a pas toujours ete le cas",
        "roulaient completement",
        "les mexicains",
        "plusieurs annees",
        "quelques excellents joueurs",
        "zachray",
        "un cran en dessous",
        "personnages un peu bizarres",
    ):
        assert garde in texte, f"{garde!r} perdu"

    assert len(build_utterances(transcript)) > len(segments), (
        "les segments fusionnes doivent avoir ete scindes"
    )


# --------------------------------------------------------------------------- #
# Normalisation orale et combinaison des mesures
# --------------------------------------------------------------------------- #
def test_les_tics_sont_retires_avant_comparaison():
    """Une reprise ajoute et retire ses tics librement : ils ne comptent pas."""
    from autorush.analysis.similarity import normalize_oral
    from autorush.utils import tokenize

    nettoye = normalize_oral(tokenize("et du coup euh en fait le niveau a monte"))
    assert "euh" not in nettoye
    for tic in ("coup", "fait"):
        assert tic not in nettoye, f"{tic!r} devrait avoir disparu"
    assert "niveau" in nettoye and "monte" in nettoye


def test_les_tournures_equivalentes_sont_ramenees_a_une_forme():
    """``pas mal de`` et ``quelques`` disent la meme chose a l'oral."""
    from autorush.analysis.similarity import normalize_oral
    from autorush.utils import tokenize

    a = normalize_oral(tokenize("on avait pas mal de joueurs"))
    b = normalize_oral(tokenize("on avait quelques joueurs"))
    assert a == b


def test_la_reformulation_avec_tournure_equivalente_est_reconnue():
    resultat = text_similarity(
        "Malgre tout, on avait quand meme pas mal de joueurs au Japon.",
        "Malgre tout, on avait quand meme quelques excellents joueurs au Japon.",
    )
    assert resultat.restart_prefix or resultat.restart_structure
    assert resultat.score >= 0.70


def test_une_seule_mesure_elevee_suffit():
    """La combinaison retient la meilleure mesure, elle ne fait pas la moyenne.

    Une reprise qui garde sa phrase en changeant son attaque a une charpente
    forte et un alignement moyen. Une moyenne la ferait passer sous le seuil.
    """
    resultat = text_similarity(
        "Et pourtant, il faut savoir que ce n'est pas toujours ete le cas.",
        "Mais pourtant, en fait, ca n'a pas toujours ete le cas.",
    )
    assert resultat.structure >= 0.60, "la charpente doit etre l'ancre ici"
    assert resultat.align < resultat.structure, "l'alignement est la mesure faible"
    assert resultat.score >= 0.90, "une ancre forte doit promouvoir le score"


def test_le_recouvrement_seul_ne_promeut_jamais():
    """Deux phrases du meme sujet se recouvrent sans etre une reprise.

    Sans ancre structurelle, un recouvrement de contenu ne doit pas suffire.
    """
    resultat = text_similarity(
        "Voici le classement complet de cette saison.",
        "Le classement a beaucoup bouge en fin de saison.",
    )
    assert not (resultat.restart_prefix or resultat.restart_structure)
    seuil = Settings.for_style("dynamique").retake.similarity_without_marker
    assert resultat.score < seuil


def test_une_phrase_de_contenu_nest_jamais_remplacee_par_un_connecteur():
    """``Et.`` ne remplace pas une phrase : elle ne dit rien."""
    from autorush.analysis.retakes import _replacement_too_thin
    from autorush.analysis.utterances import build_utterances

    transcript = make_transcript(
        [
            ("Le niveau global a vraiment explose cette saison au Japon.", 0.7),
            ("Et.", 0.9),
        ]
    )
    utterances = build_utterances(transcript)
    assert _replacement_too_thin(utterances[0], utterances[-1])
    # et la phrase survit au traitement complet
    result = analyze(
        transcript, Settings.for_style("dynamique"), None, transcript.duration
    )
    assert "explose" in final_text(result).lower()


def test_une_amorce_interrompue_est_promue_sans_ressembler_beaucoup():
    """Une amorce coupee est trop courte pour ressembler a la version longue.

    La preuve vient du contexte : elle s'arrete en plan.
    """
    complet = text_similarity("On a eu Acola Miya", "On a eu Acola Miya Asimo Hurt Raru.")
    interrompu = text_similarity(
        "On a eu Acola Miya", "On a eu Acola Miya Asimo Hurt Raru.", a_interrupted=True
    )
    assert interrompu.score >= complet.score


# --------------------------------------------------------------------------- #
# Chaines de tentatives : l'ouverture dite quatre fois
# --------------------------------------------------------------------------- #
# Rien ne garantit que la derniere tentative soit la bonne. Une personne qui
# bute sur son ouverture peut s'ameliorer (chaine montante), s'essouffler
# (chaine descendante) ou s'arreter en plan. Dans les quatre cas la version
# complete doit survivre, et elle seule.

_OUVERTURE = "Aujourd'hui quand on parle de Smash Ultimate il y a un point"
_COMPLETE = f"{_OUVERTURE} sur lequel tout le monde est d'accord."
_TENTATIVES = [
    f"{_OUVERTURE}.",
    f"{_OUVERTURE} sur lequel.",
    f"{_OUVERTURE} sur lequel tout le monde.",
]

CHAINES: dict[str, list[str]] = {
    "montante": [*_TENTATIVES, _COMPLETE],
    "descendante": [_COMPLETE, *reversed(_TENTATIVES)],
    "identiques": [_COMPLETE] * 4,
    "derniere_tronquee": [_COMPLETE, _TENTATIVES[0]],
    "derniere_abandonnee": [_COMPLETE, f"{_OUVERTURE} sur..."],
}


def _monter_chaine(textes: list[str]):
    lignes = [(texte, 0.6) for texte in textes]
    transcript = make_transcript(lignes)
    return analyze(
        transcript, Settings.for_style("dynamique"), None, transcript.duration
    )


def test_une_chaine_ne_laisse_quune_seule_ouverture():
    for nom, textes in CHAINES.items():
        texte = final_text(_monter_chaine(textes)).lower()
        assert texte.count("aujourd") == 1, (
            f"chaine {nom} : {texte.count('aujourd')} ouvertures au lieu d'une"
        )


def test_une_chaine_conserve_toujours_la_version_complete():
    """Le sens ne doit jamais se perdre, quel que soit l'ordre des tentatives."""
    for nom, textes in CHAINES.items():
        texte = final_text(_monter_chaine(textes)).lower()
        assert "tout le monde est d'accord" in texte, (
            f"chaine {nom} : la version complete a disparu -> {texte[:70]!r}"
        )


def test_une_tentative_abandonnee_ne_remplace_jamais_une_phrase_finie():
    """``il y a un...`` ne peut pas evincer la phrase entiere."""
    resultat = _monter_chaine(CHAINES["derniere_abandonnee"])
    texte = final_text(resultat).lower()
    assert "est d'accord" in texte
    assert "il y a un..." not in texte


def test_une_enumeration_legitime_survit_a_la_passe_descendante():
    """Deux elements d'une liste se ressemblent sans etre une reprise."""
    resultat = _monter_chaine(
        [
            "On a teste les nouveaux persos cette semaine.",
            "On a teste les nouvelles mecaniques aussi.",
        ]
    )
    assert not resultat.removed_word_indices
