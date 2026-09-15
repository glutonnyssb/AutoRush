# Import dans Adobe Premiere Pro

## Marche à suivre

1. Dans Premiere : `Fichier ▸ Importer…`
2. Choisissez `<nom_du_rush>_premiere.xml`
3. Premiere crée une séquence nommée `<nom du rush> - AutoRush` dans un
   chapiteau du même nom.
4. Ouvrez la séquence. Vous trouvez :
   - une piste vidéo avec un clip par plan, aux timecodes exacts ;
   - une ou deux pistes audio liées aux clips vidéo ;
   - un fondu enchaîné audio à chaque point de coupe ;
   - les zooms sur les plans concernés.

Le rush d'origine n'est pas copié : le XML pointe vers son chemin. Si vous
déplacez la vidéo après l'export, Premiere demandera où elle se trouve.

## Où voir les zooms

Sélectionnez un clip qui porte un zoom, puis ouvrez
`Options d'effet` (`Effect Controls`).

Sous **Mouvement ▸ Échelle** (`Motion ▸ Scale`) :

- **Zoom direct** — une valeur fixe, par exemple `112`. Le changement est donc
  instantané au point de coupe, puisque le plan précédent est à `100`.
- **Zoom progressif** — des keyframes du début à la fin du plan. La valeur monte
  régulièrement, par exemple de `100` à `112` sur toute la durée du plan.
- **Zoom lancé** — une grappe de keyframes serrées sur la première seconde, puis
  une keyframe de maintien jusqu'à la fin du plan. La courbe part vite, dépasse
  légèrement la cible, puis se repose dessus.

Toutes ces keyframes sont modifiables : déplacez-les, changez leurs valeurs,
supprimez-les. Rien n'est verrouillé.

## Pourquoi autant de keyframes sur un zoom lancé

Premiere interpole **linéairement** entre deux keyframes importées. Une courbe
d'inertie ne peut donc pas être décrite par deux points. AutoRush échantillonne
la courbe puis retire les keyframes inutiles : il n'en reste qu'une dizaine,
placées là où la courbe s'infléchit. Le rendu est fidèle à moins de 0,3 point
d'échelle, et la liste reste lisible.

Si vous préférez moins de keyframes, baissez `zoom.max_keyframes` ou augmentez
`zoom.keyframe_tolerance` dans un fichier de réglages
(`py -m autorush --save-settings reglages.json`, éditez, puis
`py -m autorush rush.mp4 --settings reglages.json`).

## Si les zooms apparaissent figés

C'est le seul point où les implémentations du format XMEML divergent. Le champ
`<when>` d'une keyframe se compte dans la base de temps du clip, et deux
conventions existent :

| Convention | Signification |
| --- | --- |
| `source` (défaut) | `when` est un numéro d'image **du rush**. Les keyframes restent collées aux images ; c'est le comportement de Premiere. |
| `clip` | `when` est compté depuis le début du plan. |

AutoRush écrit les deux fichiers :

```
mon_rush_premiere.xml                      <- convention "source"
mon_rush_premiere_keyframes-clip.xml       <- convention "clip"
```

Si après import du premier fichier l'échelle reste constante alors que le
rapport annonce des zooms animés, **importez le second fichier**. Les cuts et
l'audio sont identiques ; seule la base de temps des keyframes change.

Pour figer votre choix :

```powershell
py -m autorush rush.mp4 --keyframe-time-base clip --no-alternate-xml
```

## Fondus audio

Un fondu enchaîné (`Cross Fade`) de 3 images est posé au centre de chaque point
de coupe, quand le rush offre assez de matière de part et d'autre. Sinon,
AutoRush applique à la place un micro-fondu de niveau sur le clip, qui joue le
même rôle : supprimer le claquement au raccord.

Pour désactiver :

```powershell
py -m autorush rush.mp4 --no-crossfade
```

## Cadence et format

La séquence reprend la cadence et les dimensions du rush, y compris les cadences
NTSC (`23,976`, `29,97`, `59,94`), écrites avec la base de temps entière et le
drapeau `ntsc` attendu par Premiere.

Pour forcer une autre cadence :

```powershell
py -m autorush rush.mp4 --fps 25
```

## Le fichier EDL

`mon_rush.edl` ne contient que les points de coupe, en timecode SMPTE. Il ne
transporte aucun effet. Il sert de secours universel : Premiere, DaVinci
Resolve, Avid et Final Cut le lisent tous. Pratique aussi pour vérifier un
timecode à la main.

## Le fichier JSON

`mon_rush_montage.json` contient l'intégralité du montage et des décisions :
plans, zooms avec leurs courbes, suppressions avec leur justification et leur
confiance, signalements, raccords suspects, réglages utilisés. C'est le format à
lire si vous voulez brancher un autre outil sur AutoRush.
