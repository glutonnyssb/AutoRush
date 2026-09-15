# Comment AutoRush décide

## Le pipeline

```
import ──▶ transcription ──▶ analyse ──▶ reprises ──▶ montage ──▶ zooms ──▶ export
```

| Étape | Module | Rôle |
| --- | --- | --- |
| import | `media/ffmpeg.py` | lecture des caractéristiques, extraction de l'audio en WAV 16 kHz |
| transcription | `transcription/whisper_backend.py` | mots horodatés, via faster-whisper |
| analyse | `media/audio.py`, `analysis/disfluency.py` | enveloppe d'énergie, hésitations |
| reprises | `analysis/retakes.py`, `analysis/fragments.py` | tentatives multiples, fragments |
| montage | `analysis/silences.py`, `editing/timeline.py` | blancs, plans, raccords |
| zooms | `zoom/planner.py`, `zoom/curves.py` | placement et courbes |
| export | `export/*` | XML Premiere, EDL, JSON, rapport, preview |

Tout passe par un objet `Settings` unique (`config.py`), sérialisable en JSON.

## L'unité de décision : le mot horodaté

Toutes les décisions se ramènent à des intervalles de mots. Les segments rendus
par le moteur de transcription ne servent qu'à porter la ponctuation et la
langue détectée.

Les mots sont regroupés en **énoncés** (`analysis/utterances.py`), c'est-à-dire
en tentatives de phrase. Un énoncé est délimité par une ponctuation forte, des
points de suspension, un blanc assez long, ou un marqueur de correction isolé.

Chaque énoncé sait dire s'il est :

- **complet** — il se termine proprement, pas sur un mot de liaison, avec assez
  de matière ;
- **abandonné** — il finit en suspens, sur un mot tronqué, ou sur un mot qui ne
  peut pas terminer une phrase (« et », « que », « plutôt »…).

C'est cette distinction qui protège les bonnes phrases : une phrase complète ne
peut pas être supprimée sans preuve forte.

## La confiance, et pourquoi rien n'est supprimé sans elle

Chaque décision porte une confiance dans `[0, 1]`. En dessous du seuil du style,
la décision **n'est pas appliquée** : le passage reste dans le montage et
apparaît dans le rapport, section « Décisions incertaines ».

C'est le mécanisme central de la promesse « en cas de doute, on garde ».

## Détection des reprises

1. On cherche une **bonne version** `B` : un énoncé complet.
2. On remonte en arrière depuis `B` tant que les énoncés ressemblent à des
   tentatives ratées. On s'arrête dès qu'on rencontre une phrase **complète et
   dissemblable** : c'est la frontière du groupe.
3. Le groupe n'est retenu que s'il possède un **ancrage** : un marqueur explicite
   (« je recommence ») ou une tentative vraiment similaire à `B`. Sans ancrage,
   rien n'est supprimé.
4. Chaque tentative reçoit une confiance.

### La mesure de similarité

Quatre mesures lexicales, sans modèle externe (`analysis/similarity.py`) :

| Mesure | Ce qu'elle capture | Poids |
| --- | --- | --- |
| `sequence` | reformulation partielle, ordre conservé | 0,34 |
| `prefix` | redémarrage littéral de la même phrase | 0,24 |
| `jaccard` | vocabulaire commun, sans l'ordre | 0,16 |
| `containment` | la nouvelle version couvre-t-elle l'ancienne ? | 0,26 |

`containment` est la mesure décisive : une reprise réussie contient en général
l'information de la tentative ratée, plus la suite.

Exemple, sur les phrases du cahier des charges :

| Tentative | Version conservée | Score |
| --- | --- | --- |
| « Les joueurs japonais étaient plutôt… » | « Les joueurs japonais étaient vraiment excellents cette année. » | 0,63 |
| « On a eu Acola, Miya, Asimo… » | « On a eu Acola, Miya, Asimo, Hurt, Zackray, Yoshidora et Raru. » | 0,81 |
| « Le niveau global a explosé cette saison. » | « Merci d'avoir regardé. » | 0,03 |

### Les garde-fous

- **Phrase complète sans marqueur** — pénalité de confiance, forte.
- **Perte d'information** — si supprimer une tentative ferait disparaître des
  mots de contenu absents de la version conservée, la confiance chute. Exception :
  quand un redémarrage littéral est prouvé, la fin de la phrase est justement ce
  que la personne corrige, elle n'est donc pas comptée comme une perte.
