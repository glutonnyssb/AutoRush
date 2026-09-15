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
