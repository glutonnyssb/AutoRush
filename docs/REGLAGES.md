# Réglages

Tous les seuils sont regroupés dans `autorush/config.py` et sérialisables.

## Produire un fichier de réglages

```powershell
py -m autorush --save-settings mes_reglages.json --style dynamique
```

Éditez le fichier, puis :

```powershell
py -m autorush rush.mp4 --settings mes_reglages.json
```

Les options de la ligne de commande ont toujours le dernier mot sur le fichier.

## Silences (`silence`)

| Champ | Défaut (dynamique) | Effet |
| --- | --- | --- |
| `keep_below` | 0,30 | en dessous, le blanc n'est jamais touché |
| `target_gap` | 0,22 | durée visée pour un blanc d'hésitation |
| `long_pause_threshold` | 0,95 | au-delà, le blanc est une pause trop longue |
| `long_pause_target` | 0,30 | ce qu'on garde d'une pause trop longue |
| `sentence_pause_target` | 0,34 | ce qu'on garde après un point |
| `comma_pause_target` | 0,20 | ce qu'on garde après une virgule |
| `pad_in` / `pad_out` | 0,070 / 0,120 | marges avant et après chaque plan |
| `edge_silence` | 0,25 | silence conservé en tête et en queue de rush |
| `min_shot_duration` | 0,42 | aucun plan plus court que cela |
| `min_removal` | 0,09 | on ne coupe pas pour gagner moins que cela |
| `breath_energy_ratio` | 0,055 | énergie minimale d'une respiration |
| `breath_max_energy` | 0,50 | énergie maximale d'une respiration |
| `breath_max_duration` | 0,55 | durée maximale d'une respiration |
| `breath_keep` | 0,32 | ce qu'on garde d'une respiration audible |

Pour un montage plus aéré : montez `keep_below` et `target_gap`. Pour un montage
plus serré : baissez-les, mais jamais en dessous de `pad_in + pad_out`, sinon
les marges de sécurité ne tiennent plus.

## Hésitations (`disfluency`)

| Champ | Défaut | Effet |
| --- | --- | --- |
| `remove_fillers` | vrai | retire « euh », « uh », « hmm » |
| `remove_soft_fillers` | faux | retire aussi « du coup », « genre », « like » |
| `remove_stutters` | vrai | retire les répétitions immédiates |
| `remove_abandoned_words` | vrai | retire les débuts de mots abandonnés |
| `filler_max_duration` | 1,20 | un « euh » plus long est gardé (probable erreur de transcription) |
| `abandoned_max_duration` | 0,85 | durée maximale d'une amorce supprimable |
| `max_consecutive_removed` | 4 | jamais plus de N mots d'affilée |
| `protect_intensifiers` | vrai | protège « très très », « very very » |
| `keep_last_repetition` | vrai | garde la dernière occurrence, mieux articulée |
| `repetition_max_gap` | 0,90 | au-delà, ce n'est plus un bafouillage |
| `min_delete_confidence` | 0,60 | en dessous : conservé et signalé |

## Reprises (`retake`)

| Champ | Défaut | Effet |
| --- | --- | --- |
| `similarity_with_marker` | 0,52 | similarité exigée quand un marqueur est présent |
| `similarity_without_marker` | 0,78 | similarité exigée sans marqueur |
| `search_window` | 26,0 | fenêtre de recherche, en secondes |
| `max_utterance_distance` | 5 | nombre d'énoncés séparant deux tentatives |
| `min_shared_content_words` | 2 | mots de contenu communs minimum |
| `long_attempt_duration` | 7,5 | au-delà, un marqueur explicite est exigé |
| `max_information_loss` | 1 | mots de contenu uniques qu'on accepte de perdre |
| `complete_utterance_tokens` | 7 | au-delà, l'énoncé est traité comme une phrase entière |
| `min_delete_confidence` | 0,60 | en dessous : conservé et signalé |
| `max_removed_speech_ratio` | 0,55 | part maximale de parole supprimable |
| `cap_min_speech_duration` | 60,0 | le filet global ne s'applique qu'au-delà |
| `cap_exempt_confidence` | 0,75 | au-dessus, une suppression n'est jamais annulée |

**Pour être plus prudent** : montez `min_delete_confidence` à 0,75. Beaucoup de
passages basculeront en signalements, à vérifier dans le rapport.

**Pour être plus agressif** : baissez-le à 0,50. Relisez alors la section
« Décisions incertaines » du rapport.

## Fragments (`fragment`)