- **Marqueur isolé** — un « Non. » entre deux bonnes phrases n'est jamais
  supprimé : il n'y a pas de tentative ratée autour. Il est signalé comme raccord
  suspect.
- **Filet global** — si l'analyse veut retirer plus qu'une part donnée de la
  parole du rush, les décisions les moins sûres sont annulées. Ce filet ne
  s'applique qu'au-delà d'une minute de parole, et il n'annule jamais une
  consigne explicite : « je recommence » est toujours respecté.

## Traitement des blancs

Un blanc est l'intervalle entre deux mots **conservés**. Il peut donc contenir
de la parole supprimée : cette seule liste d'intervalles décrit tout le montage.

Chaque blanc est classé puis ramené à une durée cible :

| Classe | Traitement |
| --- | --- |
| respiration | conservée (énergie au-dessus du plancher de bruit, ou blanc très court) |
| pause de fin de phrase | on en garde une bonne partie : elle porte du sens |
| pause de virgule | resserrée |
| hésitation | resserrée à la durée cible du style |
| pause trop longue | coupée franchement, mais jamais à zéro |
| silence de mauvaise prise | réduit aux seules marges |

Une marge est conservée avant et après chaque plan pour ne pas couper l'attaque
ni la chute d'un mot. Cette marge est bornée par le silence réellement
disponible : on n'entend jamais le début du « euh » supprimé.

## Placement des zooms

Chaque plan est noté : montée d'énergie à l'attaque, changement d'idée
(« mais », « donc », « finalement »), mot d'insistance, début de phrase, plan
long et statique. Les plans courts, muets ou issus d'une hésitation sont
pénalisés.

Les meilleurs plans sont retenus, avec un écart minimal entre deux zooms et un
quota par minute — les deux pilotés par l'intensité. Un zoom est ensuite forcé
là où le montage resterait statique trop longtemps. Enfin les types sont
attribués en évitant deux fois le même d'affilée.

### Les trois courbes

`zoom/curves.py`

- **direct** — échelle fixe sur le plan, donc saut instantané au point de coupe.
- **progressif** — presque linéaire, extrémités adoucies. Aucun quart du plan ne
  concentre plus de 40 % du mouvement, aucun n'en fait moins de 12 % : le zoom est
  visible pendant tout le plan.
- **lancé** — deux temps. Le *lancer* monte très vite en décélérant et dépasse la
  cible de 11 % de l'amplitude ; l'*amortissement* revient tranquillement dessus.
  58 % du mouvement est fait dans le premier dixième de la durée, le sommet est
  atteint vers 55 %, l'arrivée est douce. Durée typique : 1 s.

## Fidélité entre la preview et Premiere

Le risque est que la preview et la timeline Premiere divergent. Deux mesures
l'écartent :

1. **Les bornes des plans sont arrondies à l'image une seule fois**
   (`editing/frames.py`), et les deux exports partent de ces mêmes valeurs.
2. **Les zooms sont rendus depuis les mêmes keyframes**, interpolées linéairement
   dans les deux cas. L'expression ffmpeg reproduit l'interpolation de Premiere à
   5 × 10⁻⁷ près.

Le son de la preview est monté en Python, échantillon par échantillon, avec un
micro-fondu à chaque raccord. Il ne peut donc pas se désynchroniser de l'image :
sur un rush de 91 s découpé en 16 plans, l'écart mesuré entre les durées vidéo et
audio est nul.

## Tests

`python -m pytest` exécute 223 tests. Les plus importants :

- `test_disfluency.py`, `test_retakes.py`, `test_fragments.py` — chaque exemple
  du cahier des charges, et les cas où il ne faut **rien** supprimer ;
- `test_export_premiere.py` — structure du XML, keyframes dans la plage du plan,
  timeline contiguë, échappement des caractères ;
- `test_zoom_render.py` — un rush contenant un carré blanc de taille connue est
  rendu, puis la largeur du carré est mesurée image par image. C'est la preuve
  que les zooms se voient vraiment, et qu'ils suivent la courbe écrite dans le
  XML à moins de 2 points d'échelle près ;
- `test_integration.py` — pipeline complet sur une vraie vidéo, jusqu'à la
  vérification du nombre d'images de la preview.