| Champ | Défaut | Effet |
| --- | --- | --- |
| `max_duration` | 2,6 | durée maximale d'un fragment supprimable |
| `max_tokens` | 6 | nombre de mots maximum |
| `require_context` | vrai | un fragment isolé sans indice autour est gardé |
| `min_delete_confidence` | 0,62 | en dessous : conservé et signalé |

## Zooms (`zoom`)

| Champ | Défaut | Effet |
| --- | --- | --- |
| `intensity` | 55 | curseur unique : pilote amplitude, écart et quota |
| `scale_min_delta` / `scale_max_delta` | 4 / 20 | amplitude en % à intensité 0 et 100 |
| `scale_floor` / `scale_ceiling` | 100 / 145 | bornes dures, jamais de zoom arrière |
| `launched_duration` | 1,05 | durée d'un zoom lancé |
| `launched_overshoot` | 0,11 | dépassement, en fraction de l'amplitude |
| `launched_stiffness` | 5,6 | nervosité du départ |
| `progressive_min_shot` | 2,6 | durée minimale d'un plan pour un zoom progressif |
| `launched_min_shot` | 1,5 | idem pour un zoom lancé |
| `direct_min_shot` | 0,8 | idem pour un zoom direct |
| `min_spacing` | 6,5 | écart minimal entre deux zooms |
| `max_static_duration` | 22,0 | au-delà, un zoom est forcé |
| `max_per_minute` | 5,5 | quota |
| `avoid_repeating_type` | vrai | jamais deux fois le même type d'affilée |
| `avoid_after_disfluency` | vrai | évite les plans qui suivent une suppression |
| `focus_x` / `focus_y` | 0,5 / 0,5 | point de recadrage (0,45 recadre légèrement vers le haut) |
| `weight_direct` / `weight_progressive` / `weight_launched` | 1,0 / 1,0 / 1,1 | proportion des types |
| `max_keyframes` | 90 | budget de keyframes par paramètre animé |
| `keyframe_tolerance` | 0,22 | finesse d'échantillonnage, en points d'échelle |

Le point de recadrage mérite un mot : sur un cadrage facecam où le visage est
au-dessus du centre, `focus_y = 0.45` garde le visage en place pendant le zoom.
AutoRush écrit alors des keyframes de position en plus de celles d'échelle.

## Transcription (`transcription`)

| Champ | Défaut | Effet |
| --- | --- | --- |
| `model` | `large-v3` | plus petit = plus rapide, moins précis |
| `language` | `auto` | `fr`, `en`, `auto`, ou `multi` pour une vidéo bilingue |
| `device` | `auto` | `cpu` ou `cuda` |
| `compute_type` | `auto` | `int8`, `float16`… |
| `beam_size` | 5 | qualité de décodage |
| `vad_filter` | vrai | ignore les zones sans parole |
| `condition_on_previous_text` | faux | évite que le modèle « invente » la suite |
| `word_timestamps` | vrai | **indispensable** : sans cela, pas de coupe précise |
| `multilang_chunk` | 30,0 | taille des blocs en mode `multi` |
| `use_cache` | vrai | réutilise une transcription déjà calculée |
| `initial_prompt` | vide | utile pour les noms propres récurrents |

`initial_prompt` vaut le détour : y mettre les noms qui reviennent dans vos
vidéos (pseudos, marques, jargon) améliore nettement la transcription.

## Export (`export`)

| Champ | Défaut | Effet |
| --- | --- | --- |
| `audio_crossfade` | vrai | fondu enchaîné aux coupes |
| `audio_crossfade_frames` | 3 | longueur du fondu, en images |
| `audio_level_fallback` | vrai | micro-fondu quand un fondu enchaîné est impossible |
| `keyframe_time_base` | `source` | voir docs/PREMIERE.md |
| `sequence_name` | `{name} - AutoRush` | nom de la séquence dans Premiere |
| `preview_height` | 720 | hauteur de la preview |
| `preview_crf` | 20 | qualité de la preview, plus bas = mieux |
| `preview_preset` | `veryfast` | vitesse d'encodage |
| `fps` / `width` / `height` | 0 | 0 = repris du rush |

## Modes globaux

| Champ | Effet |
| --- | --- |
| `silences_only` | ne touche qu'aux silences, aucune parole supprimée |
| `dry_run_decisions` | ne supprime rien : analyse et signale seulement |
| `seed` | graine de la variété des zooms ; même graine, même résultat |

`dry_run_decisions` (`--keep-all` en ligne de commande) est la bonne façon
d'auditer un rush : le rapport liste tout ce qu'AutoRush **aurait** retiré, sans
rien toucher.
